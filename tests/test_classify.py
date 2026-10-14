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


@pytest.mark.parametrize('raw', ['CCTV-1 (720p)', 'CCTV-1 HD (1080p)', 'CCTV-1 [Geo-blocked]'])
def test_public_playlist_quality_annotations_do_not_hide_channel(raw, channel_rules):
    assert classify_channel(raw, channel_rules).channel_id == 'cctv-1'


def test_public_playlist_english_satellite_name_matches(channel_rules):
    assert classify_channel('Hunan TV (2160p)', channel_rules).channel_id == 'hunan-satellite'


@pytest.mark.parametrize('raw,expected', [
    ('台視 HD', 'tw-ttv'), ('民視新聞台', 'tw-ftv-news'),
    ('EBC News (東森新聞台) (1080p) [Not 24/7]', 'tw-ebc-news'),
    ('Taiwan Plus TV (1080p)', 'tw-taiwan-plus'),
    ('DaAi1 (大愛1) (720p)', 'tw-daai-1'),
    ('客家電視台', 'tw-hakka'), ('三立新聞', 'tw-set-news')])
def test_taiwan_source_names_match_explicit_aliases(raw, expected, channel_rules):
    assert classify_channel(raw, channel_rules).channel_id == expected


@pytest.mark.parametrize('raw,expected', [
    ('鏡新聞', 'tw-mirror-news'), ('三立iNEWS', 'tw-set-inews'),
    ('三立戲劇台', 'tw-set-drama'), ('三立綜合台', 'tw-set-variety'),
    ('中天娛樂', 'tw-cti-entertainment'), ('中天亞洲台', 'tw-cti-asia'),
    ('八大娛樂', 'tw-gtv-entertainment'), ('東森洋片台', 'tw-ebc-western-movie'),
    ('东森超视', 'tw-ebc-super'), ('緯來日本台', 'tw-videoland-japan'),
    ('緯來綜合台', 'tw-videoland-variety'), ('非凡商業台', 'tw-ustv-business'),
    ('年代MUCH', 'tw-much'), ('中視經典台', 'tw-ctv-classic'),
    ('中視菁采台', 'tw-ctv-bravo'), ('台視財經台', 'tw-ttv-finance'),
])
def test_more_mainstream_taiwan_channels_match_without_merging_distinct_channels(raw, expected, channel_rules):
    assert classify_channel(raw, channel_rules).channel_id == expected
