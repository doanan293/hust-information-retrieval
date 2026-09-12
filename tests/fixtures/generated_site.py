from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse, Request, Response, TextResponse

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator
from hust_crawler.crawl.spider import UnifiedSpider
from hust_crawler.crawl.state import CrawlState
from hust_crawler.crawl.writer import CrawlWriter


@dataclass(frozen=True, slots=True)
class GeneratedSiteResult:
    detail_urls_extracted: int
    list_variants_requested: int
    duplicate_logical_fetches: int
    peak_in_memory_candidates: int


GeneratedRun = GeneratedSiteResult


def html(body: str, status: int = 200) -> Callable[[str, Request], HtmlResponse]:
    def _make(url: str, req: Request) -> HtmlResponse:
        return HtmlResponse(
            url=url,
            status=status,
            headers={b"Content-Type": b"text/html; charset=utf-8"},
            body=body.encode("utf-8") if isinstance(body, str) else body,
            encoding="utf-8",
            request=req,
        )

    return _make


def response(
    body: bytes, content_type: str = "application/octet-stream", status: int = 200
) -> Callable[[str, Request], Response]:
    def _make(url: str, req: Request) -> Response:
        return Response(
            url=url,
            status=status,
            headers={b"Content-Type": content_type.encode("latin1")},
            body=body,
            request=req,
        )

    return _make


html_response = html
binary_response = response


def _build_response(handler: Any, url: str, req: Request) -> Response:
    if callable(handler):
        return handler(url, req)
    if isinstance(handler, Response):
        return handler.replace(url=url, request=req)
    if isinstance(handler, str):
        return HtmlResponse(
            url=url,
            status=200,
            headers={b"Content-Type": b"text/html; charset=utf-8"},
            body=handler.encode("utf-8"),
            encoding="utf-8",
            request=req,
        )
    if isinstance(handler, bytes):
        return Response(url=url, status=200, body=handler, request=req)
    raise TypeError(f"Unknown route handler type: {type(handler)}")


def default_routes() -> dict[str, Any]:
    routes: dict[str, Any] = {}
    routes["/html-only/"] = html(
        '<a href="/html-only/article">Article</a>'
        '<a href="/html-only/report.pdf">Report</a>'
    )
    routes["/html-only/article"] = html("<main><h1>HTML only</h1><p>Body</p></main>")
    routes["/html-only/report.pdf"] = response(b"%PDF-1.4", "application/pdf")
    routes["/cycle/a"] = html('<a href="/cycle/b">B</a>')
    routes["/cycle/b"] = html('<a href="/cycle/a">A</a>')
    return routes


def run_generated_site(
    tmp_path: Path,
    *,
    filters: int = 10,
    sorts: int = 4,
    languages: int = 5,
    detail_pages: int = 250,
    max_query_variants_per_path: int = 20,
    max_total_urls: int = 10000,
    routes: dict[str, Any] | None = None,
) -> GeneratedSiteResult:
    seeds_file = tmp_path / "seeds.txt"
    seeds_file.write_text("site.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"site.test"}), (), ())
    options = CrawlOptions(
        max_query_variants_per_path=max_query_variants_per_path,
        max_total_urls=max_total_urls,
    )
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    frontier = FrontierPolicy(seeds, options)

    state = CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=seeds_file,
        semantic_config=options.semantic_snapshot(seeds),
        runtime_config={},
        resume=False,
    )
    writer = CrawlWriter(tmp_path / "output", state, options)
    policy = AssetPolicy(options)
    coordinator = SitemapDiscoveryCoordinator(
        exact_hostnames=frozenset(),
        on_change=state.upsert_host_discovery,
        strategy="hybrid-unified",
    )
    spider = UnifiedSpider(
        seeds=seeds,
        config=config,
        options=options,
        frontier=frontier,
        writer=writer,
        policy=policy,
        coordinator=coordinator,
    )

    active_routes = default_routes()
    if routes is not None:
        active_routes.update(routes)

    # Build sitemap XML containing all detail pages
    sitemap_locs = "".join(
        f"<url><loc>https://site.test/detail/{i}</loc></url>" for i in range(detail_pages)
    )
    sitemap_xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap_locs}</urlset>'

    # Build cross product of list links
    list_links: list[str] = []
    for f in range(filters):
        for s in range(sorts):
            for l_idx in range(languages):
                list_links.append(f'<a href="/list?filter={f}&amp;sort={s}&amp;lang={l_idx}">Link</a>')
    list_links_html = "".join(list_links)

    queue: deque[Request] = deque(spider.start_requests())
    logical_fetches: dict[str, int] = defaultdict(int)
    list_variants_requested = 0
    peak_candidates = 0
    request_log_path = tmp_path / "requested_urls.txt"

    while queue:
        peak_candidates = max(peak_candidates, len(queue))
        req = queue.popleft()
        url = req.url
        logical_fetches[url] += 1
        with open(request_log_path, "a", encoding="utf-8") as f:
            f.write(f"{url}\n")

        parsed = urlsplit(url)
        path = parsed.path

        if path in active_routes:
            resp = _build_response(active_routes[path], url, req)
        elif (
            path.endswith("/sitemap.xml")
            or path.endswith("/sitemap_index.xml")
            or path.endswith("/sitemap-index.xml")
        ):
            body = sitemap_xml.encode("utf-8") if path.endswith("/sitemap.xml") else b""
            resp = TextResponse(
                url=url,
                status=200,
                headers={b"Content-Type": b"application/xml"},
                body=body,
                encoding="utf-8",
                request=req,
            )
        elif path.endswith("/robots.txt"):
            resp = TextResponse(
                url=url,
                status=200,
                headers={b"Content-Type": b"text/plain"},
                body=b"Sitemap: https://site.test/sitemap.xml\n",
                encoding="utf-8",
                request=req,
            )
        elif path in {"", "/"}:
            body = (
                f'<main><h1>Home</h1><a href="/list?filter=0&amp;sort=0&amp;lang=0">List</a>'
                f"{list_links_html}</main>"
            ).encode("utf-8")
            resp = HtmlResponse(
                url=url,
                status=200,
                headers={b"Content-Type": b"text/html; charset=utf-8"},
                body=body,
                encoding="utf-8",
                request=req,
            )
        elif path == "/list":
            list_variants_requested += 1
            # link to detail pages and other list pages
            detail_links = "".join(
                f'<a href="/detail/{i}">Detail {i}</a>' for i in range(min(20, detail_pages))
            )
            body = f"<main><h1>List</h1>{detail_links}{list_links_html}</main>".encode("utf-8")
            resp = HtmlResponse(
                url=url,
                status=200,
                headers={b"Content-Type": b"text/html; charset=utf-8"},
                body=body,
                encoding="utf-8",
                request=req,
            )
        elif path.startswith("/detail/"):
            body = f"<main><h1>Detail</h1><p>Body text for {path}</p></main>".encode("utf-8")
            resp = HtmlResponse(
                url=url,
                status=200,
                headers={b"Content-Type": b"text/html; charset=utf-8"},
                body=body,
                encoding="utf-8",
                request=req,
            )
        else:
            resp = TextResponse(url=url, status=404, body=b"", request=req)

        for next_req in spider.parse_response(resp):
            queue.append(next_req)

    writer.publish()
    cursor = state.connection.execute(
        "SELECT count(*) FROM articles WHERE url LIKE 'https://site.test/detail/%';"
    )
    detail_extracted = int(cursor.fetchone()[0])
    state.close()
    duplicate_fetches = sum(v - 1 for v in logical_fetches.values() if v > 1)

    return GeneratedSiteResult(
        detail_urls_extracted=detail_extracted,
        list_variants_requested=list_variants_requested,
        duplicate_logical_fetches=duplicate_fetches,
        peak_in_memory_candidates=peak_candidates,
    )
