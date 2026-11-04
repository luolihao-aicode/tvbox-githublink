from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
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
        f"%+{policy.media_probe_seconds + 0.5}",
        "-show_packets",
        "-show_streams",
        "-show_format",
        "-show_entries",
        "packet=stream_index,pts_time,size:stream=index,codec_type,codec_name,width,height,bit_rate,avg_frame_rate:format=bit_rate",
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
    timestamps: list[float] = []
    video_bytes = 0
    sized_packets = 0
    for packet in payload.get('packets', []):
        if not isinstance(packet, dict) or packet.get('stream_index') != video.get('index'):
            continue
        try:
            stamp = float(packet.get('pts_time', ''))
        except (ValueError, TypeError):
            continue
        if math.isfinite(stamp):
            timestamps.append(stamp)
            size = _as_nonnegative_int(packet.get('size'))
            if size is not None:
                video_bytes += size
                sized_packets += 1
    continuous = len(timestamps) >= 2 and max(timestamps) - min(timestamps) >= policy.media_probe_seconds
    span = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else 0
    measured_bitrate = round(video_bytes * 8 / span) if span > 0 and sized_packets == len(timestamps) else None
    try:
        rate = float(Fraction(str(video.get('avg_frame_rate', '0/0'))))
        frame_rate = rate if math.isfinite(rate) and rate > 0 else None
    except (ValueError, ZeroDivisionError, OverflowError):
        frame_rate = None
    return CheckResult(
        checked_at=checked_at,
        success=True,
        width=_as_nonnegative_int(video.get("width")),
        height=_as_nonnegative_int(video.get("height")),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        bitrate=stream_bitrate or measured_bitrate or format_bitrate,
        frame_rate=frame_rate,
        continuous_read=continuous,
    )
