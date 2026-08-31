from pathlib import Path

from hust_crawler.cdxj import CdxjEntry
from hust_crawler.discovery import (
    DiscoveredUrl,
    historical_seeds,
    parse_robots_sitemaps,
    parse_sitemap_xml,
    root_seeds,
)
from hust_crawler.url_policy import UrlPolicy


def test_robots_extracts_absolute_and_relative_sitemaps() -> None:
    text = "Sitemap: /sitemap.xml\nSitemap: https://example.com/news-map.xml\nDisallow: /"

    assert parse_robots_sitemaps(text, "https://example.com/robots.txt") == (
        "https://example.com/news-map.xml",
        "https://example.com/sitemap.xml",
    )


def test_sitemap_index_and_urlset_are_typed() -> None:
    index = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.com/map-2.xml</loc></sitemap></sitemapindex>'
    urls = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/deep/page</loc></url></urlset>'

    assert parse_sitemap_xml(index, "https://example.com/sitemap.xml").discoveries[0] == DiscoveredUrl(
        "https://example.com/map-2.xml", "sitemap", "https://example.com/sitemap.xml", "sitemap", False
    )
    assert parse_sitemap_xml(urls, "https://example.com/map-2.xml").discoveries[0].url == "https://example.com/deep/page"
    assert parse_sitemap_xml(urls, "https://example.com/map-2.xml").discoveries[0].target_kind == "page"


def test_malformed_sitemap_returns_a_parse_error() -> None:
    result = parse_sitemap_xml(b"<urlset><url>", "https://example.com/bad.xml")

    assert result.error == "malformed_sitemap"
    assert result.discoveries == ()


def test_roots_include_robots_and_conventional_sitemaps() -> None:
    seeds = root_seeds(frozenset({"example.com"}))

    assert [(seed.url, seed.source) for seed in seeds] == [
        ("https://example.com/", "root"),
        ("https://example.com/robots.txt", "robots"),
        ("https://example.com/sitemap.xml", "conventional_sitemap"),
        ("https://example.com/sitemap_index.xml", "conventional_sitemap"),
        ("https://example.com/sitemap-index.xml", "conventional_sitemap"),
    ]


def test_pilot_roots_use_only_root_robots_and_primary_sitemap() -> None:
    seeds = root_seeds(frozenset({"example.com"}), pilot=True)

    assert [(seed.url, seed.source) for seed in seeds] == [
        ("https://example.com/", "root"),
        ("https://example.com/robots.txt", "robots"),
        ("https://example.com/sitemap.xml", "conventional_sitemap"),
    ]


def test_history_reseeds_only_eligible_page_urls(tmp_path: Path) -> None:
    entries = [
        CdxjEntry("https://example.com/old-article", "20260101000000", 200, "text/html", "sha256:a", "a.warc.gz", 0, 1, "urn:uuid:a", "response"),
        CdxjEntry("https://outside.test/page", "20260101000000", 200, "text/html", "sha256:b", "b.warc.gz", 0, 1, "urn:uuid:b", "response"),
    ]
    index = tmp_path / "latest.cdxj"
    index.write_text("\n".join(entry.to_line() for entry in entries) + "\n", encoding="utf-8")

    seeds = historical_seeds(index, UrlPolicy(frozenset({"example.com"})))

    assert [seed.url for seed in seeds] == ["https://example.com/old-article"]
    assert seeds[0].source == "history"


def test_history_does_not_reseed_previous_external_media(tmp_path: Path) -> None:
    entry = CdxjEntry("https://cdn.test/photo.jpg", "20260101000000", 200, "image/jpeg", "sha256:a", "a.warc.gz", 0, 1, "urn:uuid:a", "response")
    index = tmp_path / "latest.cdxj"
    index.write_text(entry.to_line() + "\n", encoding="utf-8")

    seeds = historical_seeds(index, UrlPolicy(frozenset({"example.com"})))

    assert seeds == ()
