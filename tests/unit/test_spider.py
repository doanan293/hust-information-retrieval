from types import SimpleNamespace

import pytest

from scrapy.http import HtmlResponse, Request, Response

import hust_crawler.models as models
from hust_crawler.cdxj import CdxjEntry
from hust_crawler.coverage import CoverageLedger
from hust_crawler.discovery import DiscoveredUrl
from hust_crawler.middlewares import OffsiteRequestError
from hust_crawler.spiders.public_sites import PublicSitesSpider


def html_response(body: bytes, url: str = "https://example.com/", meta: dict | None = None) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        request=Request(url, meta=meta or {}),
        body=body,
        encoding="utf-8",
        headers={b"Content-Type": b"text/html"},
    )


@pytest.mark.parametrize(
    ("content_type", "body"),
    [(b"image/png", b"\x89PNG\r\n"), (b"application/pdf", b"%PDF-1.7")],
)
def test_capture_accepts_binary_response(content_type: bytes, body: bytes) -> None:
    request = Request("https://example.com/file")
    response = Response(
        request.url,
        status=200,
        headers={b"Content-Type": content_type},
        body=body,
        request=request,
    )

    capture = PublicSitesSpider._capture(response)

    assert capture.reason == "OK"
    assert capture.payload == body


def test_capture_uses_empty_reason_for_unknown_status() -> None:
    request = Request("https://example.com/file")
    response = Response(request.url, status=599, body=b"", request=request)

    assert PublicSitesSpider._capture(response).reason == ""


def test_spider_extracts_only_allowed_links() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))
    response = HtmlResponse(
        url="https://example.com/",
        request=Request("https://example.com/"),
        body=b'<a href="/page">page</a><a href="https://outside.test/">out</a>',
        encoding="utf-8",
        headers={b"Content-Type": b"text/html; charset=utf-8"},
    )
    results = list(spider.parse(response))
    assert any(item.__class__.__name__ == "Capture" for item in results)
    requests = [item for item in results if isinstance(item, Request)]
    assert [request.url for request in requests] == ["https://example.com/page"]


def test_spider_extracts_pages_but_rejects_external_embedded_media() -> None:
    body = b'''<a href="/child">child</a><a href="/guide.pdf">pdf</a>
               <img src="https://cdn.test/photo.jpg">
               <video><source src="https://cdn.test/movie.mp4"></video>'''

    results = list(PublicSitesSpider(hostnames=frozenset({"example.com"})).parse(html_response(body)))
    requests = [item for item in results if isinstance(item, Request)]

    assert [
        (request.url, request.meta["target_kind"], request.meta["leaf"])
        for request in requests
    ] == [
        ("https://example.com/child", "page", False),
        ("https://example.com/guide.pdf", "page", False),
    ]


def test_child_response_discovers_grandchild() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))

    requests = [
        item for item in spider.parse(
            html_response(b'<a href="/grandchild">next</a>', "https://example.com/child")
        ) if isinstance(item, Request)
    ]

    assert [request.url for request in requests] == ["https://example.com/grandchild"]


def test_spider_never_submits_or_follows_forms_and_render_assets() -> None:
    response = html_response(
        b'''<form action="/search"><input name="q"></form>
               <link rel="stylesheet" href="/site.css"><script src="/app.js"></script>'''
    )

    requests = [
        item for item in PublicSitesSpider(hostnames=frozenset({"example.com"})).parse(response)
        if isinstance(item, Request)
    ]
    assert len(requests) == 1
    assert requests[0].meta["render_with_playwright"] is True


def test_spider_honors_per_host_page_limit() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}), max_pages_per_host=1)
    response = HtmlResponse(
        url="https://example.com/",
        request=Request("https://example.com/"),
        body=b'<a href="/page">page</a>',
        encoding="utf-8",
        headers={b"Content-Type": b"text/html"},
    )
    assert not [item for item in spider.parse(response) if isinstance(item, Request)]


def test_sitemap_pages_obey_page_limit() -> None:
    spider = PublicSitesSpider(
        hostnames=frozenset({"example.com"}),
        max_pages_per_host=1,
    )
    assert isinstance(
        spider._request(DiscoveredUrl("https://example.com/", "root")),
        Request,
    )
    sitemap_request = Request(
        "https://example.com/sitemap.xml",
        meta={"sitemap_depth": 0},
    )
    sitemap = HtmlResponse(
        url=sitemap_request.url,
        request=sitemap_request,
        body=(
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://example.com/one</loc></url>"
            b"<url><loc>https://example.com/two</loc></url></urlset>"
        ),
        encoding="utf-8",
        headers={b"Content-Type": b"application/xml"},
    )

    results = list(spider.parse_sitemap(sitemap))

    rejections = [item for item in results if isinstance(item, models.RejectedUrl)]
    assert [item.reason for item in rejections] == ["page_limit", "page_limit"]
    assert not [item for item in results if isinstance(item, Request)]


def test_pilot_does_not_load_historical_seeds(tmp_path) -> None:
    index = tmp_path / "latest.cdxj"
    entry = CdxjEntry(
        "https://example.com/old",
        "20260101000000",
        200,
        "text/html",
        "sha256:a",
        "old.warc.gz",
        0,
        1,
        "urn:uuid:a",
        "response",
    )
    index.write_text(entry.to_line() + "\n", encoding="utf-8")

    spider = PublicSitesSpider(
        hostnames=frozenset({"example.com"}),
        history_index=index,
        pilot=True,
    )

    assert "history" not in {seed.source for seed in spider.seed_urls}


def test_spider_never_schedules_more_than_per_host_page_limit() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}), max_pages_per_host=2)
    response = HtmlResponse(
        url="https://example.com/",
        request=Request("https://example.com/"),
        body=b'<a href="/one">one</a><a href="/two">two</a><a href="/three">three</a>',
        encoding="utf-8",
        headers={b"Content-Type": b"text/html"},
    )

    requests = [item for item in spider.parse(response) if isinstance(item, Request)]

    assert [request.url for request in requests] == ["https://example.com/one"]


def test_spider_converts_download_failure_to_error_item() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))
    failure = SimpleNamespace(
        request=Request("https://example.com/broken"),
        value=TimeoutError("request timed out"),
    )

    error = spider.handle_error(failure)

    assert error == models.FetchError(
        url="https://example.com/broken",
        error_type="TimeoutError",
        message="request timed out",
    )


def test_cross_host_links_consume_destination_host_limit() -> None:
    spider = PublicSitesSpider(
        hostnames=frozenset({"a.example", "b.example"}), max_pages_per_host=2
    )
    from_a = HtmlResponse(
        url="https://a.example/",
        request=Request("https://a.example/"),
        body=b'<a href="https://b.example/inbound">inbound</a>',
        encoding="utf-8",
        headers={b"Content-Type": b"text/html"},
    )
    from_b = HtmlResponse(
        url="https://b.example/",
        request=Request("https://b.example/"),
        body=b'<a href="/own">own</a>',
        encoding="utf-8",
        headers={b"Content-Type": b"text/html"},
    )

    inbound = [item for item in spider.parse(from_a) if isinstance(item, Request)]
    own = [item for item in spider.parse(from_b) if isinstance(item, Request)]

    assert [request.url for request in inbound + own] == ["https://b.example/inbound"]


def test_spider_logs_blocked_external_redirect_as_rejection() -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))
    failure = SimpleNamespace(
        request=Request(
            "https://outside.test/page",
            meta={"redirect_urls": ["https://example.com/"]},
        ),
        value=OffsiteRequestError("host outside crawl scope: outside.test"),
    )

    rejection = spider.handle_error(failure)

    assert rejection == models.RejectedUrl(
        source_url="https://example.com/",
        discovered_url="https://outside.test/page",
        reason="host_out_of_scope",
    )


def test_request_creation_records_discovery_but_not_scheduler_acceptance(tmp_path) -> None:
    spider = PublicSitesSpider(hostnames=frozenset({"example.com"}))
    spider.coverage = models_coverage = CoverageLedger(tmp_path / "lifecycle.jsonl")

    spider._request(DiscoveredUrl("https://example.com/page", "html"))

    assert [item.phase for item in models_coverage.events] == ["discovered"]
