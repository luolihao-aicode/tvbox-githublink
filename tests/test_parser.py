from iptv_manager.models import SourceType
from iptv_manager.parser import parse_m3u


def test_parse_m3u_reads_name_group_and_url() -> None:
    text = (
        '\ufeff#EXTM3U\r\n'
        '#EXTINF:-1 tvg-id="cctv-1" group-title="央视",CCTV-1 综合\r\n'
        'https://a.example/live.m3u8\r\n'
    )

    item = parse_m3u(
        text,
        source_url="https://list.example/a.m3u",
        source_type=SourceType.COMMUNITY,
    )[0]

    assert item.raw_name == "CCTV-1 综合"
    assert str(item.url) == "https://a.example/live.m3u8"
    assert item.source_type is SourceType.COMMUNITY


def test_comma_inside_quoted_attribute_does_not_become_channel_name():
    text = '#EXTM3U\n#EXTINF:-1 user-agent="a,b" group-title="央视",CCTV-1 (720p)\nhttps://stream.example/live.m3u8\n'
    item = parse_m3u(text, 'https://list.example/a.m3u', SourceType.COMMUNITY)[0]
    assert item.raw_name == 'CCTV-1 (720p)'

