"""大陆频道个人试播的安全与目录回归测试。"""
import json
from datetime import UTC, datetime

import httpx
import pytest

from iptv_manager.catalog import validate_channel_catalog


def test_extract_only_requested_channels_and_no_secrets():
    from iptv_manager.tv_trial import extract_candidates
    source = "https://lists.example/primary.m3u"
    text = ('#EXTM3U\n#EXTINF:-1,CCTV-1 综合 (1080p)\nhttps://media.example/one.m3u8\n'
            '#EXTINF:-1,CCTV-5+\nhttps://media.example/plus.m3u8\n'
            '#EXTINF:-1,CCTV-4K\nhttps://media.example/four-k.m3u8\n'
            '#EXTINF:-1,湖南卫视\nhttps://media.example/hunan.m3u8?token=secret\n'
            '#EXTINF:-1,北京卫视\nhttp://127.1/live.m3u8\n')
    found = extract_candidates(text, source)
    assert [(x['channel_id'], x['url']) for x in found] == [('cctv-1', 'https://media.example/one.m3u8')]


def test_trial_extracts_txt_without_accepting_fake_channel_names():
    from iptv_manager.tv_trial import extract_candidates
    found = extract_candidates('央视,#genre#\nCCTV10,http://media.example/ten.m3u8\n'
                               'CCTV1广告,http://media.example/ad.m3u8\n'
                               '江苏卫视,http://media.example/js.m3u8', 'https://lists.example/live.txt')
    assert [x['channel_id'] for x in found] == ['cctv-10', 'jiangsu-satellite']


def test_cctv8_label_cannot_hide_a_cctv8k_playback_id():
    from iptv_manager.tv_trial import extract_candidates
    text = '#EXTM3U\n#EXTINF:-1 tvg-name="CCTV8",CCTV8\nhttps://media.example/live.m3u8?id=cctv8k\n'
    assert extract_candidates(text, 'https://lists.example/live.m3u') == []


@pytest.mark.parametrize('url', [
    'http://[2409:8087::1]/live.m3u8',
    'https://hls.example/nn_live/nn_x64/dWlwPTEyNy4wLjAuMSZzPXNpZ25hdHVyZQ/one.m3u8',
])
def test_trial_rejects_ipv6_only_and_encoded_signature_paths(url):
    from iptv_manager.tv_trial import safe_url
    assert safe_url(url) is False


def test_empty_trial_refresh_preserves_previous_files(tmp_path):
    from iptv_manager.tv_trial import write_outputs
    previous = '{"old":"data"}\n'
    (tmp_path / 'tv-test.json').write_text(previous, encoding='utf-8')
    with pytest.raises(ValueError, match='保留'):
        write_outputs([], tmp_path, datetime(2026, 11, 4, tzinfo=UTC), {})
    assert (tmp_path / 'tv-test.json').read_text(encoding='utf-8') == previous


@pytest.mark.asyncio
async def test_trial_rejects_hls_key_or_private_reference_before_fetch(tmp_path):
    from iptv_manager.tv_trial import collect_hls_sample
    requested = []
    async def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, text='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://media.example/key"\n#EXTINF:6,\nhttp://192.168.1.2/segment.ts\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await collect_hls_sample(client, 'https://media.example/master.m3u8', tmp_path)
    assert requested == ['https://media.example/master.m3u8']


@pytest.mark.asyncio
async def test_public_entry_can_follow_normal_server_redirect_without_publishing_signature(tmp_path, monkeypatch):
    from iptv_manager.tv_trial import collect_hls_sample, report_line, safe_url
    import iptv_manager.taiwan_trial as safety
    async def public(host):
        return True
    monkeypatch.setattr(safety, '_resolve_public_host', public)
    async def handler(request):
        if request.url.path == '/entry.m3u8':
            return httpx.Response(302, headers={'location': 'https://cdn.example/media.m3u8?tm=123&key=server-issued'})
        if request.url.path == '/media.m3u8':
            return httpx.Response(200, text='#EXTM3U\n#EXTINF:6,\na.ts\n#EXTINF:6,\nb.ts\n#EXTINF:6,\nc.ts\n')
        return httpx.Response(200, content=b'G' * 188)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks={'request': [safety._request_guard(set())]}) as client:
        sample = await collect_hls_sample(client, 'https://media.example/entry.m3u8', tmp_path)
    assert safe_url(sample['media_url']) is False
    published = report_line({'url': sample['playback_url'], **sample})
    assert 'server-issued' not in json.dumps(published)
    assert published['server_redirect_query_removed'] is True
    assert published['url'] == 'https://media.example/entry.m3u8'


def test_trial_writer_has_valid_hash_missing_channels_and_explicit_rights_notice(tmp_path):
    from iptv_manager.tv_trial import write_outputs
    line = {'channel_id': 'cctv-1', 'url': 'https://media.example/one.m3u8',
            'source_url': 'https://lists.example/live.m3u', 'width': 1920, 'height': 1080,
            'video_codec': 'h264', 'frame_rate': '25/1', 'video_bitrate_estimate': 4000000,
            'decoded_frames': 75, 'decoded_seconds': 3, 'checked_at': '2026-11-04T00:00:00Z',
            'first_byte_seconds': 0.1, 'sample_bytes': 5000000, 'segments_checked': 3,
            'decode_warning': False, 'segment_urls': ['https://media.example/a.ts']}
    write_outputs([line], tmp_path, datetime(2026, 11, 4, tzinfo=UTC), {'candidates': 1, 'failures': []})
    catalog = json.loads((tmp_path / 'tv-test.json').read_text(encoding='utf-8'))
    validate_channel_catalog(catalog)
    assert len(catalog['channels']) == 1
    assert catalog['channels'][0]['group'] == '央视'
    assert isinstance(catalog['channels'][0]['sources'][0]['frameRate'], int)
    report = json.loads((tmp_path / 'tv-test-report.json').read_text(encoding='utf-8'))
    assert len(report['missing_channels']) == 13
    assert report['lines'][0]['content_verified'] is False
    assert report['distribution_authorization_confirmed'] is False
    assert '未确认分发授权' in report['notice']
    assert (tmp_path / 'tv-test.m3u').read_text(encoding='utf-8').count('https://media.example/one.m3u8') == 1


def test_origins_group_aliases_and_identical_media_instead_of_claiming_ip_independence():
    from iptv_manager.tv_trial import assign_origin_groups
    lines = [
        {'channel_id': 'cctv-1', 'url': 'http://one.example/c1.m3u8', 'segment_urls': ['http://cdn.example/a.ts'], 'segment_sha256': ['same']},
        {'channel_id': 'cctv-1', 'url': 'http://two.example/c1.m3u8', 'segment_urls': ['http://other.example/b.ts'], 'segment_sha256': ['same']},
        {'channel_id': 'cctv-2', 'url': 'http://two.example/c2.m3u8', 'segment_urls': ['http://other.example/c.ts'], 'segment_sha256': ['second']},
        {'channel_id': 'cctv-1', 'url': 'http://third.example/c1.m3u8', 'segment_urls': ['http://third.example/d.ts'], 'segment_sha256': ['unique']},
    ]
    assign_origin_groups(lines)
    assert lines[0]['origin_group'] == lines[1]['origin_group'] == lines[2]['origin_group']
    assert lines[0]['origin_group'] != lines[3]['origin_group']


@pytest.mark.parametrize('value,expected', [('0/0', None), ('25/1', 25), ('30000/1001', 29.97), (None, None)])
def test_unknown_frame_rate_does_not_destroy_an_otherwise_valid_trial(value, expected):
    from iptv_manager.tv_trial import measured_frame_rate
    assert measured_frame_rate(value) == expected
