import asyncio
import inspect
from types import SimpleNamespace

from scrapy import Request
from scrapy.exceptions import StopDownload
from scrapy.http import Response
from twisted.web.client import ResponseNeverReceived
import pytest

import hust_crawler.middlewares as middlewares
from hust_crawler.middlewares import (
    DownloadSizeGuard,
    CrawlerRetryMiddleware,
    HttpsRootFallbackMiddleware,
    PlaywrightRoutingMiddleware,
    RequestBudgetExceeded,
    RequestBudgetMiddleware,
    ScopeDownloaderMiddleware,
    should_abort_browser_request,
)
from hust_crawler.coverage import CoverageLedger
from hust_crawler.spiders.public_sites import PublicSitesSpider


def test_playwright_is_enabled_only_for_explicit_allowed_host() -> None:
    middleware = PlaywrightRoutingMiddleware(
        frozenset({"js.example.com", "example.com"}),
        frozenset({"js.example.com"}),
    )
    request = Request("https://js.example.com/page")
    middleware.process_request(request)
    assert request.meta["playwright"] is True
    external = Request("https://cdn.example.net/script.js")
    middleware.process_request(external)
    assert "playwright" not in external.meta


def test_scope_middleware_blocks_external_redirect_request() -> None:
    middleware = middlewares.ScopeDownloaderMiddleware(frozenset({"example.com"}))
    redirected = Request(
        "https://outside.test/page",
        meta={"redirect_urls": ["https://example.com/"]},
    )

    with pytest.raises(middlewares.OffsiteRequestError):
        middleware.process_request(redirected)


def test_tls_failure_on_https_root_creates_http_fallback() -> None:
    request = Request("https://example.com/", meta={"root_seed": True})

    fallback = HttpsRootFallbackMiddleware().process_exception(
        request, ResponseNeverReceived([])
    )

    assert fallback.url == "http://example.com/"
    assert fallback.meta["http_fallback"] is True
    assert fallback.dont_filter is True


def test_tls_fallback_closes_https_lifecycle(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    request = Request(
        "https://example.com/",
        meta={"root_seed": True, "discovery_source": "root"},
    )

    fallback = HttpsRootFallbackMiddleware(ledger).process_exception(
        request, ResponseNeverReceived([])
    )

    assert fallback.url == "http://example.com/"
    assert [(item.url, item.phase) for item in ledger.events] == [
        ("https://example.com/", "fallback")
    ]


def test_access_status_never_falls_back_to_http() -> None:
    request = Request("https://example.com/")
    response = Response(request.url, status=403, request=request)

    assert HttpsRootFallbackMiddleware().process_response(request, response) is response


def test_downloader_hooks_do_not_require_spider_argument() -> None:
    assert list(inspect.signature(CrawlerRetryMiddleware.process_response).parameters) == [
        "self", "request", "response"
    ]
    assert list(inspect.signature(CrawlerRetryMiddleware.process_exception).parameters) == [
        "self", "request", "exception"
    ]


def test_retry_middleware_awaits_delay_without_twisted_reactor(monkeypatch) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("hust_crawler.middlewares.asyncio.sleep", fake_sleep)
    middleware = CrawlerRetryMiddleware(
        max_attempts=3,
        retry_statuses=(503,),
        base_delay=2,
        max_delay=60,
        jitter=0,
    )
    request = Request("https://example.com/")
    response = Response(request.url, status=503, request=request)

    retry = asyncio.run(middleware.process_response(request, response))

    assert delays == [1]
    assert retry.meta["retry_attempt"] == 1
    assert retry.dont_filter is True


def test_scope_middleware_blocks_external_media_leaf() -> None:
    middleware = ScopeDownloaderMiddleware(frozenset({"example.com"}))
    leaf = Request("https://cdn.test/a.jpg", meta={"target_kind": "media", "leaf": True})

    with pytest.raises(middlewares.OffsiteRequestError, match="cdn.test"):
        middleware.process_request(leaf)


def test_request_budget_counts_every_attempt_per_host() -> None:
    middleware = RequestBudgetMiddleware(
        frozenset({"example.com"}),
        max_requests_per_host=3,
    )
    first = Request("https://example.com/")
    retry = Request("https://example.com/", meta={"retry_attempt": 1}, dont_filter=True)
    rerender = Request("https://example.com/", meta={"playwright": True}, dont_filter=True)
    redirected = Request("https://example.com/redirected", dont_filter=True)

    assert middleware.process_request(first) is None
    assert middleware.process_request(retry) is None
    assert middleware.process_request(rerender) is None
    with pytest.raises(RequestBudgetExceeded):
        middleware.process_request(redirected)


def test_request_budget_is_disabled_for_full_crawl() -> None:
    middleware = RequestBudgetMiddleware(
        frozenset({"example.com"}),
        max_requests_per_host=None,
    )
    for index in range(20):
        assert middleware.process_request(Request(f"https://example.com/{index}")) is None


def test_streaming_size_guard_marks_request_before_stopping() -> None:
    request = Request("https://cdn.test/movie.mp4")
    guard = DownloadSizeGuard(max_response_bytes=5)

    with pytest.raises(StopDownload):
        guard.bytes_received(data=b"123456", request=request, spider=None)

    assert request.meta["oversize"] is True


def test_browser_aborts_heavy_assets_but_keeps_scripts() -> None:
    assert should_abort_browser_request(SimpleNamespace(resource_type="image"))
    assert should_abort_browser_request(SimpleNamespace(resource_type="media"))
    assert not should_abort_browser_request(SimpleNamespace(resource_type="script"))


def test_oversize_failure_becomes_terminal_gap() -> None:
    failure = SimpleNamespace(
        request=Request("https://cdn.test/movie.mp4", meta={"oversize": True}),
        value=StopDownload(fail=True),
    )

    assert PublicSitesSpider.handle_error(failure).reason == "oversize"
