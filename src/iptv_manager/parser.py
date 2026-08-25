from __future__ import annotations

from datetime import UTC, datetime

from .models import SourceType, StreamCandidate
from .normalize import make_stream_id, normalize_url

MAX_PLAYLIST_CHARS = 5_000_000
MAX_LINE_CHARS = 16_384


def parse_m3u(
    text: str,
    source_url: str,
    source_type: SourceType,
) -> list[StreamCandidate]:
    if len(text) > MAX_PLAYLIST_CHARS:
        raise ValueError("播放列表超过允许大小")

    candidates: list[StreamCandidate] = []
    pending_name: str | None = None
    discovered_at = datetime.now(UTC)

    for raw_line in text.lstrip("\ufeff").splitlines():
        if len(raw_line) > MAX_LINE_CHARS:
            pending_name = None
            continue
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            _, separator, display_name = line.partition(",")
            pending_name = display_name.strip() if separator and display_name.strip() else None
            continue
        if line.startswith("#"):
            continue
        if pending_name is None:
            continue
        try:
            url = normalize_url(line)
            candidate = StreamCandidate(
                stream_id=make_stream_id(url),
                raw_name=pending_name,
                url=url,
                source_url=source_url,
                source_type=source_type,
                discovered_at=discovered_at,
            )
        except ValueError:
            pending_name = None
            continue
        candidates.append(candidate)
        pending_name = None

    return candidates
