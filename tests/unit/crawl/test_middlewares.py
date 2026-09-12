import asyncio

import pytest
from twisted.internet import task

from types import SimpleNamespace

from scrapy.exceptions import IgnoreRequest, StopDownload
from scrapy.http import Headers, HtmlResponse, Request, Response

import hust_crawler.crawl.middlewares as middlewares
from hust_crawler.crawl.middlewares import (
    ContentOnlyHeadersGuard,
    ExactHostRedirectMiddleware,
    PlaywrightFallbackMiddleware,
    PoliteRetryMiddleware,
    PublicAssetAddressMiddleware,
    SafeRedirectMiddleware,
)


def test_redirect_outside_exact_hosts_raises_ignore_request() -> None:
    request = Request("https://a.test/", meta={"allowed_hostnames": frozenset({"a.test"})})
    response = Response("https://a.test/", status=302, headers={b"Location": b"https://outside.test/page"}, request=request)
    try:
        ExactHostRedirectMiddleware().process_response(request, response)
    except Exception as exc:
        assert "redirect_out_of_scope" in str(exc)
    else:
        raise AssertionError("out-of-scope redirect was accepted")


def test_redirect_cannot_cross_request_host_even_if_other_host_in_crawl() -> None:
    # A request on b.test with allowed_hostnames={b.test} cannot redirect to a.test
    request = Request("https://b.test/redirect", meta={"allowed_hostnames": frozenset({"b.test"})})
    response = Response("https://b.test/redirect", status=302, headers={b"Location": b"https://a.test/target"}, request=request)
    try:
        ExactHostRedirectMiddleware().process_response(request, response)
    except Exception as exc:
        assert "redirect_out_of_scope:host_out_of_scope" in str(exc)
    else:
        raise AssertionError("cross-host redirect between crawl hosts was accepted")


def test_redirect_loop_is_rejected_before_refetching_seen_url() -> None:
    middleware = ExactHostRedirectMiddleware()
    first = Request(
        "https://a.test/a",
        meta={"allowed_hostnames": frozenset({"a.test"})},
    )
    to_b = Response(
        first.url,
        status=302,
        headers={b"Location": b"/b"},
        request=first,
    )
    second = middleware.process_response(first, to_b)
    assert second.meta["redirect_times"] == 1
    assert second.meta["redirect_urls"] == ("https://a.test/a",)
    assert second.dont_filter is True
    back_to_a = Response(
        second.url,
        status=302,
        headers={b"Location": b"/a"},
        request=second,
    )

    with pytest.raises(IgnoreRequest, match="redirect_loop"):
        middleware.process_response(second, back_to_a)


def test_redirect_loop_normalizes_equivalent_target_before_comparing_history() -> None:
    middleware = ExactHostRedirectMiddleware()
    first = Request(
        "https://a.test/a%20b",
        meta={"allowed_hostnames": frozenset({"a.test"})},
    )
    to_c = Response(
        first.url,
        status=302,
        headers={b"Location": b"/c"},
        request=first,
    )
    second = middleware.process_response(first, to_c)
    back_to_equivalent_a = Response(
        second.url,
        status=302,
        headers={b"Location": b"/a b"},
        request=second,
    )

    with pytest.raises(IgnoreRequest, match="redirect_loop"):
        middleware.process_response(second, back_to_equivalent_a)


def test_redirect_chain_is_rejected_after_twenty_redirects() -> None:
    middleware = ExactHostRedirectMiddleware()
    request = Request(
        "https://a.test/20",
        meta={
            "allowed_hostnames": frozenset({"a.test"}),
            "redirect_times": 20,
            "redirect_urls": tuple(f"https://a.test/{index}" for index in range(20)),
        },
    )
    response = Response(
        request.url,
        status=302,
        headers={b"Location": b"/21"},
        request=request,
    )

    with pytest.raises(IgnoreRequest, match="redirect_limit"):
        middleware.process_response(request, response)


def test_retry_after_delays_rescheduled_request() -> None:
    clock = task.Clock()
    middleware = PoliteRetryMiddleware(clock=clock, max_retries=3, retry_statuses={429, 503}, base_delay=2, max_delay=60)
    request = Request("https://a.test/")
    response = Response(request.url, status=429, headers={b"Retry-After": b"7"}, request=request)
    async def exercise():
        pending = asyncio.create_task(middleware.process_response(request, response))
        await asyncio.sleep(0)
        clock.advance(7.0)
        return await pending

    retry = asyncio.run(exercise())
    assert retry.dont_filter is True
    assert retry.meta["retry_times"] == 1


def test_exponential_retry_delay_applies_injected_jitter() -> None:
    middleware = PoliteRetryMiddleware(
        max_retries=3,
        base_delay=2,
        max_delay=60,
        jitter=lambda delay: delay + 1.0,
    )

    assert middleware._delay(None, retry_count=2) == 9.0


def test_default_retry_uses_the_running_reactor_clock(monkeypatch) -> None:
    reactor_clock = task.Clock()
    monkeypatch.setattr(middlewares, "_reactor_clock", lambda: reactor_clock, raising=False)
    middleware = PoliteRetryMiddleware(
        max_retries=3,
        retry_statuses={503},
        base_delay=2,
        jitter=lambda delay: delay,
    )
    request = Request("https://a.test/")
    response = Response(request.url, status=503, request=request)

    async def exercise():
        pending = asyncio.create_task(middleware.process_response(request, response))
        await asyncio.sleep(0)
        reactor_clock.advance(2.0)
        return await asyncio.wait_for(pending, timeout=0.1)

    retry = asyncio.run(exercise())
    assert retry.dont_filter is True
    assert retry.meta["retry_times"] == 1


def test_exhausted_status_returns_terminal_response() -> None:
    middleware = PoliteRetryMiddleware(max_retries=3, retry_statuses={503})
    request = Request("https://a.test/", meta={"retry_times": 3})
    response = Response(request.url, status=503, request=request)
    assert asyncio.run(middleware.process_response(request, response)) is response


def test_exhausted_transport_exception_does_not_reschedule() -> None:
    middleware = PoliteRetryMiddleware(max_retries=3)
    request = Request("https://a.test/", meta={"retry_times": 3})
    assert asyncio.run(middleware.process_exception(request, OSError("boom"))) is None


def test_policy_rejection_is_not_retried() -> None:
    clock = task.Clock()
    middleware = PoliteRetryMiddleware(
        clock=clock,
        max_retries=3,
        base_delay=2,
        jitter=lambda delay: delay,
    )
    request = Request("https://a.test/article")

    async def exercise():
        pending = asyncio.create_task(
            middleware.process_exception(
                request,
                IgnoreRequest("captcha_blocked:interactive_captcha"),
            )
        )
        await asyncio.sleep(0)
        clock.advance(2.0)
        return await pending

    result = asyncio.run(exercise())

    assert result is None
    assert clock.getDelayedCalls() == []


def test_html_shell_is_rescheduled_once_with_playwright() -> None:
    request = Request("https://a.test/", meta={"allowed_hostnames": frozenset({"a.test"})})
    response = HtmlResponse(request.url, status=200, headers={b"Content-Type": b"text/html"}, body=b'<div id="app"></div><script src="app.js"></script>', request=request, encoding="utf-8")
    rerender = PlaywrightFallbackMiddleware().process_response(request, response)
    assert rerender.meta["playwright"] is True
    assert rerender.meta["rendered"] is True
    assert rerender.meta["download_slot"] == "playwright:a.test"
    assert rerender.dont_filter is True


def test_unrendered_http_denial_gets_one_browser_attempt() -> None:
    request = Request("https://a.test/")
    response = HtmlResponse(
        request.url,
        status=403,
        headers={b"Content-Type": b"text/html"},
        body=b"<h1>Forbidden</h1>",
        request=request,
        encoding="utf-8",
    )
    rerender = PlaywrightFallbackMiddleware().process_response(request, response)
    assert rerender.meta["playwright"] is True
    assert rerender.meta["rendered"] is True


def test_rendered_http_denial_is_terminal() -> None:
    request = Request(
        "https://a.test/",
        meta={"playwright": True, "rendered": True},
    )
    response = HtmlResponse(
        request.url,
        status=403,
        headers={b"Content-Type": b"text/html"},
        body=b"<h1>Forbidden</h1>",
        request=request,
        encoding="utf-8",
    )
    with pytest.raises(IgnoreRequest, match="access_denied:http_403"):
        PlaywrightFallbackMiddleware().process_response(request, response)


def test_captcha_response_is_terminal_instead_of_being_extracted() -> None:
    request = Request("https://a.test/article")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=b'<main><div class="g-recaptcha"></div></main>',
        request=request,
        encoding="utf-8",
    )

    with pytest.raises(IgnoreRequest, match="captcha_blocked:interactive_captcha"):
        PlaywrightFallbackMiddleware().process_response(request, response)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ('<main><div class="hcaptcha"></div></main>', "captcha_blocked"),
        ('<form action="/login"><input type="password"></form>', "login_required"),
    ],
)
def test_rendered_access_gate_is_terminal(body: str, reason: str) -> None:
    request = Request(
        "https://a.test/article",
        meta={"playwright": True, "rendered": True},
    )
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=body.encode(),
        request=request,
        encoding="utf-8",
    )

    with pytest.raises(IgnoreRequest, match=reason):
        PlaywrightFallbackMiddleware().process_response(request, response)


def test_asset_redirect_remains_asset_only_across_public_hosts() -> None:
    middleware = SafeRedirectMiddleware()
    request = Request("https://cdn.test/a", meta={"request_scope": "asset", "redirect_urls": ()})
    response = Response(request.url, status=302, headers={"Location": "https://store.test/a.png"}, request=request)
    redirected = middleware.process_response(request, response)
    assert redirected.meta["request_scope"] == "asset"
    assert redirected.url == "https://store.test/a.png"


def test_page_redirect_still_rejects_cross_host() -> None:
    middleware = SafeRedirectMiddleware()
    request = Request("https://a.test/p", meta={"request_scope": "page", "allowed_hostnames": {"a.test"}})
    response = Response(request.url, status=302, headers={"Location": "https://cdn.test/p"}, request=request)
    with pytest.raises(IgnoreRequest, match="redirect_out_of_scope"):
        middleware.process_response(request, response)


def test_public_asset_address_middleware_default_resolver() -> None:
    from hust_crawler.policies.network import resolve_public_hostname

    mw = PublicAssetAddressMiddleware()
    assert mw.resolver is resolve_public_hostname


def test_public_asset_address_middleware_process_request_success() -> None:
    called = []

    async def mock_resolver(hostname, port, timeout):
        called.append((hostname, port, timeout))

    mw = PublicAssetAddressMiddleware(resolver=mock_resolver)
    request = Request("https://cdn.test/image.png", meta={"request_scope": "asset"})

    class MockSpider:
        class config:
            download_timeout_seconds = 12.0

    asyncio.run(mw.process_request(request, MockSpider()))
    assert called == [("cdn.test", 443, 12.0)]


def test_public_asset_address_middleware_ignores_non_asset_request() -> None:
    called = []

    async def mock_resolver(hostname, port, timeout):
        called.append((hostname, port, timeout))

    mw = PublicAssetAddressMiddleware(resolver=mock_resolver)
    request = Request("https://cdn.test/image.png", meta={"request_scope": "page"})
    asyncio.run(mw.process_request(request, None))
    assert called == []


def test_public_asset_address_middleware_rejects_unsafe_asset_url() -> None:
    mw = PublicAssetAddressMiddleware()
    request = Request("http://127.0.0.1/image.png", meta={"request_scope": "asset"})
    with pytest.raises(IgnoreRequest, match="non_public_address"):
        asyncio.run(mw.process_request(request, None))


def test_content_only_stops_ambiguous_binary_at_headers() -> None:
    request = Request("https://a.test/download?id=1", meta={
        "asset_mode": "content-only",
        "request_scope": "page",
    })
    guard = ContentOnlyHeadersGuard()
    with pytest.raises(StopDownload) as exc:
        guard.headers_received(
            headers=Headers({"Content-Type": "application/pdf", "Content-Length": "999999"}),
            body_length=999999,
            request=request,
            spider=SimpleNamespace(),
        )
    assert exc.value.fail is False
