from __future__ import annotations

import json
import os
import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .classify import classify_channel
from .config import load_config
from .discovery import DiscoveryResult, fetch_seed_sources, search_github
from .models import ChannelCategory, CheckResult, PolicyConfig, StreamCandidate, StreamStatus
from .probe_http import probe_many_http
from .probe_media import probe_media
from .publisher import publish_outputs
from .scoring import (
    ChannelSelection,
    append_history,
    calculate_score,
    select_lines,
    update_lifecycle,
)
from .storage import atomic_write_text

DiscoverFn = Callable[[], Awaitable[DiscoveryResult]]
HttpProbeFn = Callable[[list[StreamCandidate], PolicyConfig], Awaitable[list[CheckResult]]]
MediaProbeFn = Callable[[StreamCandidate, PolicyConfig], Awaitable[CheckResult]]


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    config_dir: Path
    data_dir: Path
    output_dir: Path
    dry_run: bool = False
    discover_github: bool = False
    github_token: str = ""
    categories: tuple[ChannelCategory, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineDependencies:
    discover: DiscoverFn
    probe_http: HttpProbeFn
    probe_media: MediaProbeFn
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass(slots=True)
class PipelineResult:
    candidates: list[StreamCandidate] = field(default_factory=list)
    selections: list[ChannelSelection] = field(default_factory=list)
    issues: list[object] = field(default_factory=list)
    report: dict[str, object] = field(default_factory=dict)


def _load_candidates(path: Path) -> list[StreamCandidate]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("候选源文件顶层必须是数组")
        return [StreamCandidate.model_validate(item) for item in payload]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"无法读取候选源历史：{exc}") from exc


def _merge_check(http_result: CheckResult, media_result: CheckResult) -> CheckResult:
    if not http_result.success:
        return http_result
    if not media_result.success:
        return media_result.model_copy(
            update={
                "first_byte_seconds": http_result.first_byte_seconds,
                "transfer_bytes_per_second": http_result.transfer_bytes_per_second,
            }
        )
    return media_result.model_copy(
        update={
            "first_byte_seconds": http_result.first_byte_seconds,
            "transfer_bytes_per_second": http_result.transfer_bytes_per_second,
            "success": True,
        }
    )


async def _default_dependencies(options: PipelineOptions, config) -> PipelineDependencies:
    async def discover() -> DiscoveryResult:
        timeout = httpx.Timeout(config.policy.http_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await fetch_seed_sources(config, client)
            if options.discover_github and options.github_token:
                github = await search_github(config, options.github_token, client)
                result.candidates.extend(github.candidates)
                result.issues.extend(github.issues)
            return result

    async def http_probe(candidates, policy):
        return await probe_many_http(candidates, policy)

    async def media_probe(candidate, policy):
        return await probe_media(candidate, policy)

    return PipelineDependencies(discover, http_probe, media_probe)


async def run_pipeline(
    options: PipelineOptions,
    dependencies: PipelineDependencies | None = None,
) -> PipelineResult:
    config = load_config(options.config_dir)
    deps = dependencies or await _default_dependencies(options, config)
    now = deps.now()
    discovery = await deps.discover()
    existing = {
        item.stream_id: item
        for item in _load_candidates(options.data_dir / "candidates.json")
    }
    for discovered in discovery.candidates:
        previous = existing.get(discovered.stream_id)
        if previous is None:
            existing[discovered.stream_id] = discovered
        else:
            existing[discovered.stream_id] = previous.model_copy(
                update={
                    "raw_name": discovered.raw_name,
                    "url": discovered.url,
                    "source_url": discovered.source_url,
                    "source_type": discovered.source_type,
                }
            )

    candidates: list[StreamCandidate] = []
    unknown_names: list[str] = []
    for stream in existing.values():
        match = classify_channel(stream.raw_name, config.channels)
        if match.channel_id is None:
            unknown_names.append(stream.raw_name)
            continue
        candidates.append(
            stream.model_copy(
                update={
                    "channel_id": match.channel_id,
                    "channel_name": match.channel_name,
                    "category": match.category,
                }
            )
        )
    candidates.sort(key=lambda item: item.stream_id)
    to_check = [item for item in candidates if not options.categories or item.category in options.categories]
    untouched = [item for item in candidates if options.categories and item.category not in options.categories]

    http_results = await deps.probe_http(to_check, config.policy)
    if len(http_results) != len(to_check):
        raise ValueError("HTTP 检测结果数量与候选源数量不一致")

    media_limit = asyncio.Semaphore(config.policy.media_concurrency)
    media_hosts: dict[str, asyncio.Semaphore] = {}

    async def check_one(stream: StreamCandidate, http_result: CheckResult) -> StreamCandidate:
        if http_result.success:
            host = urlsplit(str(stream.url)).hostname or ''
            host_limit = media_hosts.setdefault(host, asyncio.Semaphore(config.policy.per_host_concurrency))
            async with media_limit, host_limit:
                media_result = await deps.probe_media(stream, config.policy)
            result = _merge_check(http_result, media_result)
        else:
            result = http_result
        history = list(append_history(stream.history, result, config.policy.history_limit))
        changes: dict[str, object] = {
            "history": history,
            "last_checked_at": result.checked_at,
        }
        if result.success:
            changes["last_success_at"] = result.checked_at
        checked = stream.model_copy(update=changes)
        checked.score = calculate_score(checked.history, config.policy)
        return update_lifecycle(checked, now, config.policy)

    checked_items = await asyncio.gather(*(check_one(stream, check) for stream, check
                                          in zip(to_check, http_results, strict=True)))
    updated = [*untouched, *checked_items]

    selections = select_lines(updated, config.policy)
    selected_states = {line.stream_id: line.status for choice in selections
                       for line in (choice.primary, *choice.backups)}
    for stream in updated:
        if stream.stream_id in selected_states:
            stream.status = selected_states[stream.stream_id]
        elif stream.status in {StreamStatus.ACTIVE, StreamStatus.BACKUP}:
            stream.status = StreamStatus.CANDIDATE
    reasons: Counter[str] = Counter()
    for stream in updated:
        if (options.categories and stream.category not in options.categories) or not stream.history:
            continue
        latest = stream.history[-1]
        if not latest.success:
            reasons[latest.failure_code or 'PROBE_FAILED'] += 1
        elif not latest.continuous_read:
            reasons['INSUFFICIENT_MEDIA_DURATION'] += 1
        elif (latest.height or 0) < config.policy.minimum_height:
            reasons['BELOW_HD_THRESHOLD'] += 1
        elif len(stream.history) < 2 or not all(item.success and item.continuous_read for item in stream.history[-2:]):
            reasons['WAITING_SECOND_SUCCESS'] += 1
    report = {
        'schema_version': 1,
        'checked_at': now.isoformat(),
        'discovered_sources': len(discovery.candidates),
        'matched_sources': len(candidates),
        'checked_sources': len(to_check),
        'successful_sources': sum(stream.history[-1].success for stream in updated
                                  if stream.history and (not options.categories or stream.category in options.categories)),
        'published_channels': len(selections),
        'filter_reasons': dict(reasons),
        'unknown_channel_names': sorted(set(unknown_names))[:100],
        'issues': [{'source_url': issue.source_url, 'code': issue.code}
                   for issue in discovery.issues],
        'message': '检测完成' if selections else '暂无合格频道，请查看发现错误和筛选原因。',
    }
    if not options.dry_run:
        options.data_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            options.data_dir / "candidates.json",
            json.dumps(
                [item.model_dump(mode="json") for item in updated],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        if selections:
            source = 'GitHub Actions 节点' if os.environ.get('GITHUB_ACTIONS') == 'true' else '本次本地检测节点'
            publish_outputs(selections, options.output_dir, now, source)
        atomic_write_text(options.output_dir / 'report.json',
                          json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return PipelineResult(updated, selections, list(discovery.issues), report)
