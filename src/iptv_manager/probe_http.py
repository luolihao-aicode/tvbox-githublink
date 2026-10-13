from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .models import CheckResult, PolicyConfig, StreamCandidate
from .normalize import normalize_url

PLAYLIST_LIMIT = 512_000
STREAM_SAMPLE_LIMIT = 256_000
_PLACEHOLDER_PATH = re.compile(r'(?:^|/)(?:nosignal|no_signal|no-signal|placeholder)(?:[_.-]|/|$)', re.IGNORECASE)


class ProbeRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _reject_placeholder(url: str) -> None:
    if _PLACEHOLDER_PATH.search(unquote(urlsplit(url).path)):
        raise ProbeRejected('PLACEHOLDER_STREAM')


@dataclass(frozen=True, slots=True)
class _Sample:
    body: bytes
    content_type: str
    first_byte_seconds: float
    elapsed_seconds: float
    url: str = ''


async def _fetch_sample(
    client: httpx.AsyncClient,
    url: str,
    limit: int,
) -> _Sample:
    _reject_placeholder(url)
    started = perf_counter()
    first_byte: float | None = None
    body = bytearray()
    async with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        resolved_url = str(response.url)
        _reject_placeholder(resolved_url)
        async for chunk in response.aiter_bytes():
            if first_byte is None:
                first_byte = perf_counter() - started
            remaining = limit - len(body)
            body.extend(chunk[:remaining])
            if len(body) >= limit:
                break
        content_type = response.headers.get("content-type", "").lower()
    elapsed = max(perf_counter() - started, 1e-9)
    return _Sample(bytes(body), content_type, first_byte or elapsed, elapsed, resolved_url)


def _looks_like_html(sample: _Sample) -> bool:
    prefix = sample.body.lstrip()[:64].lower()
    return "text/html" in sample.content_type or prefix.startswith(
        (b"<!doctype html", b"<html")
    )


def _playlist_uri(text: str) -> str | None:
    for line in text.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            return value
    return None


async def _probe_hls(
    client: httpx.AsyncClient,
    playlist_url: str,
    sample: _Sample,
    depth: int = 0,
) -> tuple[bool, _Sample]:
    if depth > 2:
        return False, sample
    try:
        text = sample.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False, sample
    if not text.lstrip().startswith("#EXTM3U"):
        return False, sample
    tags = {line.strip().upper() for line in text.splitlines() if line.strip().startswith('#')}
    if '#EXT-X-ENDLIST' in tags or '#EXT-X-PLAYLIST-TYPE:VOD' in tags:
        raise ProbeRejected('NON_LIVE_PLAYLIST')
    uri = _playlist_uri(text)
    if uri is None:
        return False, sample
    child_url = normalize_url(urljoin(sample.url or playlist_url, uri))
    child = await _fetch_sample(client, child_url, STREAM_SAMPLE_LIMIT)
    if _looks_like_html(child) or not child.body:
        return False, child
    if child.body.lstrip().startswith(b"#EXTM3U"):
        return await _probe_hls(client, child_url, child, depth + 1)
    return True, child


async def probe_http(
    candidate: StreamCandidate,
    client: httpx.AsyncClient,
    policy: PolicyConfig,
) -> CheckResult:
    checked_at = datetime.now(UTC)
    if candidate.stream_id in policy.rejected_streams:
        return CheckResult(checked_at=checked_at, success=False, failure_code='MANUAL_REJECTION')
    try:
        sample = await _fetch_sample(client, str(candidate.url), PLAYLIST_LIMIT)
        if _looks_like_html(sample):
            return CheckResult(
                checked_at=checked_at,
                success=False,
                failure_code="HTML_RESPONSE",
                first_byte_seconds=sample.first_byte_seconds,
            )
        if not sample.body:
            return CheckResult(
                checked_at=checked_at,
                success=False,
                failure_code="EMPTY_RESPONSE",
                first_byte_seconds=sample.first_byte_seconds,
            )

        content_type = sample.content_type
        is_hls = (
            "mpegurl" in content_type
            or str(candidate.url).lower().split("?", 1)[0].endswith(".m3u8")
            or sample.body.lstrip().startswith(b"#EXTM3U")
        )
        measured = sample
        if is_hls:
            valid, measured = await _probe_hls(client, str(candidate.url), sample)
            if not valid:
                return CheckResult(
                    checked_at=checked_at,
                    success=False,
                    failure_code="INVALID_HLS",
                    first_byte_seconds=sample.first_byte_seconds,
                )

        speed = len(measured.body) / measured.elapsed_seconds
        return CheckResult(
            checked_at=checked_at,
            success=sample.first_byte_seconds <= policy.http_timeout_seconds,
            failure_code=(
                None
                if sample.first_byte_seconds <= policy.http_timeout_seconds
                else "FIRST_BYTE_TIMEOUT"
            ),
            first_byte_seconds=sample.first_byte_seconds,
            transfer_bytes_per_second=speed,
        )
    except ProbeRejected as exc:
        return CheckResult(checked_at=checked_at, success=False, failure_code=exc.code)
    except httpx.TimeoutException:
        return CheckResult(
            checked_at=checked_at,
            success=False,
            failure_code="HTTP_TIMEOUT",
        )
    except (httpx.HTTPError, ValueError):
        return CheckResult(
            checked_at=checked_at,
            success=False,
            failure_code="HTTP_FAILED",
        )


async def probe_many_http(
    candidates: list[StreamCandidate],
    policy: PolicyConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[CheckResult]:
    global_limit = asyncio.Semaphore(policy.global_concurrency)
    host_limits: dict[str, asyncio.Semaphore] = {}
    owned_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(policy.http_timeout_seconds),
        follow_redirects=True,
    )

    async def worker(candidate: StreamCandidate) -> CheckResult:
        host = urlsplit(str(candidate.url)).hostname or ""
        host_limit = host_limits.setdefault(
            host, asyncio.Semaphore(policy.per_host_concurrency)
        )
        async with global_limit, host_limit:
            return await probe_http(candidate, active_client, policy)

    try:
        return list(await asyncio.gather(*(worker(item) for item in candidates)))
    finally:
        if owned_client:
            await active_client.aclose()
