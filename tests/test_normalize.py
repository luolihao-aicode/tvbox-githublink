from iptv_manager.normalize import deduplicate_candidates, make_stream_id, normalize_url


def test_normalize_url_removes_default_port_and_fragment() -> None:
    assert normalize_url("HTTPS://Example.COM:443/live.m3u8#player") == (
        "https://example.com/live.m3u8"
    )


def test_stream_id_is_equal_for_equivalent_urls() -> None:
    assert make_stream_id("https://EXAMPLE.com:443/live.m3u8") == make_stream_id(
        "https://example.com/live.m3u8"
    )


def test_deduplicate_keeps_first_candidate(candidate_factory) -> None:
    first = candidate_factory("https://example.com/live.m3u8", "列表一")
    duplicate = candidate_factory("https://EXAMPLE.com:443/live.m3u8", "列表二")

    assert deduplicate_candidates([first, duplicate]) == [first]

