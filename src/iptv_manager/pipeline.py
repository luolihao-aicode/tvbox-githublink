from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .classify import classify_channel
from .config import load_config
from .discovery import DiscoveryResult, fetch_seed_sources, search_github
from .models import CheckResult, PolicyConfig, StreamCandidate
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
    for stream in existing.values():
        match = classify_channel(stream.raw_name, config.channels)
        if match.channel_id is None:
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

    http_results = await deps.probe_http(candidates, config.policy)
    if len(http_results) != len(candidates):
        raise ValueError("HTTP 检测结果数量与候选源数量不一致")

    updated: list[StreamCandidate] = []
    for stream, http_result in zip(candidates, http_results, strict=True):
        if http_result.success:
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
        updated.append(update_lifecycle(checked, now, config.policy))

    selections = select_lines(updated, config.policy)
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
            publish_outputs(selections, options.output_dir, now)
    return PipelineResult(updated, selections, list(discovery.issues))
