from scrapy.http import HtmlResponse, Request

from hust_crawler.spiders.public_sites import PublicSitesSpider


def response(url: str, body: str) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        request=Request(url),
        body=body.encode(),
        encoding="utf-8",
        headers={b"Content-Type": [b"text/html"]},
    )


def test_fixture_traverses_multiple_levels_and_rejects_external_media() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))
    root = response(
        "https://example.com/",
        '<main>public article content with enough visible text to avoid shell fallback and prove traversal works</main>'
        '<a href="/child">child</a><img src="https://cdn.example.net/photo.jpg">',
    )
    discovered = list(spider.parse(root))
    child = next(item for item in discovered if isinstance(item, Request) and item.url.endswith("/child"))
    assert not [
        item for item in discovered
        if isinstance(item, Request) and "photo.jpg" in item.url
    ]

    grandchild = response(
        "https://example.com/child",
        '<main>public child article content with enough visible text</main>'
        '<a href="/grandchild">grandchild</a>',
    )
    nested = list(spider.parse(grandchild))
    assert any(isinstance(item, Request) and item.url.endswith("/grandchild") for item in nested)
    assert child.meta["discovery_source"] == "html"
