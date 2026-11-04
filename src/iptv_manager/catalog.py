from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml
from pydantic import Field, HttpUrl, field_validator

from .models import CheckResult, PolicyConfig, StreamCandidate, StrictModel
from .scoring import _is_publishable
from .storage import atomic_write_text


# 公开目录只接受无需携带秘密的地址；不删除令牌后伪装成可播放地址。
def public_stream_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.fragment:
            return False
        # 未知查询参数也可能是令牌，首版只允许明确无敏感含义的参数。
        allowed = {'channel', 'id', 'quality', 'format', 'type', 'resolution'}
        if any(key.casefold() not in allowed for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
            return False
        path = unquote(parts.path).casefold()
        segments = [part for part in path.split('/') if part]
        if any(part in {'live', 'movie', 'series'} and len(segments) - index >= 4
               for index, part in enumerate(segments)):
            return False
        return not any(marker in path for marker in ('token=', 'password=', 'secret=', 'auth=', '/token/', '/secret/', '/password/', '/signature/', '/hmac/'))
    except ValueError:
        return False


class UsageGrant(StrictModel):
    streamId: str = Field(min_length=1)
    evidenceUrl: HttpUrl
    purpose: str = Field(min_length=5)
    allowed: bool

    @field_validator('evidenceUrl')
    @classmethod
    def evidence_not_secret(cls, value):
        if not public_stream_url(str(value)):
            raise ValueError('授权证据地址不能含凭据或敏感参数')
        return value

    @field_validator('purpose')
    @classmethod
    def purpose_not_blank(cls, value):
        if not value.strip():
            raise ValueError('授权用途不能为空')
        return value


class UsageRights(StrictModel):
    schemaVersion: Literal[1]
    grants: list[UsageGrant]


def load_usage_rights(path: Path) -> dict:
    # 旧配置目录不含此文件时默认拒绝；不能从 official/community 标签推断许可。
    data = yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else {'schemaVersion': 1, 'grants': []}
    return UsageRights.model_validate(data).model_dump(mode='json')


class CatalogSource(StrictModel):
    id: str = Field(min_length=1)
    url: HttpUrl
    type: Literal['hls', 'dash', 'progressive']
    score: int = Field(ge=0, le=100, strict=True)
    resolution: str | None = Field(pattern=r'^\d+x\d+$')
    videoCodec: str | None
    frameRate: float | None = Field(ge=0)
    lastChecked: str | None
    originGroup: str = Field(min_length=1)
    lastSuccess: str | None
    availability: float | None = Field(ge=0, le=1)
    environmentLimited: bool = False
    measurementStatus: Literal['verified', 'historicalEnvironmentLimited'] = 'verified'

    @field_validator('url')
    @classmethod
    def reject_sensitive_url(cls, value):
        if not public_stream_url(str(value)):
            raise ValueError('目录地址包含敏感或未经认可的参数')
        return value

    @field_validator('lastChecked', 'lastSuccess')
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            _parse_timestamp(value)
        return value


class CatalogChannel(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    group: str
    logo: Literal['']
    sources: list[CatalogSource] = Field(min_length=1)


class ChannelCatalog(StrictModel):
    schemaVersion: Literal[1]
    minClientVersion: Literal[1]
    generatedAt: str
    contentHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    channels: list[CatalogChannel]

    @field_validator('generatedAt')
    @classmethod
    def validate_timestamp(cls, value):
        _parse_timestamp(value)
        return value


def _parse_timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('检测时间必须包含时区')
    return parsed


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z') if value else None


def catalog_content_hash(channels: list) -> str:
    canonical = json.dumps(channels, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _measured_number(value: float | None) -> int | float | None:
    if value is None:
        return None
    rounded = round(value, 4)
    return int(rounded) if rounded.is_integer() else rounded


def validate_channel_catalog(document: dict) -> None:
    ChannelCatalog.model_validate(document)
    ids = set()
    source_ids = set()
    for channel in document['channels']:
        if channel['id'] in ids:
            raise ValueError('频道ID重复')
        ids.add(channel['id'])
        for source in channel['sources']:
            if source['id'] in source_ids:
                raise ValueError('来源ID重复')
            source_ids.add(source['id'])
    if document['contentHash'] != catalog_content_hash(document['channels']):
        raise ValueError('频道目录内容摘要不符')


def _source_type(stream: StreamCandidate) -> str | None:
    path = urlsplit(str(stream.url)).path.lower()
    if path.endswith('.m3u8') or stream.protocol in {'hls', 'HLS'}:
        return 'hls'
    if path.endswith('.mpd') or stream.protocol in {'dash', 'DASH'}:
        return 'dash'
    if path.endswith(('.ts', '.mp4', '.m4v', '.webm', '.mkv')) or stream.protocol in {'http', 'https', 'progressive'}:
        return 'progressive'
    return None


ENVIRONMENT_UNKNOWN_CODES = {'ENVIRONMENT_LIMITED', 'HTTP_TIMEOUT', 'FIRST_BYTE_TIMEOUT'}


def _historical_environment_sample(stream: StreamCandidate, policy: PolicyConfig,
                                   now: datetime) -> CheckResult | None:
    if stream.stream_id in policy.rejected_streams or stream.channel_id is None or not stream.history:
        return None
    latest = stream.history[-1]
    if latest.success or latest.failure_code not in ENVIRONMENT_UNKNOWN_CODES:
        return None
    for item in reversed(stream.history):
        if not item.success:
            if item.failure_code not in ENVIRONMENT_UNKNOWN_CODES:
                return None
            continue
        if (item.continuous_read and (item.height or 0) >= policy.minimum_height
                and item.first_byte_seconds is not None and item.first_byte_seconds <= policy.http_timeout_seconds
                and 0 <= (now - item.checked_at).total_seconds() <= 30 * 86400):
            return item
        return None
    return None


def _origin_groups(streams: Sequence[StreamCandidate]) -> dict[str, str]:
    # 将原始域名和最近最终域名放在同一连通分量，识别重定向别名。
    parents: dict[str, str] = {}
    def root(host):
        parents.setdefault(host, host)
        if parents[host] != host:
            parents[host] = root(parents[host])
        return parents[host]
    def hosts(stream):
        values = [urlsplit(str(stream.url)).hostname or '']
        if stream.history and stream.history[-1].final_url:
            values.append(urlsplit(str(stream.history[-1].final_url)).hostname or '')
        return values
    for stream in streams:
        pair = hosts(stream)
        if len(pair) == 2:
            a, b = root(pair[0]), root(pair[1])
            parents[max(a, b)] = min(a, b)
        else:
            root(pair[0])
    return {stream.stream_id: 'origin_' + hashlib.sha256(root(hosts(stream)[0]).encode()).hexdigest()[:16]
            for stream in streams}


def build_channel_catalog(streams: Sequence[StreamCandidate], policy: PolicyConfig,
                          rights: dict, generated_at: datetime) -> dict:
    permission = UsageRights.model_validate(rights)
    grants = {grant.streamId for grant in permission.grants if grant.allowed}
    # 同ID出现撤销记录时拒绝，不因列表顺序意外恢复授权。
    grants -= {grant.streamId for grant in permission.grants if not grant.allowed}
    historical = {stream.stream_id: sample for stream in streams
                  if (sample := _historical_environment_sample(stream, policy, generated_at)) is not None}
    eligible = [stream for stream in streams if stream.stream_id in grants
                and (_is_publishable(stream, policy) or stream.stream_id in historical)
                and public_stream_url(str(stream.url)) and _source_type(stream)]
    origins = _origin_groups(eligible)
    grouped: dict[str, list[StreamCandidate]] = defaultdict(list)
    for stream in eligible:
        grouped[stream.channel_id].append(stream)
    channels = []
    for channel_id, candidates in sorted(grouped.items()):
        candidates.sort(key=lambda stream: (stream.stream_id in historical, -stream.score, stream.stream_id))
        sources = []
        for stream in candidates:
            latest = stream.history[-1] if stream.history else None
            measured = historical.get(stream.stream_id) or latest
            limited = stream.stream_id in historical
            success = max((item.checked_at for item in stream.history if item.success), default=stream.last_success_at)
            ratio = round(sum(item.success for item in stream.history) / len(stream.history), 4) if stream.history else None
            if ratio is not None and ratio.is_integer():
                ratio = int(ratio)
            sources.append({'id': stream.stream_id, 'url': str(stream.url), 'type': _source_type(stream),
                            'score': min(round(stream.score), 40) if limited else round(stream.score),
                            'resolution': f'{measured.width}x{measured.height}' if measured and measured.width and measured.height else None,
                            'videoCodec': measured.video_codec if measured else None,
                            'frameRate': _measured_number(measured.frame_rate) if measured else None,
                            'lastChecked': _timestamp(latest.checked_at if latest else None),
                            'originGroup': origins[stream.stream_id], 'lastSuccess': _timestamp(success),
                            'availability': ratio, 'environmentLimited': limited,
                            'measurementStatus': 'historicalEnvironmentLimited' if limited else 'verified'})
        first = candidates[0]
        channels.append({'id': channel_id, 'name': first.channel_name or channel_id,
                         'group': first.category.value if first.category else '其他', 'logo': '', 'sources': sources})
    document = {'schemaVersion': 1, 'minClientVersion': 1, 'generatedAt': _timestamp(generated_at),
                'contentHash': catalog_content_hash(channels), 'channels': channels}
    validate_channel_catalog(document)
    return document


def render_catalog_m3u(document: dict) -> str:
    from .publisher import _safe_attribute
    lines = ['#EXTM3U']
    for channel in document['channels']:
        for index, source in enumerate(channel['sources']):
            name = channel['name'] + (f' · 备用{index}' if index else '')
            lines.append(f'#EXTINF:-1 tvg-id="{_safe_attribute(channel["id"])}" '
                         f'tvg-name="{_safe_attribute(channel["name"])}" '
                         f'group-title="{_safe_attribute(channel["group"])}",{_safe_attribute(name).replace(",", "，")}')
            lines.append(source['url'])
    return '\n'.join(lines) + '\n'


def _safe_release_path(output_dir: Path, relative: str) -> Path:
    path = (output_dir / relative).resolve()
    if not path.is_relative_to(output_dir.resolve() / 'catalog-versions'):
        raise ValueError('发布清单路径越界')
    return path


def validate_catalog_release(output_dir: Path) -> dict:
    manifest = json.loads((output_dir / 'catalog-manifest.json').read_text(encoding='utf-8'))
    document = json.loads(_safe_release_path(output_dir, manifest['channels']).read_text(encoding='utf-8'))
    validate_channel_catalog(document)
    if manifest['contentHash'] != document['contentHash']:
        raise ValueError('发布清单摘要不符')
    actual = _safe_release_path(output_dir, manifest['playlist']).read_text(encoding='utf-8')
    if actual != render_catalog_m3u(document):
        raise ValueError('JSON 与 M3U 不属于同一发布')
    return document


def publish_channel_catalog(document: dict, output_dir: Path) -> None:
    validate_channel_catalog(document)
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    playlist = render_catalog_m3u(document)
    # 发布时间参与版本ID；缓存摘要只跟频道内容关联。
    version = hashlib.sha256(text.encode('utf-8')).hexdigest()
    directory = Path('catalog-versions') / version
    atomic_write_text(output_dir / directory / 'channels.json', text)
    atomic_write_text(output_dir / directory / 'all.m3u', playlist)
    # 写入后回读验证，不把截断文件发布给客户端。
    saved = json.loads((output_dir / directory / 'channels.json').read_text(encoding='utf-8'))
    validate_channel_catalog(saved)
    if (output_dir / directory / 'all.m3u').read_text(encoding='utf-8') != render_catalog_m3u(saved):
        raise ValueError('发布文件回读不符')
    previous = None
    previous_channels = {}
    manifest_path = output_dir / 'catalog-manifest.json'
    if manifest_path.exists():
        valid_previous = validate_catalog_release(output_dir)
        previous_channels = {channel['id']: channel for channel in valid_previous['channels']}
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))['channels']
    current_channels = {channel['id']: channel for channel in document['channels']}
    changes = {'added': sorted(current_channels.keys() - previous_channels.keys()),
               'removed': sorted(previous_channels.keys() - current_channels.keys()),
               'modified': sorted(channel_id for channel_id in current_channels.keys() & previous_channels.keys()
                                  if current_channels[channel_id] != previous_channels[channel_id])}
    manifest = {'schemaVersion': 1, 'contentHash': document['contentHash'],
                'channels': (directory / 'channels.json').as_posix(),
                'playlist': (directory / 'all.m3u').as_posix(), 'previous': previous, 'changes': changes}
    # 稳定路径是兼容副本；真正的成对发布以最后原子替换的清单为准。
    old = {name: (output_dir / name).read_text(encoding='utf-8') if (output_dir / name).exists() else None
           for name in ('channels.json', 'all.m3u')}
    try:
        atomic_write_text(output_dir / 'channels.json', text)
        atomic_write_text(output_dir / 'all.m3u', playlist)
        atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    except OSError:
        for name, content in old.items():
            if content is not None:
                atomic_write_text(output_dir / name, content)
            else:
                (output_dir / name).unlink(missing_ok=True)
        raise
