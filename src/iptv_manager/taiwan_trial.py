"""台湾 1080p 候选试播：单次基础检测，独立输出，不修改推荐订阅。"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import socket
from collections.abc import Awaitable, Callable
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import yaml

from .classify import classify_channel
from .config import load_config
from .models import ChannelCategory, ChannelRule, SourceType, StreamCandidate
from .normalize import make_stream_id, normalize_url
from .parser import parse_m3u
from .probe_http import PLAYLIST_LIMIT, _fetch_sample, probe_http
from .probe_media import SubprocessRunner
from .publisher import PublishMode, render_m3u
from .scoring import ChannelSelection
from .storage import atomic_write_text


def _public_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password:
        return False
    if host in {'localhost', 'youtube.com', 'www.youtube.com', 'youtu.be'} or 'your_iptv' in host:
        return False
    if host.endswith(('.local', '.localhost')):
        return False
    try:
        if not ipaddress.ip_address(host).is_global:
            return False
    except ValueError:
        if re.fullmatch(r'(?:0x[0-9a-f]+|[0-9.]+)', host, re.I):
            return False
    if {'username', 'password', 'user', 'passwd', 'auth', 'authorization', 'access_token', 'api_key'} & {key.lower() for key in parse_qs(parts.query)}:
        return False
    return not re.search(r'^/(?:live|movie|series)/[^/]+/[^/]+/\d+(?:\.|$)', parts.path)


async def _public_dns_addresses(host: str) -> list[str]:
    # 只复核公开列表域名，不发送账户、Cookie 或任何播放口令。
    async with httpx.AsyncClient(timeout=8) as dns_client:
        response = await dns_client.get('https://dns.google/resolve', params={'name': host, 'type': 'A'})
        response.raise_for_status()
        return [answer['data'] for answer in response.json().get('Answer', []) if answer.get('type') == 1]


async def _resolve_public_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if ips and all(ip.is_global for ip in ips):
            return True
        fake_dns_range = ipaddress.ip_network('198.18.0.0/15')
        if ips and all(ip.is_global or ip in fake_dns_range for ip in ips):
            actual = await _public_dns_addresses(host)
            return bool(actual) and all(ipaddress.ip_address(ip).is_global for ip in actual)
        return False


def _request_guard(excluded: set[str]):
    resolutions: dict[str, asyncio.Task] = {}
    async def guard(request: httpx.Request) -> None:
        url = str(request.url)
        if not _public_url(url):
            raise ValueError('NON_PUBLIC_URL')
        if make_stream_id(url) in excluded:
            raise ValueError('EXCLUDED_STREAM')
        host = request.url.host
        if host not in resolutions:
            resolutions[host] = asyncio.create_task(_resolve_public_host(host))
        try:
            public = await asyncio.shield(resolutions[host])
        except (OSError, TimeoutError) as exc:
            raise httpx.ConnectError('无法解析公开来源主机', request=request) from exc
        if not public:
            raise ValueError('NON_PUBLIC_HOST')
    return guard


def extract_trial_candidates(
    text: str, source_url: str, rules: list[ChannelRule], excluded: set[str],
) -> list[StreamCandidate]:
    # 在标准解析器规范化 URL（会移除 userinfo）前先排除含账号口令的行。
    text = re.sub(r'^(#EXTINF:[^,\r\n]+),\s*(?=tvg-(?:id|name|logo)=)', r'\1 ', text, flags=re.M)
    safe = '\n'.join(line if line.strip().startswith('#') or not line.strip()
                     else line if _public_url(line.strip()) else '# 已排除非公开直链'
                     for line in text.splitlines())
    candidates = []
    for candidate in parse_m3u(safe, source_url, SourceType.COMMUNITY):
        name = re.sub(r'\s*[（(](?:备|備|备用|備用)\s*\d+[）)]$', '', candidate.raw_name)
        match = classify_channel(name, rules)
        if match.category is not ChannelCategory.TAIWAN or candidate.stream_id in excluded:
            continue
        candidates.append(candidate.model_copy(update={
            'channel_id': match.channel_id, 'channel_name': match.channel_name,
            'category': match.category,
        }))
    return candidates


def highest_1080_variant(text: str, base_url: str) -> str | None:
    variants: list[tuple[int, str]] = []
    unknown: list[tuple[int, str]] = []
    attributes = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXT-X-STREAM-INF:'):
            attributes = line
        elif line and not line.startswith('#') and attributes:
            resolution = re.search(r'RESOLUTION=(\d+)x(\d+)', attributes, re.I)
            bandwidth = re.search(r'(?:[:,])BANDWIDTH=(\d+)', attributes, re.I)
            rate = int(bandwidth[1]) if bandwidth else 0
            url = urljoin(base_url, line)
            if _public_url(url):
                if resolution and int(resolution[1]) >= 1920 and int(resolution[2]) >= 1080:
                    variants.append((rate, url))
                elif not resolution:
                    unknown.append((rate, url))
            attributes = None
    # 没有尺寸声明时只作为待探测地址，后续必须由 ffprobe 确认尺寸。
    pool = variants or unknown
    return max(pool)[1] if pool else None


async def _pin_variant(candidate: StreamCandidate, client: httpx.AsyncClient,
                       excluded: set[str] | None = None,
                       validate_reference: Callable[[httpx.Request], Awaitable[None]] | None = None) -> StreamCandidate:
    url = str(candidate.url)
    stable_entry = url
    single_variant_chain = True
    excluded = excluded or set()
    for _ in range(4):
        if not _public_url(url):
            raise ValueError('NON_PUBLIC_URL')
        if make_stream_id(url) in excluded:
            raise ValueError('EXCLUDED_STREAM')
        sample = await _fetch_sample(client, url, PLAYLIST_LIMIT)
        if not _public_url(sample.url):
            raise ValueError('NON_PUBLIC_URL')
        if make_stream_id(sample.url) in excluded:
            raise ValueError('EXCLUDED_STREAM')
        text = sample.body.decode('utf-8-sig')
        if not text.lstrip().startswith('#EXTM3U'):
            raise ValueError('NOT_HLS')
        # URI 属性也可能出现在主清单的外置音轨、字幕和会话密钥中。
        refs = re.findall(r'\bURI="([^"]+)"', text)
        if '#EXT-X-STREAM-INF:' not in text:
            refs += [line.strip() for line in text.splitlines()
                     if line.strip() and not line.strip().startswith('#')]
        for ref in refs:
            resolved = urljoin(sample.url, ref)
            if not _public_url(resolved):
                raise ValueError('NON_PUBLIC_URL')
            if validate_reference:
                await validate_reference(httpx.Request('GET', resolved))
        if '#EXT-X-STREAM-INF:' not in text:
            # 单档入口不会自动降档，保留入口可让源站刷新临时播放上下文。
            published_url = stable_entry if single_variant_chain else url
            return candidate.model_copy(update={'url': published_url, 'stream_id': make_stream_id(published_url)})
        single_variant_chain = single_variant_chain and text.count('#EXT-X-STREAM-INF:') == 1
        variant = highest_1080_variant(text, sample.url)
        if variant is None:
            raise ValueError('NO_1080_VARIANT')
        url = normalize_url(variant)
    raise ValueError('HLS_DEPTH_LIMIT')


async def _measure(candidate: StreamCandidate, runner: SubprocessRunner) -> dict:
    output = await runner.run([
        'ffprobe', '-v', 'error', '-rw_timeout', '8000000', '-read_intervals', '%+4.5',
        '-show_packets', '-show_streams', '-show_entries',
        'packet=stream_index,pts_time,size:stream=index,codec_type,codec_name,width,height,avg_frame_rate,bit_rate',
        '-of', 'json', str(candidate.url),
    ], 24)
    if output.returncode:
        raise ValueError('FFPROBE_FAILED')
    payload = json.loads(output.stdout)
    video = next((stream for stream in payload.get('streams', []) if stream.get('codec_type') == 'video'), {})
    width, height = int(video.get('width', 0)), int(video.get('height', 0))
    if width < 1920 or height < 1080:
        raise ValueError('BELOW_1080')
    packets = []
    for packet in payload.get('packets', []):
        if packet.get('stream_index') == video.get('index'):
            try:
                pts, size = float(packet['pts_time']), int(packet['size'])
                if math.isfinite(pts) and size >= 0:
                    packets.append((pts, size))
            except (KeyError, ValueError, TypeError):
                continue
    duration = max((p[0] for p in packets), default=0) - min((p[0] for p in packets), default=0)
    if duration < 3:
        raise ValueError('INSUFFICIENT_MEDIA')
    bitrate = round(sum(p[1] for p in packets) * 8 / duration)
    decoded = await runner.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-rw_timeout', '8000000',
        '-i', str(candidate.url), '-t', '3', '-map', '0:v:0', '-map', '0:a:0?',
        '-progress', 'pipe:1', '-f', 'null', '-',
    ], 24)
    times = re.findall(r'^out_time_us=(\d+)$', decoded.stdout, re.M)
    frames = re.findall(r'^frame=\s*(\d+)$', decoded.stdout, re.M)
    if (decoded.returncode or not times or max(map(int, times)) < 2_500_000
            or not frames or max(map(int, frames)) < 1):
        raise ValueError('DECODE_FAILED')
    warning = bool(decoded.stderr.strip())
    return {'width': width, 'height': height, 'video_codec': video.get('codec_name'),
            'video_bitrate_estimate': bitrate, 'frame_rate': video.get('avg_frame_rate'),
            'decoded_seconds': 3, 'decoded_frames': max(map(int, frames)),
            'decode_warning': warning,
            'decode_notice': '基础解码有输出，但存在警告，仅供试播' if warning else '基础解码通过',
            'checked_at': datetime.now(UTC).isoformat()}


def write_trial_outputs(lines: list[dict], rules: list[ChannelRule], output_dir: Path,
                        checked_at: datetime, summary: dict) -> None:
    selections = []
    counters: Counter[str] = Counter()
    records = []
    for line in sorted(lines, key=lambda x: (x['candidate'].channel_id,
                                             -x['video_bitrate_estimate'], str(x['candidate'].url))):
        candidate = line['candidate']
        channel_id = candidate.channel_id
        counters[channel_id] += 1
        if counters[channel_id] > 5:
            continue
        rate = line['video_bitrate_estimate'] / 1e6
        rate_notice = '（低码率）' if rate < 1.5 else ''
        name = f"{candidate.channel_name} · 线路{counters[channel_id]} · 1080p · {rate:.1f}Mbps{rate_notice}"
        display = candidate.model_copy(update={'channel_name': name})
        selections.append(ChannelSelection(channel_id, display, ()))
        records.append({**{key: value for key, value in line.items() if key != 'candidate'},
                        'channel_id': channel_id, 'name': candidate.channel_name,
                        'display_name': name, 'url': str(candidate.url),
                        'source_url': str(candidate.source_url), 'content_verified': False,
                        'quality_notice': '检测尺寸至少 1920×1080，真实画质与频道内容待人工试播确认'})
    found = {record['channel_id'] for record in records}
    report = {'checked_at': checked_at.isoformat(), 'mode': '1080p候选试播，非推荐订阅',
              'published_channels': len(found), 'published_lines': len(records),
              'missing_channels': [rule.name for rule in rules if rule.id not in found],
              'notice': '本地单次基础检测，不保证高清细节或长期流畅；固定子线路可能仍缓冲。',
              'summary': summary, 'lines': records}
    atomic_write_text(output_dir / 'taiwan-1080-trial.m3u', render_m3u(selections, PublishMode.COMBINED))
    atomic_write_text(output_dir / 'taiwan-1080-trial.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')


async def run_taiwan_trial(config_dir: Path, sources_file: Path, output_dir: Path) -> dict:
    config = load_config(config_dir)
    try:
        settings = yaml.safe_load(sources_file.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise ValueError('试播来源配置不是有效 YAML') from exc
    if (not isinstance(settings, dict) or not isinstance(settings.get('sources'), list)
            or not settings['sources'] or any(not isinstance(url, str) or not _public_url(url)
                                             for url in settings['sources'])):
        raise ValueError('试播来源配置必须包含非空的公开 sources 地址列表')
    rules = [rule for rule in config.channels if rule.category is ChannelCategory.TAIWAN]
    excluded_settings = settings.get('excluded_streams', [])
    if not isinstance(excluded_settings, list) or any(not isinstance(item, str) for item in excluded_settings):
        raise ValueError('excluded_streams 必须是线路 ID 字符串列表')
    excluded = set(excluded_settings) | set(config.policy.rejected_streams)
    policy = config.policy.model_copy(update={'http_timeout_seconds': 8})
    failures: Counter[str] = Counter()
    source_results = []
    candidates: dict[str, StreamCandidate] = {}
    guard = _request_guard(excluded)
    async with httpx.AsyncClient(timeout=8, event_hooks={'request': [guard]}) as client:
        async def fetch(url):
            try:
                sample = await _fetch_sample(client, url, 2_000_001)
                if len(sample.body) > 2_000_000:
                    raise ValueError('SOURCE_TOO_LARGE')
                items = extract_trial_candidates(sample.body.decode('utf-8-sig'), url, rules, excluded)
                source_results.append({'url': url, 'matched': len(items)})
                for item in items:
                    candidates.setdefault(item.stream_id, item)
            except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                source_results.append({'url': url, 'error': type(exc).__name__})
        await asyncio.gather(*(fetch(url) for url in settings['sources']))
        if not any('matched' in result for result in source_results):
            raise ValueError('所有试播来源抓取失败，已保留上一版试播输出；请检查网络及来源地址')
        print(f'已抓取 {len(source_results)} 份公开列表，去重后 {len(candidates)} 条台湾候选', flush=True)
        limit = asyncio.Semaphore(16)
        media_limit = asyncio.Semaphore(4)
        hosts: dict[str, asyncio.Semaphore] = {}
        unreachable: set[tuple[str, str]] = set()
        lines = []
        seen: set[str] = set()
        completed = 0

        async def check(candidate):
            nonlocal completed
            host = urlsplit(str(candidate.url)).hostname or ''
            origin = urlsplit(str(candidate.url))
            origin_key = (origin.scheme, origin.netloc)
            host_limit = hosts.setdefault(host, asyncio.Semaphore(2))
            try:
                async with limit, host_limit:
                    if origin_key in unreachable:
                        raise ValueError('HOST_UNREACHABLE')
                    pinned = await asyncio.wait_for(_pin_variant(candidate, client, excluded, guard), 20)
                    if pinned.stream_id in excluded or pinned.stream_id in seen:
                        raise ValueError('EXCLUDED_OR_DUPLICATE')
                    seen.add(pinned.stream_id)
                    result = await asyncio.wait_for(probe_http(pinned, client, policy), 16)
                    if not result.success:
                        raise ValueError(result.failure_code or 'HTTP_FAILED')
                    async with media_limit:
                        metadata = await _measure(pinned, SubprocessRunner())
                    lines.append({'candidate': pinned, **metadata,
                                  'first_byte_seconds': result.first_byte_seconds})
                    print(f"可试播：{pinned.channel_name}，1080p，视频约 {metadata['video_bitrate_estimate']/1e6:.2f} Mbps", flush=True)
            except TimeoutError:
                failures['TIMEOUT'] += 1
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                failed_origin = urlsplit(str(exc.request.url))
                if (failed_origin.scheme, failed_origin.netloc) == origin_key:
                    unreachable.add(origin_key)
                failures['CONNECT_FAILED'] += 1
            except httpx.HTTPError:
                failures['HTTP_FAILED'] += 1
            except (ValueError, OSError, KeyError, TypeError) as exc:
                failures[str(exc) if isinstance(exc, ValueError) else type(exc).__name__] += 1
            finally:
                completed += 1
                if completed % 25 == 0:
                    print(f'检测进度：{completed}/{len(candidates)}，已通过 {len(lines)} 条', flush=True)

        await asyncio.gather(*(check(candidate) for candidate in candidates.values()))
    summary = {'sources': source_results, 'candidate_lines': len(candidates),
               'filter_reasons': dict(failures)}
    write_trial_outputs(lines, rules, output_dir, datetime.now(UTC), summary)
    print(f'试播检测完成：{len({x["candidate"].channel_id for x in lines})} 个频道，{len(lines)} 条线路', flush=True)
    return summary
