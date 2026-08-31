from __future__ import annotations

from datetime import datetime, timezone
from http.client import responses
from pathlib import Path
from urllib.parse import urlsplit

import scrapy

from ..discovery import (
    DiscoveredUrl,
    historical_seeds,
    parse_robots_sitemaps,
    parse_sitemap_xml,
    root_seeds,
)
from ..access_policy import classify_access
from ..coverage import CoverageLedger, LifecycleEvent
from ..middlewares import OffsiteRequestError, RequestBudgetExceeded
from ..models import Capture, FetchError, RejectedUrl
from ..run_store import RunPaths
from ..url_policy import UrlPolicy


class PublicSitesSpider(scrapy.Spider):
    name = "public_sites"

    def __init__(
        self,
        hostnames: frozenset[str] | None = None,
        mode: str = "crawl",
        max_pages_per_host: int | None = None,
        history_index: Path | None = None,
        sitemap_max_depth: int = 5,
        playwright_hosts: frozenset[str] | None = None,
        playwright_auto_fallback: bool = True,
        pilot: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.hostnames = hostnames or frozenset()
        self.mode = mode
        self.max_pages_per_host = max_pages_per_host
        self.sitemap_max_depth = sitemap_max_depth
        self.playwright_hosts = playwright_hosts or frozenset()
        self.playwright_auto_fallback = playwright_auto_fallback
        self.pilot = pilot
        self.policy = UrlPolicy(self.hostnames)
        self._pages_scheduled = {hostname: 0 for hostname in self.hostnames}
        self.start_urls = [f"https://{host}/" for host in sorted(self.hostnames)]
        seeds = list(root_seeds(self.hostnames, pilot=pilot))
        if history_index is not None and not pilot:
            seeds.extend(historical_seeds(history_index, self.policy))
        self.seed_urls = tuple(seeds)
        self.coverage: CoverageLedger | None = None

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        paths = RunPaths.for_run(
            Path(crawler.settings.get("CRAWL_DATA_DIR", "data")),
            crawler.settings.get("CRAWL_RUN_ID", "manual"),
        )
        spider.coverage = CoverageLedger(paths.lifecycle)
        return spider

    async def start(self):
        for discovered in self.seed_urls:
            yield self._request(discovered)

    @staticmethod
    def handle_error(failure) -> FetchError | RejectedUrl:
        request = failure.request
        if isinstance(failure.value, OffsiteRequestError):
            redirect_urls = request.meta.get("redirect_urls", [])
            source_url = redirect_urls[-1] if redirect_urls else request.meta.get("parent_url", request.url)
            return RejectedUrl(
                source_url=source_url,
                discovered_url=request.url,
                reason="host_out_of_scope",
            )
        if isinstance(failure.value, RequestBudgetExceeded):
            return RejectedUrl(
                source_url=request.meta.get("parent_url", request.url),
                discovered_url=request.url,
                reason="request_budget",
            )
        if request.meta.get("oversize"):
            return RejectedUrl(
                source_url=request.meta.get("parent_url", request.url),
                discovered_url=request.url,
                reason="oversize",
            )
        return FetchError(
            url=request.url,
            error_type=type(failure.value).__name__,
            message=str(failure.value),
            parent_url=request.meta.get("parent_url"),
            discovery_source=request.meta.get("discovery_source", "unknown"),
            attempts=int(request.meta.get("retry_attempt", 0)) + 1,
            http_fallback_attempted=bool(request.meta.get("http_fallback")),
            playwright_attempted=bool(request.meta.get("playwright")),
        )

    def _request(self, discovered: DiscoveredUrl) -> scrapy.Request | RejectedUrl:
        hostname = (urlsplit(discovered.url).hostname or "").lower().rstrip(".")
        if (
            discovered.target_kind == "page"
            and not discovered.leaf
            and self.max_pages_per_host is not None
        ):
            scheduled = self._pages_scheduled.get(hostname, 0)
            if scheduled >= self.max_pages_per_host:
                return RejectedUrl(
                    source_url=discovered.parent_url or discovered.url,
                    discovered_url=discovered.url,
                    reason="page_limit",
                )
            self._pages_scheduled[hostname] = scheduled + 1
        priority_by_source = {
            "root": 1000,
            "robots": 900,
            "conventional_sitemap": 800,
            "robots_sitemap": 700,
            "sitemap": 600,
            "html": 500,
            "embedded_media": 400,
            "history": 100,
        }
        if discovered.target_kind == "sitemap":
            callback = self.parse_sitemap
        elif discovered.target_kind == "robots":
            callback = self.parse_robots
        else:
            callback = self.parse
        request = scrapy.Request(
            discovered.url,
            callback=callback,
            errback=self.handle_error,
            meta={
                "discovery_source": discovered.source,
                "parent_url": discovered.parent_url,
                "target_kind": discovered.target_kind,
                "leaf": discovered.leaf,
                "rendered": False,
                "root_seed": discovered.source == "root",
                "sitemap_depth": 0,
            },
            priority=priority_by_source.get(discovered.source, 0),
        )
        if self.coverage is not None:
            hostname = urlsplit(discovered.url).hostname or "unknown"
            if discovered.leaf and discovered.parent_url:
                hostname = urlsplit(discovered.parent_url).hostname or hostname
            event = {
                "url": discovered.url,
                "hostname": hostname,
                "source": discovered.source,
                "parent_url": discovered.parent_url,
                "target_kind": discovered.target_kind,
            }
            self.coverage.append(LifecycleEvent(phase="discovered", **event))
        return request

    @staticmethod
    def _capture(response: scrapy.http.Response, mime: str | None = None) -> Capture:
        content_type = mime or response.headers.get(b"Content-Type", b"").decode("latin-1")
        headers = tuple(
            (key.decode("latin-1"), b", ".join(values).decode("latin-1"))
            for key, values in response.headers.items()
        )
        reason = responses.get(response.status, "")
        request = response.request
        return Capture(
            response.url,
            datetime.now(timezone.utc),
            response.status,
            reason,
            headers,
            response.body,
            content_type,
            request.meta.get("discovery_source", "unknown") if request else "unknown",
            request.meta.get("parent_url") if request else None,
            request.meta.get("target_kind", "page") if request else "page",
            "playwright" if request and request.meta.get("playwright") else "http",
        )

    def _schedule(self, response: scrapy.http.Response, href: str, target_kind: str):
        absolute = response.urljoin(href)
        decision = self.policy.decide(
            absolute,
            target_kind=target_kind,
            source_url=response.url,
        )
        if not decision.accepted or not decision.canonical_url:
            if decision.reason:
                yield RejectedUrl(response.url, absolute, decision.reason)
            return
        yield self._request(
            DiscoveredUrl(
                decision.canonical_url,
                "html" if target_kind == "page" else "embedded_media",
                response.url,
                target_kind,
                decision.leaf,
            )
        )

    def parse_robots(self, response: scrapy.http.Response, **kwargs):
        yield self._capture(response, "text/plain")
        if self.mode == "preflight":
            return
        for sitemap_url in parse_robots_sitemaps(response.text, response.url):
            decision = self.policy.decide(sitemap_url, target_kind="sitemap")
            if decision.accepted and decision.canonical_url:
                yield self._request(
                    DiscoveredUrl(
                        decision.canonical_url,
                        "robots_sitemap",
                        response.url,
                        "sitemap",
                    )
                )
            elif decision.reason:
                yield RejectedUrl(response.url, sitemap_url, decision.reason)

    def parse_sitemap(self, response: scrapy.http.Response, **kwargs):
        yield self._capture(response, "application/xml")
        result = parse_sitemap_xml(response.body, response.url)
        if result.error:
            yield RejectedUrl(response.url, response.url, result.error)
            return
        if self.mode == "preflight":
            return
        current_depth = int(response.request.meta.get("sitemap_depth", 0)) if response.request else 0
        for discovered in result.discoveries:
            decision = self.policy.decide(
                discovered.url,
                target_kind=discovered.target_kind,
                source_url=response.url,
            )
            if not decision.accepted or not decision.canonical_url:
                if decision.reason:
                    yield RejectedUrl(response.url, discovered.url, decision.reason)
                continue
            next_discovered = DiscoveredUrl(
                decision.canonical_url,
                "sitemap",
                response.url,
                discovered.target_kind,
                decision.leaf,
            )
            if (
                discovered.target_kind == "sitemap"
                and current_depth >= self.sitemap_max_depth
            ):
                yield RejectedUrl(response.url, discovered.url, "sitemap_depth_limit")
                continue
            request = self._request(next_discovered)
            if isinstance(request, scrapy.Request):
                request.meta["sitemap_depth"] = current_depth + (1 if discovered.target_kind == "sitemap" else 0)
            yield request

    def parse(self, response: scrapy.http.Response, **kwargs):
        if self.max_pages_per_host is not None:
            current_host = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if response.request is not None and not response.request.meta.get("target_kind"):
                self._pages_scheduled[current_host] = self._pages_scheduled.get(current_host, 0) + 1
            else:
                self._pages_scheduled[current_host] = max(
                    self._pages_scheduled.get(current_host, 0), 1
                )
        content_type = response.headers.get(b"Content-Type", b"").decode("latin-1")
        mime = content_type.split(";", 1)[0].strip().lower()
        request = response.request
        if mime in {"text/html", "application/xhtml+xml"}:
            decision = classify_access(
                status=response.status,
                url=response.url,
                html=response.text,
                rendered=bool(request and request.meta.get("rendered")),
            )
            if decision.outcome in {"login_required", "captcha_blocked"}:
                yield RejectedUrl(response.url, response.url, decision.reason or "access_blocked")
                return
            if (
                decision.escalate_playwright
                and self.playwright_auto_fallback
                and not (request and request.meta.get("rendered"))
            ):
                rerender = response.request.replace(dont_filter=True)
                rerender.meta["render_with_playwright"] = True
                rerender.meta["rendered"] = True
                yield rerender
                return
            yield self._capture(response, content_type)
            if self.mode == "preflight" or (request and request.meta.get("leaf")):
                return
            for href in response.css("a::attr(href)").getall():
                yield from self._schedule(response, href, "page")
            for selector in ("img::attr(src)", "video::attr(src)", "source::attr(src)"):
                for href in response.css(selector).getall():
                    yield from self._schedule(response, href, "media")
            for selector in ("img::attr(srcset)", "source::attr(srcset)"):
                for srcset in response.css(selector).getall():
                    for candidate in srcset.split(","):
                        href = candidate.strip().split(None, 1)[0]
                        if href:
                            yield from self._schedule(response, href, "media")
            return
        if content_type and request and request.meta.get("target_kind") in {"page", "media"}:
            yield self._capture(response, content_type)
