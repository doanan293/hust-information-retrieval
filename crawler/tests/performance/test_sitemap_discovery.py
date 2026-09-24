from __future__ import annotations

import time
import pytest

from hust_crawler.crawl.sitemaps import parse_sitemap
from hust_crawler.policies.url import canonicalize_url


@pytest.mark.performance
def test_large_sitemap_parsing_and_canonical_deduplication() -> None:
    url_count = 10000
    urls = [f"https://example.test/articles/item_{i}" for i in range(url_count)]
    # Add duplicate variants with varying casing, port, and fragment to verify canonical deduplication
    duplicated_urls = urls + [
        f"HTTPS://EXAMPLE.TEST:443/articles/item_{i}#section" for i in range(2000)
    ]

    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in duplicated_urls:
        xml_parts.append(f"<url><loc>{u}</loc></url>")
    xml_parts.append("</urlset>")
    xml_content = "\n".join(xml_parts).encode("utf-8")

    start_time = time.perf_counter()
    result = parse_sitemap(xml_content, base_url="https://example.test/")
    parse_duration = time.perf_counter() - start_time

    assert result.kind == "urlset"
    # parse_sitemap inherently deduplicates normalized URLs
    assert len(result.locations) == url_count
    assert parse_duration < 2.0

    # Canonical target collection
    canon_start = time.perf_counter()
    canonical_targets: set[str] = set()
    for loc in result.locations:
        canonical, _, _ = canonicalize_url(loc, check_traps=False)
        if canonical:
            canonical_targets.add(canonical)
    canon_duration = time.perf_counter() - canon_start

    assert len(canonical_targets) == url_count
    assert canon_duration < 2.0
