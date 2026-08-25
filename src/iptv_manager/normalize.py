from __future__ import annotations

import hashlib
from collections.abc import Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .models import StreamCandidate


def normalize_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("播放地址只允许 HTTP 或 HTTPS 协议")
    if not parts.hostname:
        raise ValueError("播放地址缺少主机名")

    hostname = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("播放地址端口无效") from exc
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    normalized = SplitResult(
        scheme=scheme,
        netloc=hostname,
        path=parts.path or "/",
        query=parts.query,
        fragment="",
    )
    return urlunsplit(normalized)


def make_stream_id(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def deduplicate_candidates(
    items: Iterable[StreamCandidate],
) -> list[StreamCandidate]:
    result: list[StreamCandidate] = []
    seen: set[str] = set()
    for item in items:
        key = normalize_url(str(item.url))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

