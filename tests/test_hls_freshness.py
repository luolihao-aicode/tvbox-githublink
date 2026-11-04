from datetime import UTC, datetime

import pytest


def test_query_token_change_does_not_make_frozen_video_live():
    from iptv_manager.hls_freshness import verify_live_progress
    before = '#EXTM3U\n#EXTINF:8,\na.ts?nonce=one\n'
    with pytest.raises(ValueError, match='FROZEN_HLS_PLAYLIST'):
        verify_live_progress(before, before.replace('one', 'two'))


def test_old_program_date_is_rejected_before_media_download():
    from iptv_manager.hls_freshness import verify_program_time
    with pytest.raises(ValueError, match='STALE_HLS_PROGRAM_TIME'):
        verify_program_time('#EXT-X-PROGRAM-DATE-TIME:2023-09-11T12:49:33+0800', now=datetime(2026, 11, 4, tzinfo=UTC))


def test_latest_program_timestamp_is_used_for_a_long_live_window():
    from iptv_manager.hls_freshness import verify_program_time
    text = '#EXT-X-PROGRAM-DATE-TIME:2026-11-01T00:00:00Z\na.ts\n#EXT-X-PROGRAM-DATE-TIME:2026-11-04T00:00:00Z\nb.ts'
    verify_program_time(text, now=datetime(2026, 11, 4, tzinfo=UTC))


@pytest.mark.asyncio
async def test_trial_rejects_frozen_manifest_before_downloading_segments(tmp_path, monkeypatch):
    import httpx
    from iptv_manager.tv_trial import collect_hls_sample
    requested = []
    async def no_wait(seconds):
        pass
    monkeypatch.setattr('iptv_manager.tv_trial.wait_for_playlist_refresh', no_wait)
    async def handler(request):
        requested.append(request.url.path)
        return httpx.Response(200, text='#EXTM3U\n#EXT-X-TARGETDURATION:8\n#EXTINF:8,\na.ts\n#EXTINF:8,\nb.ts\n#EXTINF:8,\nc.ts\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='FROZEN_HLS_PLAYLIST'):
            await collect_hls_sample(client, 'https://media.example/live.m3u8', tmp_path)
    assert requested == ['/live.m3u8', '/live.m3u8']


@pytest.mark.parametrize('after', [
    '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\nb.ts\na.ts\nc.ts',
    '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:9\na.ts\nb.ts\nold.ts',
    '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:11\nhttp://new-cdn.example/a.ts\nhttp://new-cdn.example/b.ts\nhttp://new-cdn.example/c.ts',
])
def test_reordering_rewinding_or_changing_only_cdn_is_not_live_progress(after):
    from iptv_manager.hls_freshness import verify_live_progress
    before = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\na.ts\nb.ts\nc.ts'
    with pytest.raises(ValueError):
        verify_live_progress(before, after)


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', ['#EXT-X-MEDIA:TYPE=SUBTITLES,URI="http://127.0.0.1/subs.m3u8"',
                                 '#EXT-X-KEY:METHOD=AES-128,URI="https://media.example/key"'])
async def test_refreshed_manifest_revalidates_every_reference_and_encryption(tmp_path, monkeypatch, extra):
    import httpx
    from iptv_manager.tv_trial import collect_hls_sample
    count = 0
    requested = []
    async def no_wait(seconds):
        pass
    monkeypatch.setattr('iptv_manager.tv_trial.wait_for_playlist_refresh', no_wait)
    async def handler(request):
        nonlocal count
        requested.append(request.url.path)
        count += 1
        text = '#EXTM3U\n#EXT-X-TARGETDURATION:8\n#EXT-X-MEDIA-SEQUENCE:' + str(10 + count)
        text += f'\n#EXTINF:8,\na{count}.ts\n#EXTINF:8,\nb{count}.ts\n#EXTINF:8,\nc{count}.ts\n'
        if count > 1:
            text += extra + '\n'
        return httpx.Response(200, text=text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match='UNSAFE_HLS_REFERENCE|ENCRYPTED_HLS_EXCLUDED'):
            await collect_hls_sample(client, 'https://media.example/live.m3u8', tmp_path)
    assert requested == ['/live.m3u8', '/live.m3u8']
