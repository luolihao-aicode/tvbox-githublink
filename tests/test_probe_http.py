import httpx
import pytest

from iptv_manager.models import PolicyConfig
from iptv_manager.probe_http import probe_http, probe_many_http


@pytest.mark.asyncio
async def test_html_login_page_is_not_treated_as_stream(candidate_factory) -> None:
    candidate = candidate_factory("https://stream.example/live", "CCTV-1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><title>请登录</title></html>",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, PolicyConfig())

    assert result.success is False
    assert result.failure_code == "HTML_RESPONSE"


@pytest.mark.asyncio
async def test_hls_media_playlist_and_segment_are_verified(candidate_factory) -> None:
    candidate = candidate_factory("https://stream.example/live.m3u8", "CCTV-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/live.m3u8":
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.apple.mpegurl"},
                text="#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n",
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "video/mp2t"},
            content=b"\x47" + b"\x00" * 187,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, PolicyConfig())

    assert result.success is True
    assert result.failure_code is None
    assert result.transfer_bytes_per_second is not None


@pytest.mark.asyncio
async def test_batch_probe_honors_per_host_concurrency(candidate_factory) -> None:
    active = 0
    maximum_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await __import__("asyncio").sleep(0.01)
        active -= 1
        return httpx.Response(
            200,
            headers={"content-type": "video/mp2t"},
            content=b"\x47" + b"\x00" * 187,
            request=request,
        )

    candidates = [
        candidate_factory(f"https://stream.example/{index}.ts", f"频道{index}")
        for index in range(3)
    ]
    policy = PolicyConfig(global_concurrency=3, per_host_concurrency=1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await probe_many_http(candidates, policy, client=client)

    assert all(result.success for result in results)
    assert maximum_active == 1


@pytest.mark.asyncio
async def test_master_playlist_pointing_to_no_signal_video_is_rejected(candidate_factory):
    candidate = candidate_factory('https://epg.example/stream/channel.m3u8', '八大综合台')
    async def handler(request):
        if request.url.host == 'epg.example':
            return httpx.Response(200, text='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080\nhttp://files.example/media/video/nosignal_h264/playlist.m3u8\n')
        if request.url.path.endswith('.m3u8'):
            return httpx.Response(200, text='#EXTM3U\n#EXTINF:6,\noutput001.ts\n')
        return httpx.Response(200, content=b'\x47' + b'\x00' * 187)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, PolicyConfig())
    assert result.success is False
    assert result.failure_code == 'PLACEHOLDER_STREAM'


@pytest.mark.asyncio
async def test_manually_rejected_stream_does_not_use_cached_success_or_fetch(candidate_factory):
    candidate = candidate_factory('https://stream.example/wrong.m3u8', '台视')
    policy = PolicyConfig(rejected_streams={candidate.stream_id: '人工确认画面错误或解码不稳'})
    def handler(request):
        raise AssertionError('已禁用线路不应再发起请求')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, policy)
    assert result.success is False
    assert result.failure_code == 'MANUAL_REJECTION'


@pytest.mark.asyncio
async def test_finite_media_playlist_is_not_published_as_live_channel(candidate_factory):
    candidate = candidate_factory('https://stream.example/live.m3u8', '台视')
    body = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\noutput001.ts\n#EXT-X-ENDLIST\n'
    def handler(request):
        return httpx.Response(200, text=body) if request.url.path.endswith('.m3u8') else httpx.Response(200, content=b'\x47' + b'\x00' * 187)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, PolicyConfig())
    assert result.success is False
    assert result.failure_code == 'NON_LIVE_PLAYLIST'


@pytest.mark.asyncio
async def test_redirect_to_known_placeholder_is_rejected(candidate_factory):
    candidate = candidate_factory('https://stream.example/live.m3u8', '台视')
    def handler(request):
        if request.url.path == '/live.m3u8':
            return httpx.Response(302, headers={'location': 'https://stream.example/nosignal/playlist.m3u8'})
        if request.url.path.endswith('.m3u8'):
            return httpx.Response(200, text='#EXTM3U\n#EXTINF:4,\nsegment.ts\n')
        return httpx.Response(200, content=b'\x47' + b'\x00' * 187)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_http(candidate, client, PolicyConfig())
    assert result.failure_code == 'PLACEHOLDER_STREAM'
