from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from scrapy import Request, signals
from scrapy.exceptions import DownloadCancelledError, IgnoreRequest, StopDownload
from twisted.internet.error import (
    ConnectError,
    ConnectionDone,
    ConnectionLost,
    ConnectionRefusedError,
    DNSLookupError,
    TCPTimedOutError,
    TimeoutError,
)
from twisted.web.client import ResponseNeverReceived

from .retry_policy import retry_delay
from .coverage import CoverageLedger, LifecycleEvent
from .run_store import RunPaths


class OffsiteRequestError(IgnoreRequest):
    pass


class RequestBudgetExceeded(IgnoreRequest):
    pass


def is_connection_or_tls_failure(exception: BaseException) -> bool:
    if isinstance(
        exception,
        (
            DNSLookupError,
            ConnectError,
            ConnectionRefusedError,
            ConnectionDone,
            ConnectionLost,
            TCPTimedOutError,
            TimeoutError,
            ResponseNeverReceived,
        ),
    ):
        return True
    module = type(exception).__module__.lower()
    return "openssl" in module or type(exception).__name__ in {"CertificateError", "SSLError"}


class HttpsRootFallbackMiddleware:
    def __init__(self, coverage: CoverageLedger | None = None) -> None:
        self.coverage = coverage

    @classmethod
    def from_crawler(cls, crawler):
        paths = RunPaths.for_run(
            Path(crawler.settings.get("CRAWL_DATA_DIR", "data")),
            crawler.settings.get("CRAWL_RUN_ID", "manual"),
        )
        return cls(CoverageLedger(paths.lifecycle))

    def process_response(self, request: Request, response):
        return response

    def process_exception(self, request: Request, exception: BaseException):
        if (
            request.meta.get("root_seed")
            and not request.meta.get("http_fallback")
            and request.url.startswith("https://")
            and is_connection_or_tls_failure(exception)
        ):
            if self.coverage is not None:
                self.coverage.append(
                    LifecycleEvent(
                        url=request.url,
                        phase="fallback",
                        hostname=(urlsplit(request.url).hostname or "unknown").lower().rstrip("."),
                        source=request.meta.get("discovery_source", "unknown"),
                        parent_url=request.meta.get("parent_url"),
                        target_kind=request.meta.get("target_kind", "page"),
                        reason="http_fallback",
                    )
                )
            fallback = request.replace(url="http://" + request.url.removeprefix("https://"))
            fallback.meta["http_fallback"] = True
            fallback.dont_filter = True
            return fallback
        return None


class ScopeDownloaderMiddleware:
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self.allowed_hosts = allowed_hosts

    @classmethod
    def from_crawler(cls, crawler):
        return cls(frozenset(crawler.settings.getlist("CRAWL_ALLOWED_HOSTS")))

    def process_request(self, request: Request):
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")
        if hostname not in self.allowed_hosts:
            raise OffsiteRequestError(f"host outside crawl scope: {hostname}")
        return None


class RequestBudgetMiddleware:
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        max_requests_per_host: int | None,
        stats=None,
    ) -> None:
        self.allowed_hosts = allowed_hosts
        self.max_requests_per_host = max_requests_per_host
        self.stats = stats
        self.attempts_by_host: dict[str, int] = {}

    @classmethod
    def from_crawler(cls, crawler):
        configured = crawler.settings.getint("CRAWL_MAX_REQUESTS_PER_HOST", 0)
        return cls(
            frozenset(crawler.settings.getlist("CRAWL_ALLOWED_HOSTS")),
            configured or None,
            crawler.stats,
        )

    def process_request(self, request: Request):
        if self.max_requests_per_host is None:
            return None
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")
        if hostname not in self.allowed_hosts:
            return None
        attempts = self.attempts_by_host.get(hostname, 0)
        if attempts >= self.max_requests_per_host:
            request.meta["request_budget_exceeded"] = True
            if self.stats is not None:
                self.stats.inc_value(f"request_budget/rejected/{hostname}")
            raise RequestBudgetExceeded(
                f"request budget exhausted for host: {hostname}"
            )
        self.attempts_by_host[hostname] = attempts + 1
        if self.stats is not None:
            self.stats.set_value(
                f"request_budget/attempts/{hostname}",
                attempts + 1,
            )
        request.meta["network_attempt"] = attempts + 1
        return None


class CrawlerRetryMiddleware:
    def __init__(
        self,
        max_attempts: int = 3,
        retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504),
        base_delay: float = 2.0,
        max_delay: float = 3600.0,
        jitter: float = 0.2,
    ) -> None:
        self.max_attempts = max_attempts
        self.retry_statuses = retry_statuses
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            max_attempts=crawler.settings.getint("CRAWL_RETRY_TIMES", 3),
            retry_statuses=tuple(crawler.settings.getlist("CRAWL_RETRY_STATUSES")),
            base_delay=crawler.settings.getfloat("CRAWL_RETRY_BACKOFF_BASE", 2.0),
            max_delay=crawler.settings.getfloat("CRAWL_RETRY_MAX_DELAY", 3600.0),
            jitter=crawler.settings.getfloat("CRAWL_RETRY_JITTER", 0.2),
        )

    async def _delayed_retry(self, request: Request, delay: float) -> Request:
        retry = request.copy()
        retry.meta["retry_attempt"] = int(request.meta.get("retry_attempt", 0)) + 1
        retry.dont_filter = True
        await asyncio.sleep(delay)
        return retry

    def _retry_delay(self, request: Request, status: int | None, headers) -> float | None:
        attempt = int(request.meta.get("retry_attempt", 0)) + 1
        return retry_delay(
            status,
            headers,
            attempt,
            datetime.now(timezone.utc),
            max_attempts=self.max_attempts,
            retry_statuses=self.retry_statuses,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            jitter=self.jitter,
        )

    async def process_response(self, request: Request, response):
        delay = self._retry_delay(request, response.status, response.headers)
        if delay is None:
            return response
        return await self._delayed_retry(request, delay)

    async def process_exception(self, request: Request, exception: BaseException):
        if isinstance(exception, IgnoreRequest):
            return None
        if isinstance(exception, DownloadCancelledError) or request.meta.get("oversize"):
            request.meta["oversize"] = True
            return None
        delay = self._retry_delay(request, None, {})
        if delay is None:
            return None
        return await self._delayed_retry(request, delay)


class DownloadSizeGuard:
    def __init__(self, max_response_bytes: int) -> None:
        self.max_response_bytes = max_response_bytes

    @classmethod
    def from_crawler(cls, crawler):
        instance = cls(crawler.settings.getint("DOWNLOAD_MAXSIZE"))
        crawler.signals.connect(instance.bytes_received, signal=signals.bytes_received)
        return instance

    def bytes_received(self, data: bytes, request: Request, spider) -> None:
        received = int(request.meta.get("downloaded_bytes", 0)) + len(data)
        request.meta["downloaded_bytes"] = received
        if self.max_response_bytes and received > self.max_response_bytes:
            request.meta["oversize"] = True
            raise StopDownload(fail=True)


class PlaywrightRoutingMiddleware:
    def __init__(self, allowed_hosts: frozenset[str], configured_hosts: frozenset[str] | None = None) -> None:
        self.allowed_hosts = allowed_hosts
        self.configured_hosts = configured_hosts or frozenset()

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            frozenset(crawler.settings.getlist("CRAWL_ALLOWED_HOSTS")),
            frozenset(crawler.settings.getlist("CRAWL_PLAYWRIGHT_HOSTS")),
        )

    def process_request(self, request: Request):
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")
        configured = self.configured_hosts
        if hostname in self.allowed_hosts and (request.meta.get("render_with_playwright") or hostname in configured):
            request.meta["playwright"] = True
        return None


def should_abort_browser_request(playwright_request) -> bool:
    return playwright_request.resource_type in {"image", "media"}
