import json

import pytest

from iptv_manager.models import PolicyConfig
from iptv_manager.probe_media import ProcessOutput, build_ffprobe_args, probe_media


class FakeRunner:
    def __init__(self, output: ProcessOutput) -> None:
        self.output = output

    async def run(self, args: list[str], timeout_seconds: float) -> ProcessOutput:
        return self.output


@pytest.mark.asyncio
async def test_audio_only_stream_fails_media_verification(candidate_factory) -> None:
    candidate = candidate_factory("https://stream.example/audio.m3u8", "演示频道")
    runner = FakeRunner(
        ProcessOutput(
            returncode=0,
            stdout=json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac"}]}),
            stderr="",
        )
    )

    result = await probe_media(candidate, PolicyConfig(), runner)

    assert result.success is False
    assert result.failure_code == "NO_VIDEO_STREAM"


@pytest.mark.asyncio
async def test_video_metadata_is_collected(candidate_factory) -> None:
    candidate = candidate_factory("https://stream.example/hd.m3u8", "演示频道")
    runner = FakeRunner(
        ProcessOutput(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1920,
                            "height": 1080,
                            "bit_rate": "4500000",
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ]
                }
            ),
            stderr="",
        )
    )

    result = await probe_media(candidate, PolicyConfig(), runner)

    assert result.success is True
    assert (result.width, result.height, result.video_codec) == (1920, 1080, "h264")
    assert result.audio_codec == "aac"
    assert result.bitrate == 4_500_000


def test_ffprobe_url_is_passed_as_a_single_argument() -> None:
    url = "https://stream.example/live.m3u8?name=a b&token=value"
    args = build_ffprobe_args(url, PolicyConfig())

    assert args[-1] == url
    assert args[0] == "ffprobe"


@pytest.mark.asyncio
async def test_video_headers_alone_do_not_prove_continuous_playback(candidate_factory):
    candidate = candidate_factory('https://stream.example/live.m3u8', '频道')
    runner = FakeRunner(ProcessOutput(0, json.dumps({'streams': [
        {'index': 0, 'codec_type': 'video', 'height': 1080, 'width': 1920}
    ]}), ''))
    result = await probe_media(candidate, PolicyConfig(), runner)
    assert result.continuous_read is False


@pytest.mark.asyncio
async def test_video_packet_timestamps_prove_eight_seconds_of_media(candidate_factory):
    candidate = candidate_factory('https://stream.example/live.m3u8', '频道')
    payload = {'streams': [{'index': 0, 'codec_type': 'video', 'height': 1080}],
               'packets': [{'stream_index': 0, 'pts_time': str(t)} for t in (100, 104, 108)]}
    runner = FakeRunner(ProcessOutput(0, json.dumps(payload), ''))
    result = await probe_media(candidate, PolicyConfig(), runner)
    assert result.continuous_read is True
    assert '-show_packets' in build_ffprobe_args(str(candidate.url), PolicyConfig())
