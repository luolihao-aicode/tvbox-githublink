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
