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

