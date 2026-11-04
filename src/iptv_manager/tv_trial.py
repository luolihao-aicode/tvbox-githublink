"""大陆 14 频道个人试播；独立于正式授权目录和每日流水线。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import ipaddress
import socket
import re
from collections import Counter
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import httpx
import yaml

from .catalog import catalog_content_hash, public_stream_url, render_catalog_m3u, validate_channel_catalog
from .classify import classify_channel
from .config import load_config
from .normalize import make_stream_id
from .probe_http import _fetch_sample
from .probe_media import SubprocessRunner
from .storage import atomic_write_text
from .taiwan_trial import _public_url, _request_guard

TARGET_IDS = [f'cctv-{i}' for i in range(1, 11)] + [
    'hunan-satellite', 'jiangsu-satellite', 'zhejiang-satellite', 'beijing-satellite']
NOTICE = '个人试播，未确认分发授权；不属于正式推荐订阅；不得商用或据此宣称授权。'


def safe_url(url: str) -> bool:
    if not _public_url(url) or not public_stream_url(url):
        return False
    parts = urlsplit(url)
    try:
        if ipaddress.ip_address(parts.hostname or '').version != 4:
            return False
    except ValueError:
        pass
    if '/nn_live/nn_x64/' in parts.path.casefold():
        return False
    return not parts.fragment and all(key.casefold() in {
        'id', 'channel', 'quality', 'format', 'type', 'resolution'}
        for key, _ in parse_qsl(parts.query, keep_blank_values=True))


def extract_candidates(text: str, source_url: str) -> list[dict]:
    rules = [r for r in load_config(Path('config')).channels if r.id in TARGET_IDS]
    result = []
    name = ''
    for raw in text.splitlines():
        value = raw.strip().lstrip('\ufeff')
        if value.startswith('#EXTINF:'):
            # 最后一个逗号通常是名称；不把网址中的逗号用于 M3U 名称解析。
            name = value.rsplit(',', 1)[-1].strip()
            tvg = re.search(r'tvg-name="([^"]+)"', value)
            if tvg:
                name = tvg[1]
            continue
        if value.startswith('#') or not value:
            continue
        if ',' in value and not value.startswith(('http://', 'https://')):
            name, value = value.split(',', 1)
        if not safe_url(value):
            name = ''
            continue
        cleaned = re.sub(r'「[^」]*」|【[^】]*】|\s*\((?:\d{3,4}[pi]|HD|FHD|高清|备用\d*)\)', '', name, flags=re.I).strip()
        match = classify_channel(cleaned, rules)
        # 列表名称可能误写；明确的CCTV-4K/8K地址不能冒充数字相同的常规频道。
        if re.search(r'cctv[-_ ]?(?:4|8)k(?:[^a-z0-9]|$)', unquote(value), re.I):
            name = ''
            continue
        if match.channel_id:
            result.append({'channel_id': match.channel_id, 'name': match.channel_name,
                           'url': value, 'source_url': source_url, 'raw_name': name})
        name = ''
    return result


async def collect_hls_sample(client: httpx.AsyncClient, url: str, directory: Path) -> dict:
    """校验整条清单引用后下载三个完整分片；媒体工具只读本地文件。"""
    initial = url
    manifests = []
    first_byte = 0.0
    for _ in range(5):
        if not safe_url(url):
            raise ValueError('UNSAFE_URL')
        response = await _fetch_sample(client, url, 512001)
        first_byte = first_byte or response.first_byte_seconds
        if len(response.body) > 512000:
            raise ValueError('MANIFEST_TOO_LARGE')
        text = response.body.decode('utf-8-sig')
        if not text.lstrip().startswith('#EXTM3U'):
            raise ValueError('NOT_HLS')
        if '#EXT-X-ENDLIST' in text or '#EXT-X-PLAYLIST-TYPE:VOD' in text:
            raise ValueError('NOT_LIVE')
        manifests.append({'url': response.url, 'text': text})
        refs = re.findall(r'\bURI="([^"]+)"', text)
        refs += [v.strip() for v in text.splitlines() if v.strip() and not v.strip().startswith('#')]
        for ref in refs:
            resolved = urljoin(response.url, ref)
            if not safe_url(resolved):
                raise ValueError('UNSAFE_HLS_REFERENCE')
            for hook in client.event_hooks.get('request', []):
                await hook(httpx.Request('GET', resolved))
        if re.search(r'#EXT-X-(?:SESSION-)?KEY:.*METHOD=(?!NONE)', text):
            raise ValueError('ENCRYPTED_HLS_EXCLUDED')
        if '#EXT-X-MEDIA:' in text and 'TYPE=AUDIO' in text and 'URI=' in text:
            raise ValueError('SEPARATE_AUDIO_NOT_SAMPLED')
        if '#EXT-X-STREAM-INF:' in text:
            variants = []
            attrs = ''
            for raw in text.splitlines():
                if raw.startswith('#EXT-X-STREAM-INF:'):
                    attrs = raw
                elif raw.strip() and not raw.startswith('#') and attrs:
                    resolution = re.search(r'RESOLUTION=(\d+)x(\d+)', attrs)
                    bitrate = re.search(r'(?:[:,])BANDWIDTH=(\d+)', attrs)
                    height = int(resolution[2]) if resolution else 0
                    variants.append(((height <= 1080, height, int(bitrate[1]) if bitrate else 0), urljoin(response.url, raw.strip())))
                    attrs = ''
            if not variants:
                raise ValueError('NO_VARIANT')
            url = max(variants)[1]
            continue
        segment_urls = [urljoin(response.url, v.strip()) for v in text.splitlines() if v.strip() and not v.strip().startswith('#')]
        if len(segment_urls) < 3:
            raise ValueError('FEWER_THAN_THREE_SEGMENTS')
        segment_urls = segment_urls[-3:]
        initial_map = re.search(r'#EXT-X-MAP:.*?URI="([^"]+)"', text)
        download_urls = ([urljoin(response.url, initial_map[1])] if initial_map else []) + segment_urls
        blobs = []
        for item in download_urls:
            sample = await _fetch_sample(client, item, 16000001)
            if not sample.body or len(sample.body) > 16000000 or sample.body.lstrip().startswith((b'<', b'#EXTM3U')):
                raise ValueError('INVALID_MEDIA_SEGMENT')
            blobs.append(sample.body)
        directory.mkdir(parents=True, exist_ok=True)
        sample_path = directory / ('sample.mp4' if initial_map else 'sample.ts')
        sample_path.write_bytes(b''.join(blobs))
        (directory / 'manifests.json').write_text(json.dumps(manifests, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'sample_path': str(sample_path), 'playback_url': initial, 'media_url': response.url,
                'first_byte_seconds': round(first_byte, 3), 'sample_bytes': sum(map(len, blobs)),
                'segments_checked': len(segment_urls), 'segment_urls': segment_urls,
                'segment_sha256': [hashlib.sha256(blob).hexdigest() for blob in blobs[-3:]],
                'sample_sha256': hashlib.sha256(b''.join(blobs)).hexdigest(),
                'media_sequence': re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', text)[1] if '#EXT-X-MEDIA-SEQUENCE:' in text else None}
    raise ValueError('HLS_DEPTH_LIMIT')


def assign_origin_groups(lines: list[dict]) -> None:
    """按入口、媒体/分片主机与相同完整分片合并；不能证明不同组的上游独立。"""
    parents = {}
    def root(value):
        parents.setdefault(value, value)
        if parents[value] != value:
            parents[value] = root(parents[value])
        return parents[value]
    def tokens(line):
        urls = [line['url'], line.get('entry_url', line['url']), line.get('media_url', line['url']), *line.get('segment_urls', [])]
        values = ['host:' + (urlsplit(url).hostname or '') for url in urls] + [
            'media:' + line['channel_id'] + ':' + digest for digest in line.get('segment_sha256', [])]
        if line.get('sample_sha256'):
            values.append('sample:' + line['channel_id'] + ':' + line['sample_sha256'])
        return values
    for line in lines:
        values = tokens(line)
        for token in values[1:]:
            a, b = root(values[0]), root(token)
            parents[max(a, b)] = min(a, b)
    for line in lines:
        line['origin_group'] = 'trial_' + hashlib.sha256(root(tokens(line)[0]).encode()).hexdigest()[:16]


def measured_frame_rate(value: str | None) -> int | float | None:
    try:
        rate = Fraction(value or '0/0')
        return (int(rate) if rate.denominator == 1 else round(float(rate), 4)) if rate > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def report_line(item: dict) -> dict:
    result = dict(item)
    media = urlsplit(result.pop('media_url', result['url']))
    result['media_url_without_query'] = urlunsplit((media.scheme, media.netloc, media.path, '', ''))
    result['server_redirect_query_removed'] = bool(media.query)
    result['content_verified'] = False
    result['upstream_independence_confirmed'] = False
    return result


async def measure_sample(sample_path: str) -> dict:
    runner = SubprocessRunner()
    probe = await runner.run(['ffprobe', '-v', 'error', '-read_intervals', '%+4.5', '-show_packets', '-show_streams',
        '-show_entries', 'packet=stream_index,pts_time,size:stream=index,codec_type,codec_name,width,height,avg_frame_rate',
        '-of', 'json', sample_path], 15)
    if probe.returncode:
        raise ValueError('FFPROBE_FAILED')
    payload = json.loads(probe.stdout)
    video = next((s for s in payload.get('streams', []) if s.get('codec_type') == 'video'), {})
    if not any(s.get('codec_type') == 'audio' for s in payload.get('streams', [])):
        raise ValueError('NO_AUDIO')
    if int(video.get('height', 0)) < 480:
        raise ValueError('BELOW_480P')
    packets = [(float(p['pts_time']), int(p['size'])) for p in payload.get('packets', [])
               if p.get('stream_index') == video.get('index') and 'pts_time' in p and 'size' in p
               and math.isfinite(float(p['pts_time']))]
    duration = max((p[0] for p in packets), default=0) - min((p[0] for p in packets), default=0)
    if duration < 3:
        raise ValueError('INSUFFICIENT_MEDIA_DURATION')
    decode = await runner.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', sample_path,
        '-t', '3', '-map', '0:v:0', '-map', '0:a:0', '-progress', 'pipe:1', '-f', 'null', '-'], 20)
    times = re.findall(r'^out_time_us=(\d+)$', decode.stdout, re.M)
    frames = re.findall(r'^frame=\s*(\d+)$', decode.stdout, re.M)
    if decode.returncode or not times or max(map(int, times)) < 2500000 or not frames or max(map(int, frames)) < 20:
        raise ValueError('DECODE_FAILED')
    screenshot = str(Path(sample_path).parent / 'frame.jpg')
    await runner.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', sample_path, '-ss', '1', '-frames:v', '1', '-y', screenshot], 15)
    return {'width': int(video['width']), 'height': int(video['height']), 'video_codec': video.get('codec_name'),
            'frame_rate': video.get('avg_frame_rate'), 'video_bitrate_estimate': round(sum(p[1] for p in packets) * 8 / duration),
            'decoded_seconds': 3, 'decoded_frames': max(map(int, frames)), 'decode_warning': bool(decode.stderr.strip()),
            'decode_warning_excerpt': decode.stderr[:600], 'screenshot': screenshot,
            'checked_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z')}


def write_outputs(lines: list[dict], output_dir: Path, now: datetime, summary: dict) -> dict:
    rules = {r.id: r for r in load_config(Path('config')).channels if r.id in TARGET_IDS}
    channels = []
    selected = []
    for item in lines:
        original = item.get('entry_url', item['url'])
        item['entry_url'] = original
        media_url = item.get('media_url', original)
        item['url'] = media_url if safe_url(media_url) else original
        item['pinned_to_measured_variant'] = safe_url(media_url)
    assign_origin_groups(lines)
    source_ids = set()
    for cid in TARGET_IDS:
        pool = [x for x in lines if x['channel_id'] == cid]
        pool.sort(key=lambda x: (-x['height'], bool(x.get('decode_warning')), -x['video_bitrate_estimate'], x['first_byte_seconds']))
        # 同一主机的多条别名线路不冒充独立备用来源。
        hosts = set()
        sources = []
        for item in pool:
            host = urlsplit(item['url']).hostname
            source_id = make_stream_id(item['url'])
            if host in hosts or source_id in source_ids or len(sources) >= 3:
                continue
            hosts.add(host)
            source_ids.add(source_id)
            score = (80 if item['height'] >= 1080 else 65 if item['height'] >= 720 else 45) - (10 if item.get('decode_warning') else 0)
            sources.append({'id': make_stream_id(item['url']), 'url': item['url'], 'type': 'hls', 'score': score,
                'resolution': f"{item['width']}x{item['height']}", 'videoCodec': item['video_codec'],
                'frameRate': measured_frame_rate(item.get('frame_rate')),
                'lastChecked': item['checked_at'], 'lastSuccess': item['checked_at'],
                'originGroup': item['origin_group'],
                'availability': None, 'environmentLimited': False, 'measurementStatus': 'verified'})
            selected.append(report_line({**item, 'name': rules[cid].name}))
        if sources:
            channels.append({'id': cid, 'name': rules[cid].name, 'group': '央视' if cid.startswith('cctv-') else '地方台', 'logo': '', 'sources': sources})
    generated = now.astimezone(UTC).isoformat().replace('+00:00', 'Z')
    if not channels:
        raise ValueError('没有合格频道；已保留上一版个人试播文件')
    previous = output_dir / 'tv-test.json'
    if previous.is_file():
        try:
            old = json.loads(previous.read_text(encoding='utf-8'))
            validate_channel_catalog(old)
        except (ValueError, KeyError):
            old = {}
        if len(channels) < len(old.get('channels', [])):
            raise ValueError('此次合格频道覆盖下降；已保留上一版个人试播文件')
    document = {'schemaVersion': 1, 'minClientVersion': 1, 'generatedAt': generated,
                'contentHash': catalog_content_hash(channels), 'channels': channels}
    validate_channel_catalog(document)
    report = {'generated_at': generated, 'notice': NOTICE, 'distribution_authorization_confirmed': False,
              'published_channels': len(channels), 'published_lines': len(selected),
              'missing_channels': [rules[c].name for c in TARGET_IDS if not any(x['id'] == c for x in channels)],
              'measurement_limit': '当前电脑网络的单次三分片下载、ffprobe参数与3秒音视频解码；非移动宽带电视实测，不证明长期稳定或真实高清细节。',
              'score_notice': '评分仅按实测帧尺寸与解码警告排序；没有长期可用率数据。',
              'origin_notice': '按入口、最终媒体/分片主机及相同完整分片摘要识别共同来源；不同IP/域名不等于独立上游，不同originGroup也只是尚未发现共同上游。',
              'redirect_notice': '部分公开无凭据入口由源站自动302至带临时查询参数的CDN；仅按常规重定向访问，不提交、生成或复用用户密钥；输出只保留稳定入口，报告移除最终URL查询参数。该入口清晰度可由源站更改。',
              'summary': summary, 'lines': selected}
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_dir / 'tv-test.json', json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    atomic_write_text(output_dir / 'tv-test.m3u', render_catalog_m3u(document))
    atomic_write_text(output_dir / 'tv-test-report.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


async def run(sources_file: Path, output_dir: Path, work_dir: Path) -> dict:
    settings = yaml.safe_load(sources_file.read_text(encoding='utf-8'))
    sources = settings['sources']
    rejected_hosts = settings.get('rejected_hosts', {})
    rejected_streams = set(settings.get('rejected_streams', []))
    rejected_media_routes = set(settings.get('rejected_media_routes', []))
    guard = _request_guard(set())
    ipv4_checks = {}
    async def trial_guard(request):
        # 公开入口的常规服务端302可以附临时CDN参数；不将该地址保存到发布文件。
        # 仍对重定向目标做公共地址/无用户凭据与IPv4检查，不请求HLS加密密钥。
        parts = urlsplit(str(request.url))
        if urlunsplit((parts.scheme, parts.netloc, parts.path, '', '')) in rejected_media_routes:
            raise ValueError('MANUALLY_REJECTED_MEDIA_ROUTE')
        await guard(request)
        host = request.url.host
        if host not in ipv4_checks:
            ipv4_checks[host] = asyncio.create_task(asyncio.get_running_loop().getaddrinfo(
                host, None, family=socket.AF_INET, type=socket.SOCK_STREAM))
        if not await asyncio.shield(ipv4_checks[host]):
            raise ValueError('NO_IPV4_ADDRESS')
    candidates = {}
    fetched = []
    async with httpx.AsyncClient(timeout=8, event_hooks={'request': [trial_guard]}, headers={'User-Agent': 'Mozilla/5.0 personal-stream-trial'}) as client:
        async def fetch(url):
            try:
                sample = await _fetch_sample(client, url, 8000001)
                if len(sample.body) > 8000000:
                    raise ValueError('SOURCE_TOO_LARGE')
                found = extract_candidates(sample.body.decode('utf-8-sig'), url)
                fetched.append({'url': url, 'matched': len(found), 'sha256': hashlib.sha256(sample.body).hexdigest()})
                for item in found:
                    candidates.setdefault(make_stream_id(item['url']), item)
            except Exception as exc:
                fetched.append({'url': url, 'error': type(exc).__name__})
        await asyncio.gather(*(fetch(url) for url in sources))
        if not any('matched' in x for x in fetched):
            raise ValueError('所有来源抓取失败；保留原试播文件')
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / 'candidates.json').write_text(json.dumps(list(candidates.values()), ensure_ascii=False, indent=2), encoding='utf-8')
        candidates = {sid: item for sid, item in candidates.items()
                      if urlsplit(item['url']).hostname not in rejected_hosts and item['url'] not in rejected_streams}
        # 同频道同主机只检测两条入口，避免同源别名把网络时间耗尽。
        per_host_channel = Counter()
        limited_candidates = {}
        for sid, item in candidates.items():
            key = (item['channel_id'], urlsplit(item['url']).netloc)
            per_host_channel[key] += 1
            if per_host_channel[key] <= 2:
                limited_candidates[sid] = item
        candidates = limited_candidates
        print(f'公开列表 {len(fetched)} 份，14 台去重候选 {len(candidates)} 条', flush=True)
        failures = []
        lines = []
        concurrency = asyncio.Semaphore(16)
        media = asyncio.Semaphore(4)
        hosts = {}
        unreachable = set()
        completed = 0
        async def check(sid, item):
            nonlocal completed
            host = urlsplit(item['url']).hostname or ''
            limit = hosts.setdefault(host, asyncio.Semaphore(2))
            try:
                async with concurrency, limit:
                    if urlsplit(item['url']).netloc in unreachable:
                        raise ValueError('ORIGIN_UNREACHABLE')
                    sample = await asyncio.wait_for(collect_hls_sample(client, item['url'], work_dir / sid), 35)
                async with media:
                    measured = await measure_sample(sample['sample_path'])
                lines.append({**item, 'entry_url': item['url'], **sample, **measured,
                              'url': sample['media_url'] if safe_url(sample['media_url']) else item['url']})
                print(f"通过短测：{item['name']} {measured['width']}x{measured['height']} {measured['video_bitrate_estimate']/1e6:.2f}Mbps {host}", flush=True)
            except Exception as exc:
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                    unreachable.add(urlsplit(item['url']).netloc)
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                failures.append({**item, 'reason': reason[:160]})
            finally:
                completed += 1
                if completed % 25 == 0:
                    print(f'进度 {completed}/{len(candidates)}，已通过 {len(lines)}', flush=True)
        await asyncio.gather(*(check(sid, item) for sid, item in candidates.items()))
    (work_dir / 'all-results.json').write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'sources': fetched, 'candidates': len(candidates), 'rejected_hosts': rejected_hosts,
               'failure_counts': dict(Counter(x['reason'] for x in failures)), 'failures': failures}
    result = write_outputs(lines, output_dir, datetime.now(UTC), summary)
    print(f"已输出 {result['published_channels']} 台/{result['published_lines']} 线路，缺台：{result['missing_channels']}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description='独立大陆14频道个人试播检测，不触碰正式授权目录')
    parser.add_argument('--sources', type=Path, default=Path('config/tv_trial_sources.yml'))
    parser.add_argument('--output-dir', type=Path, default=Path('output'))
    parser.add_argument('--work-dir', type=Path, default=Path('artifacts/tv-test'))
    args = parser.parse_args()
    asyncio.run(run(args.sources, args.output_dir, args.work_dir))


if __name__ == '__main__':
    main()
