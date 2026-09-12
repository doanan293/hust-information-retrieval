from __future__ import annotations

from email.utils import parsedate_to_datetime
import random
import logging
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

from twisted.internet import task

from scrapy import signals
from scrapy.exceptions import IgnoreRequest, StopDownload
from scrapy.http import Request, Response
from scrapy.settings.default_settings import RETRY_EXCEPTIONS
from scrapy.utils.defer import maybe_deferred_to_future
from scrapy.utils.misc import load_object
from scrapy.utils.response import response_status_message

from ..policies.access import classify_access
from ..policies.network import resolve_public_hostname, validate_public_web_url
from ..policies.url import UrlPolicy


_RETRYABLE_EXCEPTIONS = tuple(
    load_object(value) if isinstance(value, str) else value
    for value in RETRY_EXCEPTIONS
)


def _reactor_clock():
    from twisted.internet import reactor

    return reactor


class SafeRedirectMiddleware:
    redirect_statuses = {301, 302, 303, 307, 308}

    def __init__(self, max_redirects: int = 20) -> None:
        self.max_redirects = max_redirects

    @classmethod
    def from_crawler(cls, crawler):
        return cls(max_redirects=crawler.settings.getint("REDIRECT_MAX_TIMES", 20))

    def process_response(self, request: Request, response: Response):
        if response.status not in self.redirect_statuses:
            return response
        location = response.headers.get(b"Location")
        if not location:
            raise IgnoreRequest("redirect_without_location")
        target = urljoin(response.url, location.decode("latin1"))
        scope = request.meta.get("request_scope")
        if scope == "asset":
            decision = validate_public_web_url(target)
            if not decision.accepted or decision.canonical_url is None:
                raise IgnoreRequest(f"redirect_out_of_scope:{decision.reason or 'rejected'}")
            canonical_target = decision.canonical_url
        else:
            request_host = (urlsplit(request.url).hostname or "").lower().rstrip(".")
            hosts = frozenset(request.meta.get("allowed_hostnames", ())) or frozenset(
                {request_host}
            )
            decision = UrlPolicy(hosts).decide(target)
            if not decision.accepted or decision.canonical_url is None:
                raise IgnoreRequest(f"redirect_out_of_scope:{decision.reason or 'rejected'}")
            canonical_target = decision.canonical_url

        redirect_times = int(request.meta.get("redirect_times", 0)) + 1
        if redirect_times > self.max_redirects:
            raise IgnoreRequest("redirect_limit")

        meta = dict(request.meta)
        redirect_urls = tuple(meta.get("redirect_urls", ())) + (request.url,)
        meta["redirect_times"] = redirect_times
        meta["redirect_urls"] = redirect_urls
        redirect = request.replace(url=canonical_target, meta=meta, dont_filter=True)
        if redirect.url in redirect_urls:
            raise IgnoreRequest("redirect_loop")
        return redirect


ExactHostRedirectMiddleware = SafeRedirectMiddleware


class PublicAssetAddressMiddleware:
    def __init__(self, resolver=resolve_public_hostname) -> None:
        self.resolver = resolver

    @classmethod
    def from_crawler(cls, crawler):
        return cls(resolver=crawler.settings.get("HUST_PUBLIC_RESOLVER", resolve_public_hostname))

    async def process_request(self, request: Request, spider=None) -> None:
        if request.meta.get("request_scope") != "asset":
            return None
        decision = validate_public_web_url(request.url)
        if not decision.accepted or decision.hostname is None:
            raise IgnoreRequest(decision.reason or "unsafe_asset_url")
        port = 443 if urlsplit(request.url).scheme == "https" else 80
        timeout = getattr(getattr(spider, "config", None), "download_timeout_seconds", 5.0)
        await self.resolver(decision.hostname, port, timeout)
        return None


class PoliteRetryMiddleware:
    def __init__(
        self,
        *,
        clock=None,
        max_retries: int = 3,
        retry_statuses: set[int] | None = None,
        base_delay: float = 2.0,
        max_delay: float = 3600.0,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self.clock = clock
        self.stats = None
        self.max_retries = max_retries
        self.retry_statuses = retry_statuses or {408, 425, 429, 500, 502, 503, 504}
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter or (lambda delay: random.uniform(delay * 0.5, delay * 1.5))

    @classmethod
    def from_crawler(cls, crawler):
        config = crawler.settings.get("HUST_CONFIG")
        middleware = cls(
            max_retries=config.retry_times,
            retry_statuses=set(config.retry_statuses),
            base_delay=config.retry_backoff_base_seconds,
            max_delay=config.retry_max_delay_seconds,
        )
        middleware.stats = crawler.stats
        return middleware

    def _delay(self, response: Response | None, retry_count: int) -> float:
        if response is not None:
            value = response.headers.get(b"Retry-After")
            if value:
                raw = value.decode("latin1").strip()
                try:
                    return max(0.0, float(raw))
                except ValueError:
                    try:
                        from datetime import datetime, timezone
                        return max(0.0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
        exponential = min(self.max_delay, self.base_delay * (2**retry_count))
        return min(self.max_delay, max(0.0, self.jitter(exponential)))

    async def _retry(self, request: Request, response: Response | None, reason: str = "transport"):
        retry_count = int(request.meta.get("retry_times", 0))
        if retry_count >= self.max_retries:
            return response
        retry = request.copy()
        retry.dont_filter = True
        retry.meta["retry_times"] = retry_count + 1
        if response is not None:
            retry.meta["retry_reason"] = response_status_message(response.status)
        delay = self._delay(response, retry_count)
        logging.getLogger(__name__).info(
            "retry | host=%s | attempt=%s/%s | wait=%.2fs | reason=%s | url=%s",
            urlsplit(request.url).hostname, retry_count + 1, self.max_retries,
            delay, response.status if response is not None else reason, request.url[:240],
        )
        clock = self.clock if self.clock is not None else _reactor_clock()
        delayed = deferLater(clock, delay, lambda: retry)
        if self.stats is not None:
            self.stats.inc_value("hust/retry/count")
            self.stats.inc_value("hust/retry/waiting")
        try:
            return await maybe_deferred_to_future(delayed)
        finally:
            if self.stats is not None:
                self.stats.inc_value("hust/retry/waiting", -1)

    async def process_response(self, request: Request, response: Response):
        if response.status not in self.retry_statuses:
            return response
        return await self._retry(request, response)

    async def process_exception(self, request: Request, exception):
        if not isinstance(exception, _RETRYABLE_EXCEPTIONS):
            return None
        return await self._retry(request, None, type(exception).__name__)


def deferLater(clock, delay: float, callable, *args, **kwargs):
    if isinstance(clock, task.Clock):
        return task.deferLater(clock, delay, callable, *args, **kwargs)
    return task.deferLater(clock, delay, callable, *args, **kwargs)


class PlaywrightFallbackMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_response(self, request: Request, response: Response):
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1").lower()
        if "html" not in content_type:
            return response
        rendered = bool(request.meta.get("rendered") or request.meta.get("playwright"))
        decision = classify_access(
            status=response.status,
            url=response.url,
            html=response.text,
            rendered=rendered,
        )
        if decision.outcome != "public":
            raise IgnoreRequest(f"{decision.outcome}:{decision.reason or 'access_gate'}")
        if rendered:
            return response
        if not decision.use_playwright:
            return response
        meta = dict(request.meta)
        hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
        meta.update({
            "playwright": True,
            "rendered": True,
            "download_slot": f"playwright:{hostname}",
        })
        return request.replace(meta=meta, dont_filter=True)


class ContentOnlyHeadersGuard:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        crawler.signals.connect(instance.headers_received, signal=signals.headers_received)
        return instance

    def headers_received(self, headers, body_length: int, request: Request, spider) -> None:
        if request.meta.get("asset_mode") != "content-only":
            return
        raw = headers.get(b"Content-Type") or headers.get("Content-Type") or b""
        if isinstance(raw, list):
            raw = raw[0] if raw else b""
        if isinstance(raw, bytes):
            raw = raw.decode("latin1")
        mime = str(raw).partition(";")[0].strip().lower()
        if mime and mime not in {"text/html", "application/xhtml+xml"}:
            request.meta["header_stop_reason"] = "discovered_not_downloaded"
            raise StopDownload(fail=False)
