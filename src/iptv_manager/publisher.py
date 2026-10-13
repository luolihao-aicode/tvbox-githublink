from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from .models import SourceType, StreamCandidate
from .scoring import ChannelSelection
from .storage import atomic_write_text


class OutputValidationError(ValueError):
    pass


class PublishMode(StrEnum):
    OFFICIAL = "official"
    COMMUNITY = "community"
    COMBINED = "combined"
    BACKUP = "backup"


def _safe_attribute(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace('"', "'").strip()


def _lines_for_mode(
    selection: ChannelSelection,
    mode: PublishMode,
) -> list[tuple[StreamCandidate, str]]:
    lines = [selection.primary, *selection.backups]
    if mode is PublishMode.COMBINED:
        return [(selection.primary, selection.primary.channel_name or selection.channel_id)]
    if mode is PublishMode.OFFICIAL:
        matches = [item for item in lines if item.source_type is SourceType.OFFICIAL]
        return [(matches[0], matches[0].channel_name or selection.channel_id)] if matches else []
    if mode is PublishMode.COMMUNITY:
        matches = [item for item in lines if item.source_type is SourceType.COMMUNITY]
        return [(matches[0], matches[0].channel_name or selection.channel_id)] if matches else []
    result: list[tuple[StreamCandidate, str]] = []
    for index, item in enumerate(lines):
        name = item.channel_name or selection.channel_id
        if index:
            name = f"{name} · 备用{index}"
        result.append((item, name))
    return result


def render_m3u(
    selections: Sequence[ChannelSelection],
    mode: PublishMode,
) -> str:
    output = ["#EXTM3U"]
    for selection in selections:
        for stream, display_name in _lines_for_mode(selection, mode):
            channel_id = _safe_attribute(stream.channel_id or selection.channel_id)
            channel_name = _safe_attribute(stream.channel_name or display_name)
            group = _safe_attribute(stream.category.value if stream.category else "其他")
            safe_display = _safe_attribute(display_name).replace(",", "，")
            output.append(
                f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{channel_name}" '
                f'group-title="{group}",{safe_display}'
            )
            output.append(str(stream.url))
    return "\n".join(output) + "\n"


def build_status(
    selections: Sequence[ChannelSelection],
    checked_at: datetime,
    measurement_source: str = 'GitHub Actions 节点',
) -> dict[str, object]:
    channels: list[dict[str, object]] = []
    for selection in selections:
        stream = selection.primary
        latest = stream.history[-1] if stream.history else None
        successful = sum(item.success for item in stream.history)
        availability = successful / len(stream.history) * 100 if stream.history else 0.0
        channels.append(
            {
                "channel_id": selection.channel_id,
                "name": stream.channel_name,
                "category": stream.category.value if stream.category else None,
                "source_type": stream.source_type.value,
                "protocol": stream.protocol,
                "height": latest.height if latest else None,
                "video_codec": latest.video_codec if latest else None,
                "latency_seconds": latest.first_byte_seconds if latest else None,
                "availability_percent": round(availability, 2),
                "score": stream.score,
                "status": stream.status.value,
                "last_checked_at": latest.checked_at.isoformat() if latest else None,
            }
        )
    return {
        "schema_version": 1,
        "checked_at": checked_at.isoformat(),
        "measurement_notice": f"延迟和速度由{measurement_source}测得，不代表用户本地网络体验。",
        "channels": channels,
    }


def publish_outputs(
    selections: Sequence[ChannelSelection],
    output_dir: Path,
    checked_at: datetime,
    measurement_source: str = 'GitHub Actions 节点',
) -> None:
    if not selections:
        raise OutputValidationError("没有可发布频道，已保留上一版订阅")

    rendered = {
        "official.m3u": render_m3u(selections, PublishMode.OFFICIAL),
        "community.m3u": render_m3u(selections, PublishMode.COMMUNITY),
        "combined.m3u": render_m3u(selections, PublishMode.COMBINED),
        "backup.m3u": render_m3u(selections, PublishMode.BACKUP),
        "status.json": json.dumps(
            build_status(selections, checked_at, measurement_source), ensure_ascii=False, indent=2
        )
        + "\n",
        "report.json": json.dumps(
            {
                "schema_version": 1,
                "checked_at": checked_at.isoformat(),
                "published_channels": len(selections),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    if rendered["combined.m3u"].count("#EXTINF:") == 0:
        raise OutputValidationError("综合订阅为空，已保留上一版订阅")
    for filename, content in rendered.items():
        atomic_write_text(output_dir / filename, content)
