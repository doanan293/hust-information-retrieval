from __future__ import annotations

from collections import defaultdict
import hashlib
from types import SimpleNamespace
from typing import Any, Iterator

from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request, Response

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.article import ArticleAsset
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import Candidate, FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator
from hust_crawler.crawl.spider import UnifiedSpider


class FakeState:
    def __init__(self) -> None:
        self.completed: set[str] = set()
        self.articles: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.discovered: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.url_records: dict[str, dict[str, Any]] = {}
        self.content_owners: dict[str, str] = {}
        self.route_families: dict[str, Any] = {}
        self._asset_referrers: dict[str, set[tuple[str, str]]] = defaultdict(set)

    def is_complete(self, url: str) -> bool:
        return url in self.completed

    def checkpoint(self, url: str) -> None:
        self.completed.add(url)

    def merge_url(self, record: dict[str, Any], *, completed: bool = False) -> dict[str, Any]:
        url = str(record["url"])
        existing = self.url_records.get(url, {})
        merged = dict(existing)
        merged.update({k: v for k, v in record.items() if v is not None})
        self.url_records[url] = merged
        if completed or merged.get("completed"):
            self.completed.add(url)
        return merged

    def complete_url(
        self,
        disposition: dict[str, Any],
        *,
        article: dict[str, Any] | None = None,
        file: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        if article is not None:
            self.articles.append(article)
        if file is not None:
            self.files.append(file)
        if error is not None:
            self.errors.append(error)
        self.merge_url(disposition, completed=True)

    def put_article(self, record: dict[str, Any]) -> None:
        self.articles.append(record)

    def put_file(self, record: dict[str, Any]) -> None:
        self.files.append(record)

    def put_error(self, record: dict[str, Any]) -> None:
        self.errors.append(record)

    def claim_content(self, content_id: str, url: str) -> str:
        if content_id not in self.content_owners:
            self.content_owners[content_id] = url
        return self.content_owners[content_id]

    def add_asset_referrer(self, url: str, page_url: str, role: str) -> None:
        self._asset_referrers[url].add((page_url, role))

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        return tuple(sorted({page for page, _ in self._asset_referrers.get(url, ())}))

    def put_route_family(self, state: Any) -> None:
        self.route_families[state.family_key] = state

    def iter_route_families(self) -> Iterator[Any]:
        return iter(self.route_families.values())


class FakeCrawlWriter:
    def __init__(self, state: FakeState, options: CrawlOptions) -> None:
        self.state = state
        self.options = options
        self.articles: list[dict[str, Any]] = state.articles
        self.files: list[dict[str, Any]] = state.files
        self.errors: list[dict[str, Any]] = state.errors
        self.discovered: list[dict[str, Any]] = state.discovered
        self.skipped: list[dict[str, Any]] = state.skipped
        self.url_records = state.url_records
        self.checkpoints: list[str] = []
        self.next_duplicate_of: str | None = None

    @property
    def discovered_assets(self) -> list[dict[str, Any]]:
        return [r for r in self.discovered if r.get("status") == "discovered_not_downloaded"]

    def record_scheduled(self, record: dict[str, Any]) -> None:
        self.state.merge_url(record, completed=False)

    def record_discovered(self, record: dict[str, Any], *, completed: bool = True) -> None:
        self.discovered.append(record)
        self.state.merge_url(record, completed=completed)

    def record_skipped(self, record: dict[str, Any]) -> None:
        self.skipped.append(record)
        self.state.merge_url(record, completed=True)

    def record_absent(self, record: dict[str, Any]) -> None:
        self.discovered.append(record)
        self.state.complete_url(record)

    def write_article(self, record: dict[str, Any]) -> dict[str, Any]:
        text = str(record.get("text") or "").strip()
        content_id = record.get("content_id") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec = dict(record)
        rec["content_id"] = content_id
        if self.next_duplicate_of is not None:
            rec["duplicate_of"] = self.next_duplicate_of
            self.next_duplicate_of = None
        self.state.complete_url({"url": record["url"], "status": "extracted"}, article=rec)
        return rec

    def put_article(self, record: dict[str, Any]) -> None:
        self.write_article(record)

    def write_file(
        self,
        url: str,
        final_url: str,
        content_type: str,
        body: bytes,
        *,
        asset_group: str = "document",
        asset_role: str | None = None,
        referring_page: str | None = None,
    ) -> dict[str, Any]:
        rec = {
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "size": len(body),
            "asset_group": asset_group,
            "asset_role": asset_role,
            "referring_page": referring_page,
        }
        self.state.complete_url(
            {"url": url, "final_url": final_url, "status": "file_saved", "asset_role": asset_role},
            file=rec,
        )
        return rec

    def write_error(self, record: dict[str, Any]) -> None:
        self.state.complete_url(
            {
                "url": record["url"],
                "status": "failed",
                "reason": record.get("reason", record.get("error", "error")),
            },
            error=record,
        )

    def checkpoint(self, url: str) -> None:
        self.checkpoints.append(url)
        self.state.checkpoint(url)


def make_spider(
    recursive_hostnames: frozenset[str] | None = None,
    exact_urls: tuple[str, ...] = (),
    assets: str = "content-only",
    resume_records: tuple[dict[str, Any], ...] = (),
) -> tuple[UnifiedSpider, FakeCrawlWriter]:
    if recursive_hostnames is None:
        recursive_hostnames = frozenset() if exact_urls else frozenset({"a.test"})
    seeds = SeedSet(recursive_hostnames, exact_urls, ())
    options = CrawlOptions(assets=assets)  # type: ignore[arg-type]
    config = CrawlerConfig(
        hostnames=seeds.allowed_hostnames,
        contact="ops@example.com",
    )
    frontier = FrontierPolicy(seeds, options)
    policy = AssetPolicy(options)
    coordinator = SitemapDiscoveryCoordinator(strategy="hybrid-unified")
    state = FakeState()
    writer = FakeCrawlWriter(state, options)
    spider = UnifiedSpider(
        seeds=seeds,
        config=config,
        options=options,
        frontier=frontier,
        writer=writer,  # type: ignore[arg-type]
        policy=policy,
        coordinator=coordinator,
        resume_records=resume_records,
    )
    return spider, writer


def page_request(
    url: str,
    *,
    discovery_source: str = "seed",
    seed_mode: str = "recursive",
    explicit: bool = False,
) -> Request:
    return Request(
        url,
        meta={
            "input_url": url,
            "seed_mode": seed_mode,
            "response_purpose": "page",
            "discovery_source": discovery_source,
            "explicit": explicit,
        },
    )


def html_response(request: Request, body: str, status: int = 200) -> HtmlResponse:
    return HtmlResponse(
        url=request.url,
        status=status,
        headers={b"Content-Type": b"text/html; charset=utf-8"},
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def seed_duplicate(frontier: FrontierPolicy, url: str) -> None:
    cand = Candidate(
        url=url,
        source_mode="recursive",
        discovered_from=None,
        kind="html",
        purpose="page",
        explicit=False,
        discovery_source="html_link",
    )
    frontier.consider(cand)


# Step 4 Required New Cases


def test_recursive_start_schedules_robots_sitemaps_and_homepage() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    urls = {request.url for request in spider.start_requests()}
    assert urls == {
        "https://a.test/robots.txt",
        "https://a.test/sitemap.xml",
        "https://a.test/sitemap_index.xml",
        "https://a.test/sitemap-index.xml",
        "https://a.test/",
    }


def test_resume_schedules_pending_record_from_sqlite_state() -> None:
    pending = {
        "url": "https://a.test/pending",
        "status": "scheduled",
        "frontier_action": "scheduled",
        "seed_type": "recursive",
        "discovered_from": "https://a.test/news",
        "discovery_source": "html_link",
        "response_purpose": "page",
    }
    spider, _ = make_spider(
        recursive_hostnames=frozenset({"a.test"}),
        resume_records=(pending,),
    )
    spider.frontier.restore((pending,))

    requests = list(spider.start_requests())
    resumed = next(request for request in requests if request.url == pending["url"])

    assert resumed.meta["seed_mode"] == "recursive"
    assert resumed.meta["discovered_from"] == "https://a.test/news"
    assert resumed.meta["discovery_source"] == "html_link"


def test_sitemap_discovered_html_also_expands_same_host_links() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    request = page_request(
        "https://a.test/from-map", discovery_source="sitemap"
    )
    children = list(spider.parse_response(html_response(
        request,
        '<a href="/outside-map">New</a><a href="https://other.test/no">No</a>',
    )))
    assert [child.url for child in children] == ["https://a.test/outside-map"]
    assert children[0].meta["discovery_source"] == "html_link"


def test_exact_seed_extracts_but_does_not_expand_html() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/one",))
    request = next(spider.start_requests())
    children = list(spider.parse_response(
        html_response(request, '<a href="/two">Two</a>')
    ))
    assert children == []
    assert writer.articles[0]["url"] == "https://a.test/one"


def test_exact_seed_schedules_document_attachment_but_not_html() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/one",), assets="all")
    request = next(spider.start_requests())
    children = list(spider.parse_response(
        html_response(
            request,
            '<main><h1>One</h1><p>Body</p><a href="/report.pdf">Report</a><a href="/two">Two</a></main>',
        )
    ))
    assert len(children) == 1
    assert children[0].url == "https://a.test/report.pdf"
    assert children[0].meta["asset_role"] == "document_attachment"
    assert writer.articles[0]["url"] == "https://a.test/one"


def test_pagination_counts_only_newly_scheduled_content() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    seed_duplicate(spider.frontier, "https://a.test/article")
    request = page_request(
        "https://a.test/news?page=1", discovery_source="html_link"
    )
    list(spider.parse_response(html_response(
        request, '<a href="/article">Already seen</a>'
    )))
    assert spider.frontier.snapshot()["stats"][
        "frontier/pagination_empty_observation"
    ] == 1


def test_wordpress_shortlink_counts_as_new_pagination_content() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    request = page_request(
        "https://a.test/news?page=1", discovery_source="html_link"
    )
    request.meta.update(
        {
            "family_key": "a.test/news?page={page}",
            "link_kind": "pagination",
            "ordinal": 1,
        }
    )

    children = list(spider.parse_response(html_response(
        request,
        '<link rel="shortlink" href="/?p=33214">'
        '<a href="/news?page=2">2</a>',
    )))

    assert "https://a.test/?p=33214" in {child.url for child in children}
    assert spider.frontier.snapshot()["stats"].get(
        "frontier/pagination_empty_observation", 0
    ) == 0


def test_html_schedules_all_assets_in_all_mode() -> None:
    spider, writer = make_spider(
        recursive_hostnames=frozenset({"a.test"}), assets="all"
    )
    request = page_request("https://a.test/", discovery_source="seed")
    children = list(spider.parse_response(html_response(
        request,
        '<main><h1>Title</h1><p>Body</p>'
        '<a href="/report.pdf">PDF</a>'
        '<a href="/sheet.xlsx">Sheet</a>'
        '<img src="/photo.jpg"><video src="/movie.mp4"></video></main>',
    )))
    child_urls = {child.url for child in children}
    assert "https://a.test/report.pdf" in child_urls
    assert "https://a.test/photo.jpg" in child_urls
    assert writer.articles[0]["assets"]


def test_extensionless_embedded_resource_outside_article_is_not_scheduled_as_page() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    request = page_request("https://a.test/news", discovery_source="seed")

    children = list(spider.parse_response(html_response(
        request,
        '<main><h1>Title</h1><p>Body</p></main>'
        '<footer><img src="/index.php?second=cronjobs&amp;p=random" alt="cron"></footer>',
    )))

    assert "https://a.test/index.php?p=random&second=cronjobs" not in {
        child.url for child in children
    }


def test_https_homepage_failure_yields_one_http_fallback_and_second_yields_none() -> None:
    spider, writer = make_spider(recursive_hostnames=frozenset({"a.test"}))
    start_reqs = list(spider.start_requests())
    home_req = next(r for r in start_reqs if r.url == "https://a.test/")

    # HTTPS homepage fails with HTTP 500
    home_500 = Response(
        url="https://a.test/",
        status=500,
        request=home_req,
    )
    fallback_reqs = list(spider.parse_response(home_500))
    assert len(fallback_reqs) == 1
    fallback_req = fallback_reqs[0]
    assert fallback_req.url == "http://a.test/"
    assert fallback_req.meta["http_fallback"] is True

    # Second failure on the HTTP fallback yields no further fallbacks
    fallback_500 = Response(
        url="http://a.test/",
        status=500,
        request=fallback_req,
    )
    second_fallback = list(spider.parse_response(fallback_500))
    assert second_fallback == []


def test_https_transport_failure_yields_one_http_fallback_and_second_yields_none() -> None:
    spider, writer = make_spider(recursive_hostnames=frozenset({"a.test"}))
    start_reqs = list(spider.start_requests())
    home_req = next(r for r in start_reqs if r.url == "https://a.test/")

    failure = SimpleNamespace(
        request=home_req,
        value=RuntimeError("connection reset"),
        type=RuntimeError,
    )
    fallback_iter = spider.on_request_error(failure)
    assert fallback_iter is not None
    fallback_reqs = list(fallback_iter)
    assert len(fallback_reqs) == 1
    fallback_req = fallback_reqs[0]
    assert fallback_req.url == "http://a.test/"
    assert fallback_req.meta["http_fallback"] is True

    second_failure = SimpleNamespace(
        request=fallback_req,
        value=RuntimeError("connection reset again"),
        type=RuntimeError,
    )
    assert spider.on_request_error(second_failure) is None


# Migrated Cases from ContentSpider & Historical Spider


def test_asset_policy_schedules_in_all_and_records_in_content_only() -> None:
    page = "https://a.test/article"
    light_policy = AssetPolicy(CrawlOptions(assets="content-only"))
    assert (
        light_policy.consider(
            ArticleAsset("https://a.test/photo.jpg", "inline_image", external=False), page
        ).action
        == "record_only"
    )
    assert (
        light_policy.consider(
            ArticleAsset("https://a.test/report.pdf", "document_attachment", external=False), page
        ).action
        == "record_only"
    )

    all_policy = AssetPolicy(CrawlOptions(assets="all"))
    assert (
        all_policy.consider(
            ArticleAsset("https://a.test/photo.jpg", "inline_image", external=False), page
        ).action
        == "schedule"
    )
    assert (
        all_policy.consider(
            ArticleAsset("https://a.test/report.pdf", "document_attachment", external=False), page
        ).action
        == "schedule"
    )


def test_known_image_media_targets_skipped_without_request() -> None:
    spider, writer = make_spider(
        exact_urls=("https://a.test/photo.jpg", "https://a.test/audio.mp3", "https://a.test/article")
    )
    requests = list(spider.start_requests())
    assert [r.url for r in requests] == ["https://a.test/article"]
    skipped_urls = {d["url"] for d in writer.discovered}
    assert skipped_urls == {"https://a.test/photo.jpg", "https://a.test/audio.mp3"}


def test_direct_pdf_target_is_stored() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/doc.pdf",), assets="all")
    req = next(spider.start_requests())
    resp = Response(
        url="https://a.test/doc.pdf",
        status=200,
        headers={"Content-Type": "application/pdf"},
        body=b"%PDF-1.4",
        request=req,
    )
    list(spider.parse_response(resp))
    assert len(writer.files) == 1
    assert writer.files[0]["url"] == "https://a.test/doc.pdf"


def test_extensionless_image_response_is_not_persisted() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/dynamic-image",))
    req = next(spider.start_requests())
    resp = Response(
        url="https://a.test/dynamic-image",
        status=200,
        headers={"Content-Type": "image/png"},
        body=b"\x89PNG",
        request=req,
    )
    list(spider.parse_response(resp))
    assert len(writer.files) == 0
    assert len(writer.discovered) == 1
    assert writer.discovered[0]["status"] == "discovered_not_downloaded"


def test_duplicate_document_links_schedule_once() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}), assets="all")
    request = page_request("https://a.test/article", discovery_source="seed")
    response = html_response(
        request,
        '<main><h1>Title</h1><p>Body</p><a href="/report.pdf">Report 1</a><a href="/report.pdf">Report 2</a></main>',
    )
    children = list(spider.parse_response(response))
    assert len(children) == 1
    assert children[0].url == "https://a.test/report.pdf"


def test_document_responses_never_create_children() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/doc.pdf",), assets="all")
    req = next(spider.start_requests())
    resp = Response(
        url="https://a.test/doc.pdf",
        status=200,
        headers={"Content-Type": "application/pdf"},
        body=b"%PDF-1.4 with <a href='/other'>link</a>",
        request=req,
    )
    children = list(spider.parse_response(resp))
    assert children == []
    assert len(writer.files) == 1


def test_robots_disallowed_target_is_recorded_as_skipped() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/private",))
    request = next(spider.start_requests())
    failure = SimpleNamespace(
        request=request,
        value=IgnoreRequest("Forbidden by robots.txt"),
        type=IgnoreRequest,
    )

    spider.on_request_error(failure)

    assert writer.errors == []
    assert writer.skipped == [
        {
            "url": "https://a.test/private",
            "status": "skipped",
            "reason": "robots_disallowed",
        }
    ]
    assert writer.checkpoints == ["https://a.test/private"]


def test_captcha_target_is_recorded_as_blocked_without_error() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/article",))
    request = next(spider.start_requests())
    failure = SimpleNamespace(
        request=request,
        value=IgnoreRequest("captcha_blocked:interactive_captcha"),
        type=IgnoreRequest,
    )

    spider.on_request_error(failure)

    assert writer.errors == []
    assert writer.skipped == [
        {
            "url": "https://a.test/article",
            "status": "skipped",
            "reason": "captcha_blocked",
        }
    ]
    assert writer.checkpoints == ["https://a.test/article"]


def test_login_required_target_is_recorded_as_skipped() -> None:
    spider, writer = make_spider(exact_urls=("https://a.test/private",))
    request = next(spider.start_requests())
    failure = SimpleNamespace(
        request=request,
        value=IgnoreRequest("login_required:access_gate"),
        type=IgnoreRequest,
    )

    spider.on_request_error(failure)

    assert writer.errors == []
    assert writer.skipped[0]["reason"] == "login_required"
    assert writer.checkpoints == ["https://a.test/private"]


def test_recursive_html_schedules_only_the_exact_seed_hostname() -> None:
    spider, writer = make_spider(recursive_hostnames=frozenset({"lms.hust.edu.vn"}))
    request = page_request("https://lms.hust.edu.vn/", discovery_source="seed")
    response = html_response(
        request,
        '<a href="/course">Course</a>'
        '<a href="https://sub.lms.hust.edu.vn/no">Sub</a>'
        '<a href="https://evil.test/?next=lms.hust.edu.vn">Evil</a>',
    )
    assert [item.url for item in spider.parse_response(response)] == [
        "https://lms.hust.edu.vn/course"
    ]


def test_overlapping_exact_and_recursive_schedules_once_with_recursive_mode() -> None:
    spider, writer = make_spider(
        recursive_hostnames=frozenset({"a.test"}),
        exact_urls=("https://a.test/page",),
    )
    requests = list(spider.start_requests())
    page_reqs = [r for r in requests if r.url == "https://a.test/page"]
    assert len(page_reqs) == 1
    assert page_reqs[0].meta["seed_mode"] == "recursive"


def test_known_binary_links_in_content_only_become_discovered_not_downloaded() -> None:
    spider, writer = make_spider(
        recursive_hostnames=frozenset({"a.test"}),
        assets="content-only",
    )
    request = page_request("https://a.test/", discovery_source="seed")
    response = html_response(
        request,
        '<a href="/report.pdf">Report PDF</a>',
    )
    scheduled = list(spider.parse_response(response))
    assert scheduled == []
    assert writer.url_records["https://a.test/report.pdf"]["status"] == "discovered_not_downloaded"


def test_unknown_binary_responses_are_not_persisted_in_content_only() -> None:
    spider, writer = make_spider(
        recursive_hostnames=frozenset({"a.test"}),
        assets="content-only",
    )
    req = Request(
        "https://a.test/download-slug",
        meta={
            "input_url": "https://a.test/download-slug",
            "seed_mode": "recursive",
            "explicit": False,
            "response_purpose": "page",
        },
    )
    response = Response(
        url="https://a.test/download-slug",
        status=200,
        headers={b"Content-Type": b"application/pdf"},
        body=b"%PDF-1.4...",
        request=req,
    )
    list(spider.parse_response(response))
    assert writer.files == []
    assert writer.url_records["https://a.test/download-slug"]["status"] == "discovered_not_downloaded"
    assert writer.state.is_complete("https://a.test/download-slug")


def test_explicit_binary_is_saved() -> None:
    spider, writer = make_spider(
        exact_urls=("https://a.test/report.pdf",),
        assets="all",
    )
    req = next(spider.start_requests())
    response = Response(
        url="https://a.test/report.pdf",
        status=200,
        headers={b"Content-Type": b"application/pdf"},
        body=b"%PDF-1.4...",
        request=req,
    )
    list(spider.parse_response(response))
    assert len(writer.files) == 1
    assert writer.url_records["https://a.test/report.pdf"]["status"] == "file_saved"


def test_accepted_request_records_scheduled_provenance() -> None:
    spider, writer = make_spider(recursive_hostnames=frozenset({"a.test"}))
    requests = list(spider.start_requests())
    assert len(requests) > 0
    robots_rec = writer.url_records["https://a.test/robots.txt"]
    assert robots_rec["status"] == "scheduled"
    assert robots_rec["seed_type"] == "recursive"
    assert robots_rec["frontier_action"] == "scheduled"
    assert "https://a.test/robots.txt" not in writer.state.completed


INDEX_XML = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.test/generated/site-map</loc></sitemap></sitemapindex>'


def test_nested_sitemap_is_scheduled_in_content_only() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}), assets="content-only")
    request = next(r for r in spider.start_requests() if r.url.endswith("/sitemap.xml"))
    response = Response(
        request.url,
        status=200,
        headers={b"Content-Type": b"application/xml"},
        body=INDEX_XML,
        request=request,
    )
    children = list(spider.parse_response(response))
    assert [r.url for r in children] == ["https://a.test/generated/site-map"]
    assert children[0].meta["response_purpose"] == "sitemap"


def test_index_child_failure_produces_sitemap_partial_without_fallback() -> None:
    spider, _ = make_spider(recursive_hostnames=frozenset({"a.test"}))
    start_reqs = list(spider.start_requests())
    robots_req = next(r for r in start_reqs if r.url.endswith("/robots.txt"))
    list(
        spider.parse_response(
            Response(
                robots_req.url,
                status=200,
                headers={b"Content-Type": b"text/plain"},
                body=b"",
                request=robots_req,
            )
        )
    )

    sm_req = next(r for r in start_reqs if r.url.endswith("/sitemap.xml"))
    index_body = (
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://a.test/good.xml</loc></sitemap>"
        b"<sitemap><loc>https://a.test/bad.xml</loc></sitemap></sitemapindex>"
    )
    children = list(
        spider.parse_response(
            Response(
                sm_req.url,
                status=200,
                headers={b"Content-Type": b"application/xml"},
                body=index_body,
                request=sm_req,
            )
        )
    )
    assert len(children) == 2

    good_req = next(r for r in children if r.url.endswith("/good.xml"))
    urlset_body = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://a.test/article1</loc></url></urlset>"
    )
    targets = list(
        spider.parse_response(
            Response(
                good_req.url,
                status=200,
                headers={b"Content-Type": b"application/xml"},
                body=urlset_body,
                request=good_req,
            )
        )
    )
    assert len(targets) == 1

    bad_req = next(r for r in children if r.url.endswith("/bad.xml"))
    bad_res = list(spider.parse_response(Response(bad_req.url, status=500, request=bad_req)))
    assert bad_res == []

    for r in start_reqs:
        if "sitemap" in r.url and r.url != sm_req.url:
            list(spider.parse_response(Response(r.url, status=404, request=r)))

    snapshot = spider.coordinator.snapshot()
    assert snapshot["hosts"]["a.test"]["mode"] == "sitemap_partial"
    assert "https://a.test/" not in [t.url for t in targets]


def test_external_page_rejected_but_external_asset_discovered() -> None:
    spider, writer = make_spider(recursive_hostnames=frozenset({"a.test"}))
    request = page_request("https://a.test/", discovery_source="seed")
    html = (
        '<main><h1>Header</h1><p>Text</p>'
        '<a href="https://cdn.example.com/other">External Page</a>'
        '<img src="https://cdn.example.com/logo.png">'
        '<img src="https://a.test/valid.png"></main>'
    )
    children = list(spider.parse_response(html_response(request, html)))
    assert children == []

    discovered_urls = {d["url"] for d in writer.discovered}
    skipped_reasons = {s["url"]: s["reason"] for s in writer.skipped}
    assert "https://cdn.example.com/other" not in discovered_urls
    assert skipped_reasons["https://cdn.example.com/other"] == "host_out_of_scope"
    assert "https://cdn.example.com/logo.png" in discovered_urls
    assert "https://a.test/valid.png" in discovered_urls


def test_same_host_media_obeys_host_url_budget() -> None:
    seeds = SeedSet(recursive_hostnames=frozenset({"a.test"}), exact_urls=(), invalid=())
    options = CrawlOptions(max_urls_per_host=1)
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    frontier = FrontierPolicy(seeds, options)
    policy = AssetPolicy(options)
    coordinator = SitemapDiscoveryCoordinator(strategy="hybrid-unified")
    state = FakeState()
    writer = FakeCrawlWriter(state, options)
    spider = UnifiedSpider(
        seeds=seeds,
        config=config,
        options=options,
        frontier=frontier,
        writer=writer,
        policy=policy,
        coordinator=coordinator,
    )
    request = page_request("https://a.test/", discovery_source="seed")
    html = '<main><h1>Header</h1><p>Text</p><img src="/first.png"><img src="/second.png"></main>'
    list(spider.parse_response(html_response(request, html)))

    discovered_urls = {d["url"] for d in writer.discovered}
    assert "https://a.test/first.png" in discovered_urls
    assert "https://a.test/second.png" not in discovered_urls
    assert frontier.snapshot()["stats"]["frontier/reject/host_url_limit"] == 1


def test_content_only_records_assets_without_requests() -> None:
    spider, writer = make_spider(assets="content-only")
    children = list(
        spider.parse_response(
            html_response(
                page_request("https://a.test/p"),
                '<main><h1>T</h1><p>Body</p><img src="/a.png"><a href="/r.pdf">R</a></main>',
            )
        )
    )
    assert not [request for request in children if request.meta.get("request_scope") == "asset"]
    assert {record["status"] for record in writer.discovered_assets} == {"discovered_not_downloaded"}


def test_all_schedules_same_host_and_external_assets_only() -> None:
    spider, _ = make_spider(assets="all")
    children = list(
        spider.parse_response(
            html_response(
                page_request("https://a.test/p"),
                '<main><h1>T</h1><p>Body</p><img src="https://cdn.test/a.png"><iframe src="https://cdn.test/frame"></iframe></main>',
            )
        )
    )
    assets = [request for request in children if request.meta.get("request_scope") == "asset"]
    assert [request.url for request in assets] == ["https://cdn.test/a.png"]


def test_duplicate_page_never_expands() -> None:
    spider, writer = make_spider()
    writer.next_duplicate_of = "https://a.test/original"
    children = list(
        spider.parse_response(
            html_response(
                page_request("https://a.test/p"),
                '<main><h1>T</h1><p>Body</p><a href="/child">Child</a><img src="/a.png"></main>',
            )
        )
    )
    assert children == []


def test_paginator_enqueues_one_next() -> None:
    spider, _ = make_spider()
    first = html_response(
        page_request("https://a.test/news?page=1"),
        '<main><h1>News</h1><p>Listing body</p><a href="/article/1">A</a>'
        '<a href="/news?page=2" rel="next">2</a><a href="/news?page=3">3</a></main>',
    )
    requests = list(spider.parse_response(first))
    assert [request.url for request in requests if "page=" in request.url] == ["https://a.test/news?page=2"]


def test_exact_pdf_seed_obeys_mode() -> None:
    light, light_writer = make_spider(exact_urls=("https://a.test/r.pdf",), assets="content-only")
    assert list(light.start_requests()) == []
    assert light_writer.discovered_assets[0]["status"] == "discovered_not_downloaded"
    full, _ = make_spider(exact_urls=("https://a.test/r.pdf",), assets="all")
    request = next(full.start_requests())
    assert request.meta["request_scope"] == "asset"


def test_asset_error_record_does_not_replace_article() -> None:
    spider, writer = make_spider(assets="all")
    list(
        spider.parse_response(
            html_response(
                page_request("https://a.test/p"),
                '<main><h1>T</h1><p>Body</p><img src="https://cdn.test/a.png"></main>',
            )
        )
    )
    spider.writer.write_error({"url": "https://cdn.test/a.png", "error": "dns_failure"})
    assert writer.articles[0]["url"] == "https://a.test/p"
    assert writer.errors[0]["url"] == "https://cdn.test/a.png"
