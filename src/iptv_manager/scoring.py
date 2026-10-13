from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .models import CheckResult, PolicyConfig, StreamCandidate, StreamStatus


@dataclass(frozen=True, slots=True)
class ChannelSelection:
    channel_id: str
    primary: StreamCandidate
    backups: tuple[StreamCandidate, ...]


def append_history(
    history: Sequence[CheckResult],
    result: CheckResult,
    limit: int = 30,
) -> tuple[CheckResult, ...]:
    if limit < 1:
        raise ValueError("历史记录上限必须大于零")
    return tuple((*history, result)[-limit:])


def _speed_score(seconds: float | None) -> float:
    if seconds is None or seconds > 5:
        return 0.0
    if seconds <= 1:
        return 1.0
    if seconds <= 2:
        return 0.85
    if seconds <= 3:
        return 0.70
    return 0.50


def _resolution_score(height: int | None) -> float:
    if height is None or height < 540:
        return 0.0
    if height >= 2160:
        return 1.0
    if height >= 1080:
        return 0.90
    if height >= 720:
        return 0.70
    return 0.45


def _bitrate_score(bitrate: int | None, height: int | None) -> float:
    if bitrate is None or height is None:
        return 0.5
    target = 8_000_000 if height >= 2160 else 4_000_000 if height >= 1080 else 2_000_000
    return min(1.0, bitrate / target)


def calculate_score(
    history: Sequence[CheckResult],
    policy: PolicyConfig,
) -> float:
    recent = list(history[-policy.history_limit :])
    if not recent:
        return 0.0
    availability = sum(item.success for item in recent) / len(recent)
    successful = [item for item in recent if item.success]
    if successful:
        speed = sum(_speed_score(item.first_byte_seconds) for item in successful) / len(successful)
        quality = sum(
            _resolution_score(item.height) * 0.8
            + _bitrate_score(item.bitrate, item.height) * 0.2
            for item in successful
        ) / len(successful)
    else:
        speed = 0.0
        quality = 0.0
    stability = sum(item.success and item.continuous_read for item in recent) / len(recent)
    score = availability * 40 + speed * 25 + quality * 20 + stability * 15
    return round(max(0.0, min(100.0, score)), 2)


def update_lifecycle(
    stream: StreamCandidate,
    now: datetime,
    policy: PolicyConfig,
) -> StreamCandidate:
    del policy
    history = stream.history
    latest_success = max(
        (item.checked_at for item in history if item.success),
        default=stream.last_success_at,
    )
    reference = latest_success or stream.discovered_at
    if now - reference >= timedelta(days=30):
        return stream.model_copy(update={"status": StreamStatus.RETIRED})

    failures = 0
    for item in reversed(history):
        if item.success:
            break
        failures += 1
    if failures >= 2:
        return stream.model_copy(update={"status": StreamStatus.COOLING})
    if failures == 0 and history and stream.status in {StreamStatus.COOLING, StreamStatus.RETIRED}:
        return stream.model_copy(update={'status': StreamStatus.CANDIDATE})
    return stream.model_copy()


def _is_publishable(stream: StreamCandidate, policy: PolicyConfig) -> bool:
    if stream.status in {StreamStatus.COOLING, StreamStatus.RETIRED}:
        return False
    if stream.channel_id is None or len(stream.history) < 2:
        return False
    latest = stream.history[-2:]
    if not all(item.success and item.continuous_read for item in latest):
        return False
    last = latest[-1]
    minimum_height = policy.minimum_height
    return (
        last.height is not None
        and last.height >= minimum_height
        and last.first_byte_seconds is not None
        and last.first_byte_seconds <= policy.http_timeout_seconds
    )


def select_lines(
    streams: Sequence[StreamCandidate],
    policy: PolicyConfig,
) -> list[ChannelSelection]:
    grouped: dict[str, list[StreamCandidate]] = defaultdict(list)
    for stream in streams:
        if _is_publishable(stream, policy) and stream.channel_id:
            grouped[stream.channel_id].append(stream)

    selections: list[ChannelSelection] = []
    for channel_id in sorted(grouped):
        ranked = sorted(
            grouped[channel_id],
            key=lambda item: (-item.score, item.stream_id),
        )
        current = next(
            (item for item in ranked if item.status is StreamStatus.ACTIVE), None
        )
        best = ranked[0]
        if current is not None and best.score < current.score + policy.switch_margin:
            best = current
        backups = [item for item in ranked if item.stream_id != best.stream_id][:2]
        primary = best.model_copy(update={"status": StreamStatus.ACTIVE})
        backup_copies = tuple(
            item.model_copy(update={"status": StreamStatus.BACKUP}) for item in backups
        )
        selections.append(ChannelSelection(channel_id, primary, backup_copies))
    return selections
