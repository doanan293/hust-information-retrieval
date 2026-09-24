from __future__ import annotations

from typing import Any, AsyncIterator, Iterator, Literal
from urllib.parse import urlsplit

import scrapy

from hust_crawler.config import CrawlerConfig
from hust_crawler.policies.url import canonicalize_url
from .content import normalize_url
from .discovery_writer import DiscoveryWriter
from .routing import route_response
from .seeds import SeedSet
from .sitemaps import SitemapDiscoveryCoordinator, SitemapParseError, parse_sitemap


class DiscoverySpider(scrapy.Spider):
    name = "discovery_spider"

    def __init__(
        self,
        *,
        seeds: SeedSet,
        config: CrawlerConfig,
        coordinator: SitemapDiscoveryCoordinator,
        writer: DiscoveryWriter,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.seeds = seeds
        self.config = config
        self.coordinator = coordinator
        self.writer = writer
        self._scheduled_urls: set[str] = set()

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for req in self.start_requests():
            yield req

    def start_requests(self) -> Iterator[scrapy.Request]:
        for raw_url in self.seeds.exact_urls:
            canonical, _, _ = canonicalize_url(raw_url, check_traps=False)
            if canonical:
                self.writer.record_target({"url": canonical, "discovered_from": "seed"})
                host = (urlsplit(canonical).hostname or "").lower().rstrip(".")
                if host and host not in self.seeds.recursive_hostnames:
                    self.coordinator.register_exact_host(host)

        for hostname in sorted(self.seeds.recursive_hostnames):
            robots_url = f"https://{hostname}/robots.txt"
            robots_req = self._probe_request(robots_url, purpose="robots")
            if robots_req is not None:
                yield robots_req

            for candidate in (
                f"https://{hostname}/sitemap.xml",
                f"https://{hostname}/sitemap_index.xml",
                f"https://{hostname}/sitemap-index.xml",
            ):
                self.coordinator.register_sitemap(hostname, candidate, required=False)
                req = self._probe_request(candidate, purpose="sitemap")
                if req is not None:
                    yield req

    def _probe_request(
        self,
        url: str,
        *,
        purpose: Literal["robots", "sitemap"],
        logical_url: str | None = None,
        discovered_from: str | None = None,
    ) -> scrapy.Request | None:
        canonical, _, _ = canonicalize_url(url, check_traps=False)
        target_url = canonical or url
        if target_url in self._scheduled_urls:
            return None
        self._scheduled_urls.add(target_url)

        if self.writer.state.is_complete(target_url):
            return None

        return scrapy.Request(
            target_url,
            callback=self.parse_response,
            errback=self.on_request_error,
            meta={
                "input_url": target_url,
                "logical_url": logical_url or target_url,
                "response_purpose": purpose,
                "discovery_source": "probe" if purpose == "robots" else "sitemap",
                "discovered_from": discovered_from,
                "handle_httpstatus_all": True,
            },
            dont_filter=True,
        )

    def _http_retry(self, request: scrapy.Request) -> scrapy.Request | None:
        if request.url.startswith("https://") and not request.meta.get("http_fallback"):
            http_url = f"http://{request.url[len('https://'):]}"
            if http_url in self._scheduled_urls:
                return None
            self._scheduled_urls.add(http_url)

            meta = dict(request.meta)
            meta["http_fallback"] = True
            meta["input_url"] = http_url
            return scrapy.Request(
                http_url,
                callback=self.parse_response,
                errback=self.on_request_error,
                meta=meta,
                dont_filter=True,
            )
        return None

    def parse_response(self, response: scrapy.Response) -> Iterator[scrapy.Request]:
        input_url = response.meta.get("input_url", response.url)
        logical_url = response.meta.get("logical_url", input_url)
        purpose = response.meta.get(
            "response_purpose",
            "robots" if response.url.endswith("/robots.txt") else "sitemap",
        )
        content_type = response.headers.get("Content-Type", b"").decode("latin1", errors="replace")
        route = route_response(
            url=response.url, status=response.status, content_type=content_type, purpose=purpose
        )
        hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")

        if route.action == "robots":
            encoding = getattr(response, "encoding", None) or "utf-8"
            for line in response.body.decode(encoding, errors="replace").splitlines():
                key, sep, val = line.partition(":")
                if sep and key.strip().lower() == "sitemap":
                    sitemap_url = normalize_url(val.strip(), response.url)
                    if sitemap_url:
                        shost = (urlsplit(sitemap_url).hostname or "").lower().rstrip(".")
                        if shost == hostname:
                            is_new = self.coordinator.register_sitemap(
                                hostname, sitemap_url, required=True
                            )
                            if is_new:
                                req = self._probe_request(
                                    sitemap_url, purpose="sitemap", discovered_from=response.url
                                )
                                if req is not None:
                                    yield req
            self.coordinator.complete_robots(hostname)
            self.writer.record_probe(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "complete",
                    "http_status": response.status,
                }
            )
            self.writer.checkpoint(input_url)

        elif route.action == "sitemap":
            try:
                parsed = parse_sitemap(response.body, response.url, content_type)
            except SitemapParseError as exc:
                self.writer.write_error(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "error": "invalid_sitemap",
                        "message": str(exc),
                    }
                )
                self.coordinator.complete_sitemap(
                    hostname, logical_url, result="failed", targets=0
                )
                return

            if parsed.kind == "index":
                for loc in parsed.locations:
                    shost = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if shost == hostname:
                        is_new = self.coordinator.register_sitemap(hostname, loc, required=True)
                        if is_new:
                            req = self._probe_request(
                                loc, purpose="sitemap", discovered_from=response.url
                            )
                            if req is not None:
                                yield req
                self.coordinator.complete_sitemap(
                    hostname, logical_url, result="success", targets=0
                )
            else:
                valid_targets = 0
                for loc in parsed.locations:
                    shost = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if shost == hostname:
                        canonical, _, _ = canonicalize_url(loc, check_traps=False)
                        if canonical:
                            self.coordinator.record_sitemap_target(hostname, canonical)
                            self.writer.record_target(
                                {"url": canonical, "discovered_from": response.url}
                            )
                            valid_targets += 1
                self.coordinator.complete_sitemap(
                    hostname, logical_url, result="success", targets=valid_targets
                )
            self.writer.record_probe(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "complete",
                    "http_status": response.status,
                }
            )
            self.writer.checkpoint(input_url)

        elif route.action == "optional_absent":
            if purpose == "robots":
                self.coordinator.complete_robots(hostname)
            elif purpose == "sitemap":
                self.coordinator.complete_sitemap(
                    hostname, logical_url, result="absent", targets=0
                )
            self.writer.record_probe(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "absent",
                    "http_status": response.status,
                }
            )
            self.writer.checkpoint(input_url)

        elif route.action == "http_error":
            retry = self._http_retry(response.request) if response.request else None
            if retry is not None:
                yield retry
                return
            if purpose == "robots":
                self.coordinator.complete_robots(hostname)
            elif purpose == "sitemap":
                self.coordinator.complete_sitemap(
                    hostname, logical_url, result="failed", targets=0
                )
            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "error": route.error or f"http_{response.status}",
                }
            )

        elif route.action in {"transient", "unknown"}:
            self.writer.write_error({"url": input_url, "error": "unsupported_content_type"})

    def on_request_error(self, failure: Any) -> Iterator[scrapy.Request] | None:
        request = failure.request
        retry = self._http_retry(request)
        if retry is not None:
            return iter([retry])

        input_url = request.meta.get("input_url", request.url)
        logical_url = request.meta.get("logical_url", input_url)
        purpose = request.meta.get("response_purpose", "sitemap")
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")

        if purpose == "robots":
            self.coordinator.complete_robots(hostname)
        elif purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, logical_url, result="failed", targets=0)

        self.writer.write_error(
            {"url": input_url, "error": "request_failed", "message": str(failure.value)}
        )
        return None
