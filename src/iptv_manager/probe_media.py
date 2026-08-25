from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .models import CheckResult, PolicyConfig, StreamCandidate

PROCESS_OUTPUT_LIMIT = 1_000_000


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    async def run(self, args: list[str], timeout_seconds: float) -> ProcessOutput: ...


class SubprocessRunner:
    async def run(self, args: list[str], timeout_seconds: float) -> ProcessOutput:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if len(stdout) > PROCESS_OUTPUT_LIMIT or len(stderr) > PROCESS_OUTPUT_LIMIT:
            raise ValueError("ffprobe 输出超过大小限制")
        return ProcessOutput(
            returncode=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


def build_ffprobe_args(url: str, policy: PolicyConfig) -> list[str]:
    network_timeout = int(policy.http_timeout_seconds * 1_000_000)
    return [
        "ffprobe",
        "-v",
        "error",
        "-rw_timeout",
        str(network_timeout),
        "-read_intervals",
        f"%+{max(1, int(policy.media_probe_seconds))}",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        url,
    ]


def _as_nonnegative_int(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


async def probe_media(
    candidate: StreamCandidate,
    policy: PolicyConfig,
    runner: ProcessRunner | None = None,
) -> CheckResult:
    checked_at = datetime.now(UTC)
    active_runner = runner or SubprocessRunner()
    try:
        output = await active_runner.run(
            build_ffprobe_args(str(candidate.url), policy),
            timeout_seconds=policy.media_probe_seconds + policy.http_timeout_seconds,
        )
    except TimeoutError:
        return CheckResult(
            checked_at=checked_at, success=False, failure_code="MEDIA_TIMEOUT"
        )
    except (OSError, ValueError):
        return CheckResult(
            checked_at=checked_at, success=False, failure_code="FFPROBE_FAILED"
        )

    if output.returncode != 0:
        return CheckResult(
            checked_at=checked_at, success=False, failure_code="FFPROBE_FAILED"
        )
    try:
        payload = json.loads(output.stdout)
        streams = payload["streams"]
        if not isinstance(streams, list):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError):
        return CheckResult(
            checked_at=checked_at, success=False, failure_code="INVALID_MEDIA_JSON"
        )

    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        None,
    )
    if video is None:
        return CheckResult(
            checked_at=checked_at, success=False, failure_code="NO_VIDEO_STREAM"
        )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        None,
    )
    format_data = payload.get("format") if isinstance(payload, dict) else None
    stream_bitrate = _as_nonnegative_int(video.get("bit_rate"))
    format_bitrate = (
        _as_nonnegative_int(format_data.get("bit_rate"))
        if isinstance(format_data, dict)
        else None
    )
    return CheckResult(
        checked_at=checked_at,
        success=True,
        width=_as_nonnegative_int(video.get("width")),
        height=_as_nonnegative_int(video.get("height")),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        bitrate=stream_bitrate or format_bitrate,
        continuous_read=True,
    )
