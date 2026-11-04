"""检查媒体清单是否持续推进，避免把可解码历史片段当作直播。"""
from datetime import UTC, datetime
import re
from urllib.parse import urlsplit


def verify_program_time(text: str, *, now: datetime | None = None) -> None:
    stamps = [line.split(':', 1)[1].strip() for line in text.splitlines()
              if line.startswith('#EXT-X-PROGRAM-DATE-TIME:')]
    if not stamps:
        return
    now = now or datetime.now(UTC)
    try:
        stamp = datetime.fromisoformat(stamps[-1].replace('Z', '+00:00'))
        if stamp.tzinfo is None or abs((now - stamp).total_seconds()) > 86400:
            raise ValueError('STALE_HLS_PROGRAM_TIME')
    except (TypeError, OverflowError) as exc:
        raise ValueError('INVALID_HLS_PROGRAM_TIME') from exc


def verify_live_progress(previous: str, current: str, *, now: datetime | None = None) -> None:
    verify_program_time(current, now=now)
    if not current.lstrip().startswith('#EXTM3U') or '#EXT-X-ENDLIST' in current or '#EXT-X-PLAYLIST-TYPE:VOD' in current:
        raise ValueError('NOT_LIVE')
    def segments(text):
        result = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                parsed = urlsplit(line)
                result.append(parsed.path.lstrip('/'))
        return result
    before, after = segments(previous), segments(current)
    if not before or not after or before == after or after[-1] in before:
        raise ValueError('FROZEN_HLS_PLAYLIST')
    before_seq = re.search(r'^#EXT-X-MEDIA-SEQUENCE:(\d+)\s*$', previous, re.M)
    after_seq = re.search(r'^#EXT-X-MEDIA-SEQUENCE:(\d+)\s*$', current, re.M)
    if not before_seq or not after_seq:
        raise ValueError('LIVE_FRESHNESS_UNCONFIRMED')
    if int(after_seq[1]) <= int(before_seq[1]):
        raise ValueError('FROZEN_OR_REWOUND_HLS_PLAYLIST')
    common = [before.index(path) for path in after if path in before]
    if common != sorted(common):
        raise ValueError('REORDERED_HLS_PLAYLIST')
