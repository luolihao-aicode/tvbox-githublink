from pathlib import Path

import pytest

from iptv_manager.classify import classify_channel
from iptv_manager.config import load_config
from iptv_manager.models import ChannelCategory, ChannelRule


@pytest.fixture
def channel_rules():
    return load_config(Path("config")).channels


@pytest.mark.parametrize("raw", ["CCTV1", "CCTV-1 综合", "央视一套"])
def test_cctv1_aliases_match(raw: str, channel_rules) -> None:
    match = classify_channel(raw, channel_rules)

    assert match.channel_id == "cctv-1"
    assert match.category is ChannelCategory.CCTV


def test_mainstream_satellite_channel_matches(channel_rules) -> None:
    match = classify_channel("湖南卫视高清", channel_rules)

    assert match.channel_id == "hunan-satellite"
    assert match.category is ChannelCategory.SATELLITE


def test_municipal_channel_is_not_accepted_as_satellite(channel_rules) -> None:
    match = classify_channel("长沙新闻频道", channel_rules)

    assert match.channel_id is None
    assert match.failure_code == "UNKNOWN_CHANNEL"


def test_ambiguous_alias_is_rejected() -> None:
    rules = [
        ChannelRule(id="demo-a", name="演示甲", category="台湾", aliases=["演示"]),
        ChannelRule(id="demo-b", name="演示乙", category="香港", aliases=["演示"]),
    ]

    match = classify_channel("演示", rules)

    assert match.channel_id is None
    assert match.failure_code == "AMBIGUOUS_CHANNEL"
