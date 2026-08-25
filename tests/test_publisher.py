from datetime import UTC, datetime

import pytest

from iptv_manager.models import ChannelCategory, SourceType, StreamStatus
from iptv_manager.publisher import (
    OutputValidationError,
    PublishMode,
    build_status,
    publish_outputs,
    render_m3u,
)
from iptv_manager.scoring import ChannelSelection


@pytest.fixture
def selection(candidate_factory) -> ChannelSelection:
    primary = candidate_factory("https://stream.example/cctv1.m3u8", "CCTV1")
    primary.channel_id = "cctv-1"
    primary.channel_name = "CCTV-1 综合"
    primary.category = ChannelCategory.CCTV
    primary.source_type = SourceType.COMMUNITY
    primary.status = StreamStatus.ACTIVE
    primary.score = 92.5
    return ChannelSelection("cctv-1", primary, ())


def test_render_m3u_omits_logo_and_uses_chinese_group(selection) -> None:
    text = render_m3u([selection], PublishMode.COMBINED)

    assert text.startswith("#EXTM3U\n")
    assert 'group-title="央视"' in text
    assert 'tvg-name="CCTV-1 综合"' in text
    assert "tvg-logo" not in text


def test_status_contains_quality_and_node_measurement_notice(selection) -> None:
    status = build_status([selection], datetime(2026, 8, 25, tzinfo=UTC))

    assert status["channels"][0]["score"] == 92.5
    assert "GitHub Actions" in status["measurement_notice"]


def test_empty_publication_does_not_overwrite_existing_outputs(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    old_file = output / "combined.m3u"
    old_file.write_text("#EXTM3U\n旧订阅\n", encoding="utf-8")

    with pytest.raises(OutputValidationError, match="没有可发布频道"):
        publish_outputs([], output, datetime(2026, 8, 25, tzinfo=UTC))

    assert old_file.read_text(encoding="utf-8") == "#EXTM3U\n旧订阅\n"

