from datetime import UTC, datetime
import json

import pytest

from iptv_manager.models import ChannelCategory, ChannelRule


def test_trial_candidate_import_rejects_private_and_password_urls():
    from iptv_manager.taiwan_trial import extract_trial_candidates
    text = ('#EXTM3U\n#EXTINF:-1,台视\nhttps://public.example/live.m3u8\n'
            '#EXTINF:-1,台视\nhttp://192.168.1.2/live.m3u8\n'
            '#EXTINF:-1,台视\nhttp://user:password@public.example/live.m3u8\n'
            '#EXTINF:-1,台视\nhttps://public.example/live/user/password/123.m3u8\n'
            '#EXTINF:-1,台视\nhttps://public.example/get.php?username=a&password=b\n')
    rules = [ChannelRule(id='tw-ttv', name='台视', category=ChannelCategory.TAIWAN)]
    candidates = extract_trial_candidates(text, 'https://lists.example/tw.m3u', rules, set())
    assert [str(item.url) for item in candidates] == ['https://public.example/live.m3u8']


def test_trial_skips_malformed_url_without_losing_valid_channel():
    from iptv_manager.taiwan_trial import extract_trial_candidates
    rules = [ChannelRule(id='tw-ttv', name='台视', category=ChannelCategory.TAIWAN)]
    text = '#EXTM3U\n#EXTINF:-1,台视\nhttp://[broken/live.m3u8\n#EXTINF:-1,台视\nhttps://public.example/live.m3u8\n'
    candidates = extract_trial_candidates(text, 'https://lists.example/tw.m3u', rules, set())
    assert len(candidates) == 1


def test_trial_pins_highest_bitrate_1080_variant_instead_of_adaptive_master():
    from iptv_manager.taiwan_trial import highest_1080_variant
    text = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1920x1080\nlow.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080\nhigh.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1280x720\n720.m3u8\n')
    assert highest_1080_variant(text, 'https://cdn.example/hls/master.m3u8') == 'https://cdn.example/hls/high.m3u8'


def test_trial_master_without_1080_is_not_mislabeled():
    from iptv_manager.taiwan_trial import highest_1080_variant
    text = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n720.m3u8\n'
    assert highest_1080_variant(text, 'https://cdn.example/master.m3u8') is None


def test_trial_report_lists_missing_channels_and_does_not_overwrite_recommendations(tmp_path):
    from iptv_manager.taiwan_trial import write_trial_outputs
    rules = [ChannelRule(id='tw-ttv', name='台视', category=ChannelCategory.TAIWAN)]
    (tmp_path / 'combined.m3u').write_text('#EXTM3U\n旧推荐\n', encoding='utf-8')
    write_trial_outputs([], rules, tmp_path, datetime(2026, 10, 14, tzinfo=UTC), {})
    assert (tmp_path / 'combined.m3u').read_text(encoding='utf-8') == '#EXTM3U\n旧推荐\n'
    assert (tmp_path / 'taiwan-1080-trial.m3u').read_text(encoding='utf-8') == '#EXTM3U\n'
    assert '台视' in (tmp_path / 'taiwan-1080-trial.json').read_text(encoding='utf-8')


def test_trial_recovers_extra_comma_before_m3u_attributes():
    from iptv_manager.taiwan_trial import extract_trial_candidates
    text = '#EXTM3U\n#EXTINF:-1,tvg-id="台视" group-title="台湾,综合",台视\nhttps://public.example/live.m3u8\n'
    rules = [ChannelRule(id='tw-ttv', name='台视', category=ChannelCategory.TAIWAN)]
    candidates = extract_trial_candidates(text, 'https://lists.example/tw.m3u', rules, set())
    assert [item.channel_id for item in candidates] == ['tw-ttv']


@pytest.mark.asyncio
@pytest.mark.parametrize('frames,expected_success', [(34, True), (0, False)])
async def test_trial_distinguishes_recovered_startup_warning_from_audio_only_progress(candidate_factory, frames, expected_success):
    from iptv_manager.taiwan_trial import _measure
    from iptv_manager.probe_media import ProcessOutput
    payload = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264',
                            'width': 1920, 'height': 1080, 'avg_frame_rate': '30/1'}],
               'packets': [{'stream_index': 0, 'pts_time': str(t), 'size': '100000'} for t in (0, 1, 2, 3, 4)]}
    class Runner:
        async def run(self, args, timeout_seconds):
            if args[0] == 'ffprobe':
                return ProcessOutput(0, json.dumps(payload), '')
            return ProcessOutput(0, f'frame={frames}\nout_time_us=3003000\nprogress=end\n', 'non-existing PPS 0 referenced')
    candidate = candidate_factory('https://public.example/high.m3u8', '台视')
    if expected_success:
        result = await _measure(candidate, Runner())
        assert result['decode_warning'] is True
        assert result['decoded_frames'] == 34
    else:
        with pytest.raises(ValueError, match='DECODE_FAILED'):
            await _measure(candidate, Runner())


@pytest.mark.asyncio
async def test_trial_does_not_repeat_connection_attempts_to_known_unreachable_host(tmp_path, monkeypatch):
    import httpx
    from pathlib import Path
    from iptv_manager.taiwan_trial import run_taiwan_trial
    import iptv_manager.taiwan_trial as module
    async def public_dns(host):
        return True
    monkeypatch.setattr(module, '_resolve_public_host', public_dns)
    seed = 'https://lists.example/tw.m3u'
    source_file = tmp_path / 'sources.yml'
    source_file.write_text(f'sources:\n  - {seed}\n', encoding='utf-8')
    playlist = '#EXTM3U\n' + ''.join(f'#EXTINF:-1,台视\nhttps://dead.example/{i}.m3u8\n' for i in range(8))
    async def handler(request):
        if str(request.url) == seed:
            return httpx.Response(200, text=playlist)
        raise httpx.ConnectError('连接被拒绝', request=request)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    summary = await run_taiwan_trial(Path('config'), source_file, tmp_path / 'out')
    assert summary['filter_reasons'].get('HOST_UNREACHABLE', 0) >= 6
    assert summary['candidate_lines'] == 8


@pytest.mark.asyncio
async def test_trial_retains_stable_single_variant_entry_instead_of_temporary_context(candidate_factory):
    import httpx
    from iptv_manager.taiwan_trial import _pin_variant
    candidate = candidate_factory('https://public.example/live.m3u8', '中天新闻')
    def handler(request):
        text = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n/live.m3u8?hls_ctx=temporary\n'
                if not request.url.query else '#EXTM3U\n#EXTINF:6,\nsegment.ts\n')
        return httpx.Response(200, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pinned = await _pin_variant(candidate, client)
    assert str(pinned.url) == 'https://public.example/live.m3u8'


@pytest.mark.parametrize('host', ['2130706433', '127.1', '0x7f000001'])
def test_trial_rejects_ambiguous_loopback_ip_forms(host):
    from iptv_manager.taiwan_trial import _public_url
    assert _public_url(f'http://{host}/live.m3u8') is False


@pytest.mark.asyncio
async def test_trial_rejects_private_segment_and_key_before_media_probe(candidate_factory):
    import httpx
    from iptv_manager.taiwan_trial import _pin_variant
    candidate = candidate_factory('https://public.example/live.m3u8', '台视')
    text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="http://192.168.1.2/key"\n#EXTINF:6,\nhttp://192.168.1.2/segment.ts\n'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text))) as client:
        with pytest.raises(ValueError, match='NON_PUBLIC_URL'):
            await _pin_variant(candidate, client)


def test_trial_marks_low_bitrate_in_player_visible_channel_name(tmp_path, candidate_factory):
    from iptv_manager.taiwan_trial import write_trial_outputs
    candidate = candidate_factory('https://public.example/low.m3u8', '台视新闻')
    candidate.channel_id = 'tw-ttv-news'
    candidate.channel_name = '台视新闻台'
    candidate.category = ChannelCategory.TAIWAN
    line = {'candidate': candidate, 'width': 1920, 'height': 1080,
            'video_bitrate_estimate': 1_000_000, 'video_codec': 'h264'}
    write_trial_outputs([line], [], tmp_path, datetime(2026, 10, 14, tzinfo=UTC), {})
    text = (tmp_path / 'taiwan-1080-trial.m3u').read_text(encoding='utf-8')
    assert '1.0Mbps' in text
    assert '低码率' in text


@pytest.mark.asyncio
async def test_trial_cannot_hide_excluded_leaf_behind_stable_master(candidate_factory):
    import httpx
    from iptv_manager.taiwan_trial import _pin_variant
    from iptv_manager.normalize import make_stream_id
    candidate = candidate_factory('https://public.example/master.m3u8', '台视')
    def handler(request):
        text = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080\nbad.m3u8\n'
                if request.url.path.endswith('master.m3u8') else '#EXTM3U\n#EXTINF:6,\nsegment.ts\n')
        return httpx.Response(200, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='EXCLUDED'):
            await _pin_variant(candidate, client, {make_stream_id('https://public.example/bad.m3u8')})


@pytest.mark.asyncio
async def test_trial_guard_blocks_private_redirect_before_any_private_request(monkeypatch, candidate_factory):
    import httpx
    import iptv_manager.taiwan_trial as module
    async def public_dns(host):
        return True
    monkeypatch.setattr(module, '_resolve_public_host', public_dns)
    def handler(request):
        if request.url.host == '192.168.1.2':
            raise AssertionError('不能先访问内网再拒绝')
        return httpx.Response(302, headers={'location': 'http://192.168.1.2/live.m3u8'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                event_hooks={'request': [module._request_guard(set())]}) as client:
        with pytest.raises(ValueError, match='NON_PUBLIC_URL'):
            await module._pin_variant(candidate_factory('https://public.example/live.m3u8', '台视'), client)


@pytest.mark.asyncio
async def test_trial_guard_rejects_domain_resolving_to_private_network(monkeypatch):
    import httpx
    import iptv_manager.taiwan_trial as module
    async def private_dns(host):
        return False
    monkeypatch.setattr(module, '_resolve_public_host', private_dns)
    def handler(request):
        raise AssertionError('DNS 指向内网时不能发起请求')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                event_hooks={'request': [module._request_guard(set())]}) as client:
        with pytest.raises(ValueError, match='NON_PUBLIC_HOST'):
            await client.get('https://looks-public.example/live.m3u8')


@pytest.mark.asyncio
async def test_trial_resolves_proxy_fake_dns_without_allowing_private_real_address(monkeypatch):
    import asyncio
    import socket
    import iptv_manager.taiwan_trial as module
    async def fake_system_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.4.36', 0))]
    async def real_public_dns(host):
        return ['185.199.108.133']
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', fake_system_dns)
    monkeypatch.setattr(module, '_public_dns_addresses', real_public_dns, raising=False)
    assert await module._resolve_public_host('raw.githubusercontent.com') is True
    async def real_private_dns(host):
        return ['127.0.0.1']
    monkeypatch.setattr(module, '_public_dns_addresses', real_private_dns)
    assert await module._resolve_public_host('private-alias.example') is False


@pytest.mark.asyncio
async def test_failed_source_refresh_preserves_previous_trial_outputs(tmp_path, monkeypatch):
    import httpx
    from pathlib import Path
    import iptv_manager.taiwan_trial as module
    async def public_dns(host):
        return True
    monkeypatch.setattr(module, '_resolve_public_host', public_dns)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    source = tmp_path / 'sources.yml'
    source.write_text('sources:\n  - https://lists.example/tw.m3u\n', encoding='utf-8')
    old = '#EXTM3U\n旧试播\n'
    (tmp_path / 'taiwan-1080-trial.m3u').write_text(old, encoding='utf-8')
    with pytest.raises(ValueError, match='来源'):
        await module.run_taiwan_trial(Path('config'), source, tmp_path)
    assert (tmp_path / 'taiwan-1080-trial.m3u').read_text(encoding='utf-8') == old


@pytest.mark.asyncio
async def test_one_channel_timeout_does_not_cancel_shared_dns_for_other_channels(monkeypatch):
    import asyncio
    import httpx
    import iptv_manager.taiwan_trial as module
    ready = asyncio.Event()
    async def slow_public_dns(host):
        await ready.wait()
        return True
    monkeypatch.setattr(module, '_resolve_public_host', slow_public_dns)
    guard = module._request_guard(set())
    request = httpx.Request('GET', 'https://public.example/live.m3u8')
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(guard(request), 0.01)
    ready.set()
    try:
        await guard(request)
    except asyncio.CancelledError:
        pytest.fail('单条线路超时不应取消其他线路共享的 DNS 复核')


@pytest.mark.asyncio
async def test_trial_checks_external_audio_uri_on_master_before_media_probe(candidate_factory):
    import httpx
    from iptv_manager.taiwan_trial import _pin_variant
    candidate = candidate_factory('https://public.example/master.m3u8', '台视')
    def handler(request):
        if request.url.path.endswith('master.m3u8'):
            text = ('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="http://192.168.1.2/audio.m3u8"\n'
                    '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,AUDIO="audio"\nvideo.m3u8\n')
        else:
            text = '#EXTM3U\n#EXTINF:6,\nsegment.ts\n'
        return httpx.Response(200, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='NON_PUBLIC_URL'):
            await _pin_variant(candidate, client)
