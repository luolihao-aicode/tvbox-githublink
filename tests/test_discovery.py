import httpx
import pytest

from iptv_manager.discovery import fetch_seed_sources, search_github
from iptv_manager.models import AppConfig, GitHubSearchConfig, PolicyConfig, SourceConfig


@pytest.mark.asyncio
async def test_seed_larger_than_configured_limit_is_rejected() -> None:
    source = SourceConfig(
        url="https://list.example/channels.m3u",
        max_bytes=16,
    )
    config = AppConfig(sources=[source], channels=[], policy=PolicyConfig())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 17, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_seed_sources(config, client)

    assert result.candidates == []
    assert result.issues[0].code == "SOURCE_TOO_LARGE"


@pytest.mark.asyncio
async def test_github_search_follows_pagination_and_parses_public_lists() -> None:
    config = AppConfig(
        sources=[],
        channels=[],
        policy=PolicyConfig(),
        github_search=GitHubSearchConfig(queries=["extension:m3u CCTV"], max_files=2),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path == "/search/code" and request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json={"items": [{"url": "https://api.github.com/files/one"}]},
                headers={"Link": '<https://api.github.com/search/code?page=2>; rel="next"'},
                request=request,
            )
        if request.url.path == "/search/code":
            return httpx.Response(
                200,
                json={"items": [{"url": "https://api.github.com/files/two"}]},
                request=request,
            )
        name = request.url.path.rsplit("/", 1)[-1]
        body = f'#EXTM3U\n#EXTINF:-1,CCTV-{name}\nhttps://stream.example/{name}.m3u8\n'
        return httpx.Response(
            200,
            json={"size": len(body.encode()), "encoding": "base64", "content": __import__("base64").b64encode(body.encode()).decode()},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_github(config, "test-token", client)

    assert [item.raw_name for item in result.candidates] == ["CCTV-one", "CCTV-two"]
