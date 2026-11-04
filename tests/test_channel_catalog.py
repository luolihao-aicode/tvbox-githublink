import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iptv_manager import publisher
from iptv_manager import catalog
from iptv_manager.models import CheckResult, PolicyConfig, StreamStatus
from iptv_manager.scoring import calculate_score, update_lifecycle

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def eligible(factory, url):
    stream = factory(url, 'CCTV-1')
    stream.channel_id, stream.channel_name = 'cctv-1', 'CCTV-1 综合'
    stream.history = [CheckResult(checked_at=NOW - timedelta(hours=i), success=True,
                                first_byte_seconds=.5, width=1920, height=1080,
                                video_codec='h264', continuous_read=True) for i in (1, 0)]
    stream.score = 90
    return stream


def rights(streams):
    return {'schemaVersion': 1, 'grants': [
        {'streamId': s.stream_id, 'evidenceUrl': 'https://rights.example/permission',
         'purpose': '允许测试并在本应用中使用和发布直播地址', 'allowed': True}
        for s in streams]}


def test_catalog_denies_unlicensed_community_even_if_official_label(candidate_factory):
    stream = eligible(candidate_factory, 'https://one.example/live.m3u8')
    doc = publisher.build_channel_catalog([stream], PolicyConfig(), {'schemaVersion': 1, 'grants': []}, NOW)
    assert doc['channels'] == []
    publisher.validate_channel_catalog(doc)


def test_catalog_retains_all_qualified_backups_and_shared_origin_group(candidate_factory):
    streams = [eligible(candidate_factory, f'https://{host}.example/{i}.m3u8')
               for i, host in enumerate(('a', 'a', 'b', 'c', 'd'))]
    doc = publisher.build_channel_catalog(streams, PolicyConfig(), rights(streams), NOW)
    sources = doc['channels'][0]['sources']
    assert len(sources) == 5
    by_id = {source['id']: source for source in sources}
    assert by_id[streams[0].stream_id]['originGroup'] == by_id[streams[1].stream_id]['originGroup']
    assert len({s['originGroup'] for s in sources}) == 4
    assert sources[0]['availability'] == 1
    assert sources[0]['resolution'] == '1920x1080'
    assert sources[0]['lastSuccess'] == NOW.isoformat().replace('+00:00', 'Z')
    publisher.validate_channel_catalog(doc)


@pytest.mark.parametrize('url', [
    'https://user:password@a.example/live.m3u8',
    'https://a.example/live.m3u8?token=secret',
    'https://a.example/live.m3u8?X-Amz-Signature=secret',
    'https://a.example/live.m3u8?auth_key=secret',
    'https://a.example/live.m3u8#password=secret',
])
def test_catalog_never_exports_sensitive_urls(candidate_factory, url):
    stream = eligible(candidate_factory, url)
    doc = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    assert doc['channels'] == []


def test_catalog_rejects_forged_hash_invalid_type_and_unknown_fields(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    doc = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    for change in ({'contentHash': '0' * 64}, {'minClientVersion': 0}, {'secret': 'x'}):
        with pytest.raises(ValueError):
            publisher.validate_channel_catalog({**doc, **change})
    doc['channels'][0]['sources'][0]['type'] = 'udp'
    with pytest.raises(ValueError):
        publisher.validate_channel_catalog(doc)


def test_catalog_hash_ignores_generation_time_for_incremental_cache(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    one = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    two = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW + timedelta(hours=1))
    assert one['contentHash'] == two['contentHash']
    assert one['generatedAt'] != two['generatedAt']


def test_catalog_atomic_manifest_preserves_last_pair_after_failed_write(tmp_path, monkeypatch, candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    doc = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    publisher.publish_channel_catalog(doc, tmp_path)
    before = (tmp_path / 'catalog-manifest.json').read_bytes()
    stable = (tmp_path / 'channels.json').read_bytes()
    original = catalog.atomic_write_text
    def fail(path, text):
        if path.name == 'all.m3u' and 'catalog-versions' in str(path):
            raise OSError('磁盘写入失败')
        original(path, text)
    monkeypatch.setattr(catalog, 'atomic_write_text', fail)
    changed = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW + timedelta(hours=1))
    with pytest.raises(OSError):
        publisher.publish_channel_catalog(changed, tmp_path)
    assert (tmp_path / 'catalog-manifest.json').read_bytes() == before
    assert (tmp_path / 'channels.json').read_bytes() == stable
    manifest = json.loads(before)
    assert (tmp_path / manifest['channels']).is_file()
    assert (tmp_path / manifest['playlist']).is_file()


def test_catalog_publishes_previous_version_and_validates_m3u_pair(tmp_path, candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    doc = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    publisher.publish_channel_catalog(doc, tmp_path)
    empty = publisher.build_channel_catalog([], PolicyConfig(), rights([]), NOW + timedelta(days=1))
    publisher.publish_channel_catalog(empty, tmp_path)
    manifest = json.loads((tmp_path / 'catalog-manifest.json').read_text())
    assert manifest['previous']
    previous = json.loads((tmp_path / manifest['previous']).read_text())
    assert previous['contentHash'] == doc['contentHash']
    publisher.validate_catalog_release(tmp_path)
    (tmp_path / manifest['playlist']).write_text('#EXTM3U\nhttps://wrong.example\n')
    with pytest.raises(ValueError):
        publisher.validate_catalog_release(tmp_path)


def test_recent_failure_penalty_and_freshness_use_explicit_now():
    good = CheckResult(checked_at=NOW, success=True, first_byte_seconds=.5, height=1080, continuous_read=True)
    bad = CheckResult(checked_at=NOW, success=False, failure_code='INVALID_MEDIA')
    policy = PolicyConfig()
    assert calculate_score([good, good, bad], policy, now=NOW) < calculate_score([bad, good, good], policy, now=NOW)
    old = good.model_copy(update={'checked_at': NOW - timedelta(days=10)})
    assert calculate_score([old], policy, now=NOW) < calculate_score([good], policy, now=NOW)


def test_environment_restriction_keeps_candidate_not_retired(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    stream.discovered_at = NOW - timedelta(days=60)
    stream.history = [CheckResult(checked_at=NOW, success=False, failure_code='ENVIRONMENT_LIMITED')]
    stream.status = StreamStatus.ACTIVE
    assert update_lifecycle(stream, NOW, PolicyConfig()).status is not StreamStatus.RETIRED


def test_redirect_final_url_groups_pseudo_independent_origins(candidate_factory):
    streams = [eligible(candidate_factory, f'https://{host}.example/live.m3u8') for host in ('a', 'b')]
    for stream in streams:
        stream.history[-1] = stream.history[-1].model_copy(update={'final_url': 'https://origin.example/live.m3u8'})
    doc = publisher.build_channel_catalog(streams, PolicyConfig(), rights(streams), NOW)
    assert len({s['originGroup'] for s in doc['channels'][0]['sources']}) == 1


def test_usage_rights_invalid_evidence_fails_closed(tmp_path):
    path = tmp_path / 'usage_rights.yml'
    path.write_text('schemaVersion: 1\ngrants:\n- streamId: a\n  allowed: true\n  evidenceUrl: ""\n  purpose: ""\n')
    with pytest.raises(ValueError):
        publisher.load_usage_rights(path)

def test_generated_catalog_schema_is_checked_in_and_matches_runtime():
    from iptv_manager.catalog import ChannelCatalog
    assert json.loads(Path('schema/channels.schema.json').read_text(encoding='utf-8')) == ChannelCatalog.model_json_schema()


def test_validate_catalog_cli_detects_bad_release(tmp_path):
    from iptv_manager.cli import main
    assert main(['validate-catalog', '--output-dir', str(tmp_path)]) == 2
    document = publisher.build_channel_catalog([], PolicyConfig(), rights([]), NOW)
    publisher.publish_channel_catalog(document, tmp_path)
    assert main(['validate-catalog', '--output-dir', str(tmp_path)]) == 0


def test_actions_validate_catalog_and_copy_versioned_pair():
    import yaml
    for name in ('update', 'discover'):
        workflow = yaml.load(Path(f'.github/workflows/{name}.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        steps = next(iter(workflow['jobs'].values()))['steps']
        assert any('validate-catalog' in step.get('run', '') for step in steps)
    page = Path('.github/workflows/pages.yml').read_text(encoding='utf-8')
    assert 'output/channels.json' in page
    assert 'output/catalog-manifest.json' in page
    assert 'output/catalog-versions' in page


def test_unknown_frame_rate_remains_null(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    document = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    assert document['channels'][0]['sources'][0]['frameRate'] is None

@pytest.mark.asyncio
async def test_pipeline_retains_valid_authorized_catalog_when_probes_fail(tmp_path, candidate_factory):
    import shutil
    import yaml
    from iptv_manager.discovery import DiscoveryResult
    from iptv_manager.pipeline import PipelineDependencies, PipelineOptions, run_pipeline
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    config = tmp_path / 'config'
    shutil.copytree('config', config)
    (config / 'usage_rights.yml').write_text(yaml.safe_dump(rights([stream])), encoding='utf-8')
    output = tmp_path / 'output'
    publisher.publish_channel_catalog(publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW), output)
    original = (output / 'catalog-manifest.json').read_bytes()
    async def discover():
        return DiscoveryResult(candidates=[stream])
    async def probe_http(items, policy):
        return [CheckResult(checked_at=NOW, success=False, failure_code='HTTP_TIMEOUT') for _ in items]
    async def probe_media(item, policy):
        raise AssertionError('HTTP失败不执行媒体检测')
    await run_pipeline(PipelineOptions(config, tmp_path / 'data', output),
                       PipelineDependencies(discover, probe_http, probe_media, now=lambda: NOW))
    assert (output / 'catalog-manifest.json').read_bytes() == original


def test_fps_number_normalization_supports_cross_language_hash(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    stream.history[-1] = stream.history[-1].model_copy(update={'frame_rate': 25.0})
    document = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    assert type(document['channels'][0]['sources'][0]['frameRate']) is int

@pytest.mark.parametrize('url', [
    'https://a.example/live/account/s3cr3t/123.m3u8',
    'https://a.example/movie/account/s3cr3t/123.mp4',
    'https://a.example/series/account/s3cr3t/123.mp4',
])
def test_catalog_rejects_xtream_account_paths(candidate_factory, url):
    stream = eligible(candidate_factory, url)
    document = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    assert document['channels'] == []


def test_usage_rights_rejects_sensitive_evidence_url(tmp_path):
    import yaml
    path = tmp_path / 'usage_rights.yml'
    data = {'schemaVersion': 1, 'grants': [{'streamId': 'x', 'allowed': True,
             'purpose': '允许测试和应用使用', 'evidenceUrl': 'https://user:secret@rights.example/grant?token=secret'}]}
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ValueError):
        publisher.load_usage_rights(path)


def test_manifest_identifies_changed_added_removed_channels(tmp_path, candidate_factory):
    first = eligible(candidate_factory, 'https://a.example/live.m3u8')
    second = eligible(candidate_factory, 'https://b.example/live.m3u8')
    second.channel_id, second.channel_name = 'cctv-2', 'CCTV-2'
    doc = publisher.build_channel_catalog([first, second], PolicyConfig(), rights([first, second]), NOW)
    publisher.publish_channel_catalog(doc, tmp_path)
    first.channel_name = 'CCTV-1 新名称'
    third = eligible(candidate_factory, 'https://c.example/live.m3u8')
    third.channel_id, third.channel_name = 'cctv-3', 'CCTV-3'
    new = publisher.build_channel_catalog([first, third], PolicyConfig(), rights([first, third]), NOW)
    publisher.publish_channel_catalog(new, tmp_path)
    changes = json.loads((tmp_path / 'catalog-manifest.json').read_text())['changes']
    assert changes == {'added': ['cctv-3'], 'removed': ['cctv-2'], 'modified': ['cctv-1']}

def test_shared_nonempty_golden_fixture_has_known_canonical_hash():
    document = json.loads(Path('schema/fixtures/channels-v1.json').read_text(encoding='utf-8'))
    publisher.validate_channel_catalog(document)
    source = document['channels'][0]['sources'][0]
    assert source['score'] == 87
    assert source['frameRate'] == 29.97
    assert source['availability'] == 0.333333
    assert source['videoCodec'] is None
    assert document['channels'][0]['name'] == '\u6d4b\u8bd5\u9891\u9053 \U0001f4fa'
    assert document['contentHash'] == '6c32f768aa9170baa13e80fbc24344a38193f5d775500239ea3d27412013fe1e'
    assert document['contentHash'] == catalog.catalog_content_hash(document['channels'])

@pytest.mark.parametrize('failure_code', ['ENVIRONMENT_LIMITED', 'HTTP_TIMEOUT', 'FIRST_BYTE_TIMEOUT'])
def test_catalog_retains_authorized_region_limited_backup_in_mixed_results(candidate_factory, failure_code):
    active = eligible(candidate_factory, 'https://active.example/live.m3u8')
    regional = eligible(candidate_factory, 'https://regional.example/live.m3u8')
    regional.history.append(CheckResult(checked_at=NOW + timedelta(minutes=1), success=False, failure_code=failure_code))
    regional.status = StreamStatus.COOLING
    document = publisher.build_channel_catalog([active, regional], PolicyConfig(), rights([active, regional]), NOW + timedelta(minutes=1))
    sources = document['channels'][0]['sources']
    assert [source['id'] for source in sources] == [active.stream_id, regional.stream_id]
    historical = sources[1]
    assert historical['environmentLimited'] is True
    assert historical['measurementStatus'] == 'historicalEnvironmentLimited'
    assert historical['score'] <= 40
    assert historical['resolution'] == '1920x1080'
    assert historical['lastSuccess'] == '2026-11-04T00:00:00Z'
    assert historical['lastChecked'] == '2026-11-04T00:01:00Z'


@pytest.mark.parametrize('failure_code', ['HTTP_FAILED', 'PLACEHOLDER_STREAM', 'MANUAL_REJECTION', 'NO_VIDEO_STREAM'])
def test_catalog_does_not_retain_known_invalid_sources_as_environment_unknown(candidate_factory, failure_code):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    stream.history.append(CheckResult(checked_at=NOW, success=False, failure_code=failure_code))
    document = publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)
    assert document['channels'] == []


def test_catalog_region_backup_requires_recent_real_media_success_and_still_valid_rights(candidate_factory):
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    stream.history.append(CheckResult(checked_at=NOW, success=False, failure_code='ENVIRONMENT_LIMITED'))
    assert publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW + timedelta(days=31))['channels'] == []
    assert publisher.build_channel_catalog([stream], PolicyConfig(), rights([]), NOW)['channels'] == []
    stream.history[-2].success = False
    stream.history[-3].success = False
    assert publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW)['channels'] == []

@pytest.mark.asyncio
async def test_pipeline_does_not_restore_previous_after_known_invalid_then_network_timeout(tmp_path, candidate_factory):
    import shutil
    import yaml
    from iptv_manager.discovery import DiscoveryResult
    from iptv_manager.pipeline import PipelineDependencies, PipelineOptions, run_pipeline
    stream = eligible(candidate_factory, 'https://a.example/live.m3u8')
    config = tmp_path / 'config'
    shutil.copytree('config', config)
    (config / 'usage_rights.yml').write_text(yaml.safe_dump(rights([stream])), encoding='utf-8')
    output = tmp_path / 'output'
    publisher.publish_channel_catalog(publisher.build_channel_catalog([stream], PolicyConfig(), rights([stream]), NOW), output)
    stream.history.append(CheckResult(checked_at=NOW, success=False, failure_code='HTTP_FAILED'))
    async def discover():
        return DiscoveryResult(candidates=[stream])
    async def probe_http(items, policy):
        return [CheckResult(checked_at=NOW, success=False, failure_code='HTTP_TIMEOUT') for _ in items]
    async def probe_media(item, policy):
        raise AssertionError('HTTP失败不执行媒体检测')
    await run_pipeline(PipelineOptions(config, tmp_path / 'data', output),
                       PipelineDependencies(discover, probe_http, probe_media, now=lambda: NOW))
    assert json.loads((output / 'channels.json').read_text(encoding='utf-8'))['channels'] == []
