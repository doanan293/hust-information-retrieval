from __future__ import annotations

import gzip
import pytest

from hust_crawler.crawl.sitemaps import (
    SitemapDiscoveryCoordinator,
    SitemapParseError,
    SitemapParseResult,
    parse_sitemap,
)


def test_sitemap_index_treats_extensionless_locations_as_sitemaps() -> None:
    body = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>/generated/site-map</loc></sitemap></sitemapindex>'
    result = parse_sitemap(body, "https://a.test/index")
    assert result.kind == "index"
    assert result.locations == ("https://a.test/generated/site-map",)


def test_gzipped_urlset_is_decompressed() -> None:
    xml = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.test/a</loc></url></urlset>'
    result = parse_sitemap(gzip.compress(xml), "https://a.test/sitemap.xml.gz", "application/gzip")
    assert result == SitemapParseResult("urlset", ("https://a.test/a",))


def test_gzipped_detected_by_magic_bytes_without_header_or_extension() -> None:
    xml = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.test/b</loc></url></urlset>'
    result = parse_sitemap(gzip.compress(xml), "https://a.test/sitemap")
    assert result == SitemapParseResult("urlset", ("https://a.test/b",))


def test_malformed_gzip_raises_sitemap_parse_error() -> None:
    with pytest.raises(SitemapParseError) as exc_info:
        parse_sitemap(b"\x1f\x8b\x08not-valid-gzip", "https://a.test/sitemap.xml.gz")
    assert exc_info.value.reason == "invalid_gzip"


def test_malformed_xml_raises_sitemap_parse_error() -> None:
    with pytest.raises(SitemapParseError) as exc_info:
        parse_sitemap(b"<broken xml", "https://a.test/sitemap.xml")
    assert exc_info.value.reason == "invalid_xml"


def test_unsupported_root_raises_sitemap_parse_error() -> None:
    with pytest.raises(SitemapParseError) as exc_info:
        parse_sitemap(b"<html><body>Not a sitemap</body></html>", "https://a.test/sitemap.xml")
    assert exc_info.value.reason == "unsupported_root"


def test_empty_or_whitespace_locations_are_ignored() -> None:
    body = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>   </loc></url>
        <url><loc></loc></url>
        <url><loc>https://a.test/valid</loc></url>
    </urlset>"""
    result = parse_sitemap(body, "https://a.test/sitemap.xml")
    assert result.locations == ("https://a.test/valid",)


def test_document_order_deduplication() -> None:
    body = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://a.test/first</loc></url>
        <url><loc>https://a.test/second</loc></url>
        <url><loc>https://a.test/first</loc></url>
    </urlset>"""
    result = parse_sitemap(body, "https://a.test/sitemap.xml")
    assert result.locations == ("https://a.test/first", "https://a.test/second")


def test_image_loc_nodes_are_not_collected_as_page_locations() -> None:
    body = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                 xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
        <url>
            <loc>https://a.test/page1</loc>
            <image:image>
                <image:loc>https://a.test/image.jpg</image:loc>
            </image:image>
        </url>
    </urlset>"""
    result = parse_sitemap(body, "https://a.test/sitemap.xml")
    assert result.locations == ("https://a.test/page1",)


def test_empty_discovery_finishes_as_no_sitemap() -> None:
    coordinator = SitemapDiscoveryCoordinator()
    coordinator.register_sitemap("a.test", "https://a.test/sitemap.xml", required=False)
    coordinator.complete_robots("a.test")
    coordinator.complete_sitemap(
        "a.test", "https://a.test/sitemap.xml", result="absent", targets=0
    )
    host = coordinator.snapshot()["hosts"]["a.test"]
    assert host["mode"] == "no_sitemap"
    assert "fallback_targets" not in host


def test_snapshot_declares_sitemap_only_strategy() -> None:
    assert SitemapDiscoveryCoordinator().snapshot()["strategy"] == "sitemap-only"


def test_partial_sitemap_mode() -> None:
    coordinator = SitemapDiscoveryCoordinator()
    coordinator.register_sitemap("a.test", "https://a.test/root.xml", required=True)
    coordinator.register_sitemap("a.test", "https://a.test/broken.xml", required=True)
    coordinator.record_sitemap_target("a.test", "https://a.test/article")
    coordinator.complete_robots("a.test")
    coordinator.complete_sitemap("a.test", "https://a.test/root.xml", result="success", targets=1)
    coordinator.complete_sitemap("a.test", "https://a.test/broken.xml", result="failed", targets=0)
    assert coordinator.snapshot()["hosts"]["a.test"]["mode"] == "sitemap_partial"


def test_html_targets_are_counted_without_hiding_sitemap_outcome() -> None:
    coordinator = SitemapDiscoveryCoordinator(strategy="hybrid-unified")
    coordinator.register_sitemap("a.test", "https://a.test/sitemap.xml", required=False)
    coordinator.complete_robots("a.test")
    coordinator.complete_sitemap(
        "a.test", "https://a.test/sitemap.xml", result="absent"
    )
    coordinator.record_html_target("a.test", "https://a.test/")
    host = coordinator.snapshot()["hosts"]["a.test"]
    assert host["mode"] == "no_sitemap"
    assert host["html_targets"] == 1
    assert coordinator.snapshot()["strategy"] == "hybrid-unified"


def test_html_target_count_is_unique() -> None:
    coordinator = SitemapDiscoveryCoordinator(strategy="hybrid-unified")
    coordinator.record_html_target("a.test", "https://a.test/news")
    coordinator.record_html_target("a.test", "https://a.test/news#top")
    assert coordinator.snapshot()["hosts"]["a.test"]["html_targets"] == 1
