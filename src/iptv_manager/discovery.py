from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from .models import AppConfig, SourceType, StreamCandidate
from .parser import parse_m3u


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    source_url: str
    code: str
    message: str


@dataclass(slots=True)
class DiscoveryResult:
    candidates: list[StreamCandidate] = field(default_factory=list)
    issues: list[DiscoveryIssue] = field(default_factory=list)


async def fetch_seed_sources(
    config: AppConfig,
    client: httpx.AsyncClient,
) -> DiscoveryResult:
    result = DiscoveryResult()
    for source in config.sources:
        if not source.enabled:
            continue
        source_url = str(source.url)
        try:
            async with client.stream("GET", source_url, follow_redirects=True) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > source.max_bytes:
                    result.issues.append(
                        DiscoveryIssue(source_url, "SOURCE_TOO_LARGE", "来源文件超过大小限制")
                    )
                    continue
                payload = bytearray()
                too_large = False
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > source.max_bytes:
                        too_large = True
                        break
                if too_large:
                    result.issues.append(
                        DiscoveryIssue(source_url, "SOURCE_TOO_LARGE", "来源文件超过大小限制")
                    )
                    continue
        except (httpx.HTTPError, ValueError) as exc:
            result.issues.append(
                DiscoveryIssue(source_url, "SOURCE_FETCH_FAILED", f"抓取来源失败：{exc}")
            )
            continue

        try:
            text = bytes(payload).decode("utf-8-sig")
        except UnicodeDecodeError:
            result.issues.append(
                DiscoveryIssue(source_url, "SOURCE_ENCODING_INVALID", "来源不是有效 UTF-8 文本")
            )
            continue
        result.candidates.extend(parse_m3u(text, source_url, source.type))
    return result


async def search_github(
    config: AppConfig,
    token: str,
    client: httpx.AsyncClient,
) -> DiscoveryResult:
    result = DiscoveryResult()
    settings = config.github_search
    if not settings.enabled:
        return result
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    files_seen = 0

    for query in settings.queries:
        page = 1
        while page <= settings.max_pages and files_seen < settings.max_files:
            try:
                response = await client.get(
                    "https://api.github.com/search/code",
                    params={"q": query, "page": page, "per_page": 100},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                result.issues.append(
                    DiscoveryIssue("https://api.github.com/search/code", "GITHUB_SEARCH_FAILED", f"GitHub 搜索失败：{exc}")
                )
                break

            items = payload.get("items", []) if isinstance(payload, dict) else []
            for item in items:
                if files_seen >= settings.max_files:
                    break
                api_url = item.get("url") if isinstance(item, dict) else None
                if not isinstance(api_url, str):
                    continue
                files_seen += 1
                try:
                    file_response = await client.get(api_url, headers=headers)
                    file_response.raise_for_status()
                    file_data = file_response.json()
                    if not isinstance(file_data, dict):
                        raise ValueError("文件元数据格式无效")
                    if int(file_data.get("size", 0)) > settings.max_bytes:
                        result.issues.append(
                            DiscoveryIssue(api_url, "SOURCE_TOO_LARGE", "GitHub 文件超过大小限制")
                        )
                        continue
                    if file_data.get("encoding") != "base64":
                        raise ValueError("GitHub 文件编码不是 base64")
                    encoded = ''.join(file_data.get('content', '').split())
                    raw = base64.b64decode(encoded, validate=True)
                    if len(raw) > settings.max_bytes:
                        result.issues.append(
                            DiscoveryIssue(api_url, "SOURCE_TOO_LARGE", "GitHub 文件超过大小限制")
                        )
                        continue
                    text = raw.decode("utf-8-sig")
                    result.candidates.extend(
                        parse_m3u(text, api_url, source_type=SourceType.COMMUNITY)
                    )
                except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                    result.issues.append(
                        DiscoveryIssue(api_url, "GITHUB_FILE_FAILED", f"读取 GitHub 文件失败：{exc}")
                    )

            link = response.headers.get("link", "")
            if 'rel="next"' not in link:
                break
            page += 1
    return result
