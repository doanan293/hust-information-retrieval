from __future__ import annotations

from urllib.parse import urlsplit

from scrapy.http import Request, Response

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.discovery_spider import DiscoverySpider
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator


class FakeState:
    def __init__(self) -> None:
        self.completed: set[str] = set()

    def is_complete(self, url: str) -> bool:
        return url in self.completed


class FakeDiscoveryWriter:
    def __init__(self) -> None:
        self.targets: set[str] = set()
        self.target_records: list[dict[str, object]] = []
        self.probes: list[dict[str, object]] = []
        self.errors: list[dict[str, object]] = []
        self.checkpoints: list[str] = []
        self.state = FakeState()

    def record_target(self, record: dict[str, object]) -> None:
        self.targets.add(str(record["url"]))
        self.target_records.append(record)

    def record_probe(self, record: dict[str, object]) -> None:
        self.probes.append(record)

    def write_error(self, record: dict[str, object]) -> None:
        self.errors.append(record)

    def checkpoint(self, url: str) -> None:
        self.checkpoints.append(url)
        self.state.completed.add(url)


def make_discovery_spider(
    hostnames: frozenset[str], exact_urls: tuple[str, ...] = ()
) -> tuple[DiscoverySpider, FakeDiscoveryWriter]:
    seeds = SeedSet(recursive_hostnames=hostnames, exact_urls=exact_urls, invalid=())
    allowed = hostnames | {
        (urlsplit(u).hostname or "").lower().rstrip(".") for u in exact_urls if urlsplit(u).hostname
    }
    config = CrawlerConfig(hostnames=allowed, contact="ops@example.com")
    coordinator = SitemapDiscoveryCoordinator(
        exact_hostnames=frozenset(
            (urlsplit(u).hostname or "").lower().rstrip(".")
            for u in exact_urls
            if urlsplit(u).hostname
        )
        - hostnames
    )
    writer = FakeDiscoveryWriter()
    spider = DiscoverySpider(
        seeds=seeds,
        config=config,
        coordinator=coordinator,
        writer=writer,  # type: ignore[arg-type]
    )
    return spider, writer


def sitemap_response(
    spider: DiscoverySpider,
    url: str,
    body: bytes,
    headers: dict[str, str] | None = None,
    status: int = 200,
) -> Response:
    req = Request(url=url, meta={"input_url": url, "response_purpose": "sitemap"})
    resp_headers = {"Content-Type": "application/xml; charset=utf-8"}
    if headers:
        resp_headers.update(headers)
    return Response(
        url=url,
        status=status,
        headers=resp_headers,
        body=body,
        request=req,
    )


def test_discovery_starts_only_robots_and_sitemap_probes() -> None:
    spider, _ = make_discovery_spider(hostnames=frozenset({"a.test"}))
    urls = {request.url for request in spider.start_requests()}
    assert urls == {
        "https://a.test/robots.txt",
        "https://a.test/sitemap.xml",
        "https://a.test/sitemap_index.xml",
        "https://a.test/sitemap-index.xml",
    }
    assert "https://a.test/" not in urls


def test_urlset_records_targets_without_fetching_them() -> None:
    spider, writer = make_discovery_spider(hostnames=frozenset({"a.test"}))
    response = sitemap_response(
        spider,
        "https://a.test/sitemap.xml",
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.test/article</loc></url></urlset>',
    )
    assert list(spider.parse_response(response)) == []
    assert writer.targets == {"https://a.test/article"}


def test_explicit_url_seed_records_target_immediately_without_request() -> None:
    spider, writer = make_discovery_spider(
        hostnames=frozenset(), exact_urls=("https://a.test/target",)
    )
    requests = list(spider.start_requests())
    assert requests == []
    assert writer.targets == {"https://a.test/target"}


def test_robots_declared_sitemap_schedules_child_sitemap() -> None:
    spider, _ = make_discovery_spider(hostnames=frozenset({"a.test"}))
    robots_req = Request(
        "https://a.test/robots.txt",
        meta={"input_url": "https://a.test/robots.txt", "response_purpose": "robots"},
    )
    resp = Response(
        url="https://a.test/robots.txt",
        status=200,
        headers={"Content-Type": "text/plain"},
        body=b"User-agent: *\nSitemap: https://a.test/custom_sitemap.xml\n",
        request=robots_req,
    )
    requests = list(spider.parse_response(resp))
    assert len(requests) == 1
    assert requests[0].url == "https://a.test/custom_sitemap.xml"
    assert requests[0].meta["response_purpose"] == "sitemap"


def test_nested_sitemap_index_schedules_child_sitemaps() -> None:
    spider, _ = make_discovery_spider(hostnames=frozenset({"a.test"}))
    resp = sitemap_response(
        spider,
        "https://a.test/sitemap_index.xml",
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.test/part1.xml</loc></sitemap></sitemapindex>',
    )
    requests = list(spider.parse_response(resp))
    assert len(requests) == 1
    assert requests[0].url == "https://a.test/part1.xml"
    assert requests[0].meta["response_purpose"] == "sitemap"


def test_cross_host_targets_are_rejected() -> None:
    spider, writer = make_discovery_spider(hostnames=frozenset({"a.test"}))
    resp = sitemap_response(
        spider,
        "https://a.test/sitemap.xml",
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://evil.test/attack</loc></url></urlset>',
    )
    list(spider.parse_response(resp))
    assert writer.targets == set()


def test_empty_discovery_produces_no_root_request() -> None:
    spider, _ = make_discovery_spider(hostnames=frozenset({"a.test"}))
    resp = sitemap_response(
        spider,
        "https://a.test/sitemap.xml",
        b"",
        status=404,
    )
    requests = list(spider.parse_response(resp))
    assert requests == []
    assert spider.coordinator.snapshot()["hosts"]["a.test"]["mode"] == "pending"


def test_https_failure_yields_one_http_probe_with_same_logical_url() -> None:
    spider, _ = make_discovery_spider(hostnames=frozenset({"a.test"}))
    req = Request(
        url="https://a.test/sitemap.xml",
        meta={
            "input_url": "https://a.test/sitemap.xml",
            "logical_url": "https://a.test/sitemap.xml",
            "response_purpose": "sitemap",
        },
    )
    retry = spider._http_retry(req)
    assert retry is not None
    assert retry.url == "http://a.test/sitemap.xml"
    assert retry.meta["logical_url"] == "https://a.test/sitemap.xml"
    assert retry.meta["http_fallback"] is True

    # No second retry
    assert spider._http_retry(retry) is None
