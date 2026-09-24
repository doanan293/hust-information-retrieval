from types import SimpleNamespace

from scrapy import signals
from scrapy.http import HtmlResponse, Request
from scrapy.signalmanager import SignalManager

from hust_crawler.crawl.adaptive import AdaptiveConcurrencyMiddleware, Observation


def _middleware() -> tuple[AdaptiveConcurrencyMiddleware, SimpleNamespace]:
    crawler = SimpleNamespace(
        settings={"HUST_TUNING": SimpleNamespace(max_per_host=4, max_concurrency=64)},
        engine=SimpleNamespace(
            downloader=SimpleNamespace(
                slots={"a.test": SimpleNamespace(concurrency=1, delay=0.25)},
            )
        ),
        stats=SimpleNamespace(inc_value=lambda *args, **kwargs: None),
    )
    crawler.signals = SignalManager(crawler)
    middleware = AdaptiveConcurrencyMiddleware.from_crawler(crawler)
    middleware._resource_sampler = SimpleNamespace(
        sample=lambda: SimpleNamespace(cpu_percent=0.0, ram_percent=0.0)
    )
    return middleware, crawler


def test_healthy_window_increases_matching_scrapy_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    for _ in range(20):
        middleware.process_response(request, HtmlResponse(request.url, status=200, request=request))
    middleware.reconcile(now=31.0)
    assert middleware._crawler.engine.downloader.slots["a.test"].concurrency == 2


def test_429_reduces_only_matching_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))
    assert middleware._crawler.engine.downloader.slots["a.test"].concurrency == 1


def test_429_retry_after_sets_host_cooldown() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(
        request,
        HtmlResponse(request.url, status=429, headers={b"Retry-After": b"7"}, request=request),
    )
    assert middleware._hosts["a.test"]._cooldown_until is not None
    assert middleware._crawler.engine.downloader.slots["a.test"].delay >= 6.9


def test_429_without_retry_after_applies_default_delay_to_scrapy_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")

    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))

    assert middleware._crawler.engine.downloader.slots["a.test"].delay >= 29.9


def test_response_downloaded_signal_applies_cooldown_before_middleware_response() -> None:
    middleware, crawler = _middleware()
    request = Request("https://a.test/")
    response = HtmlResponse(request.url, status=429, request=request)

    crawler.signals.send_catch_log(
        signal=signals.response_downloaded,
        request=request,
        response=response,
        spider=None,
    )

    assert middleware._crawler.engine.downloader.slots["a.test"].delay >= 29.9


def test_healthy_response_after_cooldown_restores_original_slot_delay() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))
    health = middleware._hosts["a.test"]
    recovered_at = health._cooldown_until + 0.1

    health.observe(Observation(at=recovered_at, latency=0.2, status=200, error=None))
    middleware.reconcile_host("a.test", request, now=recovered_at)

    assert middleware._crawler.engine.downloader.slots["a.test"].delay == 0.25


def test_captcha_response_applies_host_circuit_cooldown() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/article")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=b'<main><div class="hcaptcha"></div></main>',
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert middleware._crawler.engine.downloader.slots["a.test"].delay >= 299.9


def test_comment_form_captcha_does_not_open_host_circuit() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/article")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=(
            b'<div id="news-body"><h1>Article</h1><p>Public content.</p></div>'
            b'<div id="formcomment" class="comment-form">'
            b'<form action="/comment/post"><img class="captchaImg" '
            b'src="/index.php?scaptcha=captcha&amp;t=123"></form></div>'
        ),
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert middleware._crawler.engine.downloader.slots["a.test"].delay == 0.25


def test_public_contact_form_captcha_does_not_open_host_circuit() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/contact/")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=(
            b"<main><h1>Contact the faculty</h1>"
            b"<p>Our address, telephone number, and office hours are public.</p>"
            b'<form action="/contact/"><textarea name="message"></textarea>'
            b'<img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">'
            b"</form></main>"
        ),
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert middleware._crawler.engine.downloader.slots["a.test"].delay == 0.25


def test_playwright_captcha_cools_normal_and_browser_slots() -> None:
    middleware, crawler = _middleware()
    crawler.engine.downloader.slots["playwright:a.test"] = SimpleNamespace(
        concurrency=2,
        delay=0.5,
    )
    request = Request(
        "https://a.test/article",
        meta={"playwright": True, "rendered": True, "download_slot": "playwright:a.test"},
    )
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=b'<main><div class="cf-turnstile"></div></main>',
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert crawler.engine.downloader.slots["a.test"].concurrency == 1
    assert crawler.engine.downloader.slots["a.test"].delay >= 299.9
    assert crawler.engine.downloader.slots["playwright:a.test"].concurrency == 1
    assert crawler.engine.downloader.slots["playwright:a.test"].delay >= 299.9
