from html.parser import HTMLParser
from pathlib import Path


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.tags.append((tag, dict(attrs)))


def test_status_page_has_no_images_or_external_runtime_assets() -> None:
    parser = AssetParser()
    parser.feed(Path("web/index.html").read_text(encoding="utf-8"))

    assert all(tag != "img" for tag, _ in parser.tags)
    assert all(
        not attributes.get("src", "").startswith(("http://", "https://"))
        for _, attributes in parser.tags
    )
    assert all(
        not attributes.get("href", "").startswith(("http://", "https://"))
        for tag, attributes in parser.tags
        if tag == "link"
    )


def test_page_exposes_four_categories_and_subscription_links() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")

    for category in ("央视", "地方频道", "香港", "台湾"):
        assert f'data-category="{category}"' in html
    for playlist in ("official.m3u", "community.m3u", "combined.m3u", "backup.m3u"):
        assert playlist in html
