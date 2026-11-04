import json
from pathlib import Path

import pytest


def row(url='https://media.example/one.m3u8', channel_id='cctv-1', raw_name='CCTV1'):
    return {'channel_id': channel_id, 'raw_name': raw_name, 'channel_name': 'CCTV-1 综合',
            'url': url, 'source_url': 'https://api.github.com/repos/example/tv/contents/live.m3u?ref=abc'}


def test_discovered_pool_only_adds_matching_public_requested_channels(tmp_path):
    from iptv_manager.tv_trial import load_discovered_candidates
    path = tmp_path / 'candidates.json'
    path.write_text(json.dumps([row(), row(), row('http://127.0.0.1/a.m3u8'),
        row('https://media.example/secret.m3u8?token=secret'),
        row(channel_id='cctv-1', raw_name='CCTV2'), row(channel_id='tw-ftv', raw_name='民视')]), encoding='utf-8')
    found = load_discovered_candidates(path)
    assert [(x['channel_id'], x['url']) for x in found] == [('cctv-1', 'https://media.example/one.m3u8')]
    assert found[0]['source_url'].startswith('https://api.github.com/')


def test_discovered_pool_missing_is_optional_but_invalid_pool_is_reported(tmp_path):
    from iptv_manager.tv_trial import load_discovered_candidates
    path = tmp_path / 'candidates.json'
    assert load_discovered_candidates(path) == []
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        load_discovered_candidates(path)


def test_candidate_budget_keeps_channel_coverage_and_limits_origin_aliases():
    from iptv_manager.tv_trial import budget_candidates
    candidates = [row(f'https://one.example/{i}.m3u8') for i in range(80)]
    candidates += [row('https://other.example/two.m3u8', 'cctv-2', 'CCTV2')]
    found = budget_candidates(candidates, maximum=2)
    assert [x['channel_id'] for x in found] == ['cctv-1', 'cctv-2']
    assert len(budget_candidates(candidates, maximum=100)) == 3


@pytest.mark.asyncio
async def test_trial_uses_discovered_pool_even_without_a_seed_list(tmp_path, monkeypatch):
    import iptv_manager.tv_trial as trial
    path = tmp_path / 'candidates.json'
    path.write_text(json.dumps([row()]), encoding='utf-8')
    config = tmp_path / 'sources.yml'
    config.write_text('sources: []\n', encoding='utf-8')
    checked = []
    async def collect(client, url, directory):
        checked.append(url)
        return {'sample_path': 'sample.ts', 'media_url': url}
    async def measure(path):
        return {'width': 1920, 'height': 1080, 'video_bitrate_estimate': 4000000}
    def write(lines, output_dir, now, summary):
        assert summary['discovered_pool_candidates'] == 1
        assert len(lines) == 1
        return {'published_channels': 1, 'published_lines': 1, 'missing_channels': []}
    monkeypatch.setattr(trial, 'collect_hls_sample', collect)
    monkeypatch.setattr(trial, 'measure_sample', measure)
    monkeypatch.setattr(trial, 'write_outputs', write)
    await trial.run(config, tmp_path / 'output', tmp_path / 'work', candidates_file=path)
    assert checked == ['https://media.example/one.m3u8']
