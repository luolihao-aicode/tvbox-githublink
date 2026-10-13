import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iptv_manager.discovery import DiscoveryResult
from iptv_manager.models import CheckResult
from iptv_manager.pipeline import (
    PipelineDependencies,
    PipelineOptions,
    run_pipeline,
)


def _successful_check(checked_at: datetime) -> CheckResult:
    return CheckResult(
        checked_at=checked_at,
        success=True,
        first_byte_seconds=0.8,
        transfer_bytes_per_second=2_000_000,
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
        bitrate=4_500_000,
        continuous_read=True,
    )


@pytest.mark.asyncio
async def test_pipeline_publishes_only_twice_verified_hd_source(
    tmp_path: Path, candidate_factory
) -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    candidate = candidate_factory("https://stream.example/cctv1.m3u8", "央视一套")
    existing = candidate.model_copy(update={"history": [_successful_check(now - timedelta(days=1))]})
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "candidates.json").write_text(
        json.dumps([existing.model_dump(mode="json")], ensure_ascii=False), encoding="utf-8"
    )

    async def discover():
        return DiscoveryResult(candidates=[candidate])

    async def probe_http(candidates, policy):
        return [_successful_check(now) for _ in candidates]

    async def probe_media(item, policy):
        return _successful_check(now)

    result = await run_pipeline(
        PipelineOptions(
            config_dir=Path("config"),
            data_dir=data_dir,
            output_dir=tmp_path / "output",
            dry_run=False,
        ),
        PipelineDependencies(
            discover=discover,
            probe_http=probe_http,
            probe_media=probe_media,
            now=lambda: now,
        ),
    )

    assert [item.channel_id for item in result.selections] == ["cctv-1"]
    assert (tmp_path / "output" / "combined.m3u").is_file()
    persisted = json.loads((data_dir / 'candidates.json').read_text(encoding='utf-8'))
    assert persisted[0]['status'] == 'active'


@pytest.mark.asyncio
async def test_first_success_is_saved_without_publishing_empty_playlist(
    tmp_path: Path, candidate_factory
) -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    candidate = candidate_factory("https://stream.example/cctv1.m3u8", "央视一套")

    async def discover():
        return DiscoveryResult(candidates=[candidate])

    async def probe_http(candidates, policy):
        return [_successful_check(now)]

    async def probe_media(item, policy):
        return _successful_check(now)

    result = await run_pipeline(
        PipelineOptions(
            config_dir=Path("config"),
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "output",
        ),
        PipelineDependencies(discover, probe_http, probe_media, now=lambda: now),
    )

    assert result.selections == []
    assert (tmp_path / "data" / "candidates.json").is_file()
    assert not (tmp_path / "output" / "combined.m3u").exists()


@pytest.mark.asyncio
async def test_empty_discovery_persists_failure_report_without_overwriting_playlist(tmp_path):
    from iptv_manager.discovery import DiscoveryIssue

    async def discover():
        return DiscoveryResult(issues=[DiscoveryIssue('https://api.github.com/search/code',
                                                     'GITHUB_SEARCH_FAILED', 'HTTP 403')])
    async def http_probe(candidates, policy):
        return []
    async def media_probe(candidate, policy):
        raise AssertionError('没有候选时不得调用媒体检测')
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'combined.m3u').write_text('#EXTM3U\n旧频道\n', encoding='utf-8')
    await run_pipeline(PipelineOptions(Path('config'), tmp_path / 'data', output),
                       PipelineDependencies(discover, http_probe, media_probe))
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['discovered_sources'] == 0
    assert report['issues'][0]['code'] == 'GITHUB_SEARCH_FAILED'
    assert report['published_channels'] == 0
    assert (output / 'combined.m3u').read_text(encoding='utf-8') == '#EXTM3U\n旧频道\n'


@pytest.mark.asyncio
async def test_taiwan_only_update_keeps_other_channels_history_unchanged(tmp_path, candidate_factory):
    from iptv_manager.models import ChannelCategory
    now = datetime(2026, 10, 14, tzinfo=UTC)
    cctv = candidate_factory('https://stream.example/cctv.m3u8', 'CCTV1')
    cctv.history = [_successful_check(now - timedelta(days=1)), _successful_check(now)]
    cctv.channel_id, cctv.channel_name, cctv.category = 'cctv-1', 'CCTV-1 综合', ChannelCategory.CCTV
    taiwan = candidate_factory('https://stream.example/taiwan.m3u8', '台視')
    taiwan.history = [_successful_check(now - timedelta(days=1))]
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'candidates.json').write_text(json.dumps([x.model_dump(mode='json') for x in [cctv, taiwan]]), encoding='utf-8')
    async def discover():
        return DiscoveryResult(candidates=[taiwan])
    async def http_probe(candidates, policy):
        assert [x.raw_name for x in candidates] == ['台視']
        return [_successful_check(now)]
    async def media_probe(item, policy):
        return _successful_check(now)
    result = await run_pipeline(PipelineOptions(Path('config'), data, tmp_path / 'output',
                                               categories=(ChannelCategory.TAIWAN,)),
                                PipelineDependencies(discover, http_probe, media_probe, now=lambda: now))
    assert {x.channel_id for x in result.selections} == {'cctv-1', 'tw-ttv'}
    assert len(next(x for x in result.candidates if x.channel_id == 'cctv-1').history) == 2
    assert len(next(x for x in result.candidates if x.channel_id == 'tw-ttv').history) == 2


@pytest.mark.asyncio
async def test_media_checks_run_with_bounded_parallelism(tmp_path, candidate_factory):
    import asyncio
    now = datetime(2026, 10, 14, tzinfo=UTC)
    items = [candidate_factory(f'https://host{index}.example/live.m3u8', '台视') for index in range(6)]
    active, maximum = 0, 0
    async def discover():
        return DiscoveryResult(candidates=items)
    async def http_probe(candidates, policy):
        return [_successful_check(now) for item in candidates]
    async def media_probe(item, policy):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _successful_check(now)
    await run_pipeline(PipelineOptions(Path('config'), tmp_path / 'data', tmp_path / 'output', dry_run=True),
                       PipelineDependencies(discover, http_probe, media_probe, now=lambda: now))
    assert maximum == 4
