import pytest

from hust_crawler.crawl.article import ArticleAsset
from hust_crawler.crawl.assets import (
    AssetPolicy,
    is_recognizable_binary,
    recognizable_asset,
)
from hust_crawler.crawl.options import CrawlOptions


@pytest.mark.parametrize("url,role,group", [
    ("https://cdn.test/a.svg", "inline_image", "image"),
    ("https://cdn.test/a.mp4", "media_reference", "media"),
    ("https://cdn.test/a.pdf", "document_attachment", "document"),
    ("https://cdn.test/a.odp", "document_attachment", "document"),
])
def test_all_schedules_supported_semantic_assets(url: str, role: str, group: str) -> None:
    asset = ArticleAsset(url=url, role=role, alt="", caption="", external=True)
    decision = AssetPolicy(CrawlOptions(assets="all")).consider(asset, "https://a.test/p")
    assert (decision.action, decision.group, decision.reason) == ("schedule", group, None)


def test_content_only_records_without_scheduling() -> None:
    asset = ArticleAsset("https://a.test/a.pdf", "document_attachment", "", "", False)
    decision = AssetPolicy(CrawlOptions()).consider(asset, "https://a.test/p")
    assert (decision.action, decision.reason) == ("record_only", "asset_mode_content_only")


@pytest.mark.parametrize("url", ["/app.js", "/site.css", "/font.woff2", "/frame.html"])
def test_nonsemantic_dependencies_are_rejected(url: str) -> None:
    assert recognizable_asset(f"https://a.test{url}") is None


@pytest.mark.parametrize("url", ["/dump.zip", "/installer.exe", "/font.woff2"])
def test_known_unsupported_binary_is_recognized_without_becoming_semantic(url: str) -> None:
    absolute = f"https://a.test{url}"
    assert is_recognizable_binary(absolute) is True
    assert recognizable_asset(absolute) is None


@pytest.mark.parametrize("mime,url,expected", [
    ("image/png", "https://x/a.bin", (True, "image", None)),
    ("application/octet-stream", "https://x/a.pdf", (True, "document", None)),
    ("text/html", "https://x/a.pdf", (False, None, "unexpected_asset_content")),
    ("application/zip", "https://x/a.zip", (False, None, "unsupported_asset_type")),
])
def test_final_response_validation(mime: str, url: str, expected: tuple[object, ...]) -> None:
    decision = AssetPolicy(CrawlOptions(assets="all")).validate_response(mime, url)
    assert (decision.persist, decision.group, decision.reason) == expected
