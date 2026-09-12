from __future__ import annotations

from pathlib import Path
import pytest

from tests.fixtures.generated_site import run_generated_site


@pytest.mark.performance
def test_query_cross_product_is_bounded_without_losing_detail_pages(tmp_path: Path) -> None:
    result = run_generated_site(
        tmp_path,
        filters=10,
        sorts=4,
        languages=5,
        detail_pages=250,
        max_query_variants_per_path=20,
    )
    assert result.detail_urls_extracted == 250
    assert result.list_variants_requested <= 20
    assert result.duplicate_logical_fetches == 0
    assert result.peak_in_memory_candidates <= 500


@pytest.mark.performance
def test_unclassifiable_generator_is_truncated_by_circuit_breaker(tmp_path: Path) -> None:
    routes = {}
    for i in range(30):
        next_url = f"/item/gen-{i + 1}"
        routes[f"/item/gen-{i}"] = (
            f"<main><h1>Item {i}</h1><p>Unique content {i}</p><a href='{next_url}'>Next</a></main>"
        )
    routes["/"] = '<main><h1>Home</h1><a href="/item/gen-0">Start</a></main>'

    run_generated_site(
        tmp_path,
        filters=0,
        sorts=0,
        languages=0,
        detail_pages=0,
        max_total_urls=10,
        routes=routes,
    )
    requested_path = tmp_path / "requested_urls.txt"
    requests = [line.strip() for line in requested_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(requests) <= 12


@pytest.mark.performance
def test_recognized_pagination_closes_before_circuit_breaker(tmp_path: Path) -> None:
    routes = {
        "/": '<main><h1>Home</h1><a href="/news?page=1">News</a></main>',
        "/news?page=1": (
            '<main><h1>News 1</h1><p>Body 1</p>'
            '<a href="/article/100">A100</a>'
            '<a href="/news?page=2" rel="next">2</a>'
            '<a href="/news?page=3">3</a>'
            '</main>'
        ),
        "/news?page=2": (
            '<main><h1>News 2</h1><p>Body 2</p>'
            '<a href="/article/101">A101</a>'
            '<a href="/news?page=3" rel="next">3</a>'
            '</main>'
        ),
        "/news?page=3": (
            '<main><h1>News 3</h1><p>Body 3</p>'
            '<a href="/article/101">A101</a>'
            '<a href="/news?page=4" rel="next">4</a>'
            '</main>'
        ),
        "/article/100": "<main><h1>Article 100</h1><p>Body 100</p></main>",
        "/article/101": "<main><h1>Article 101</h1><p>Body 101</p></main>",
    }
    run_generated_site(
        tmp_path,
        filters=0,
        sorts=0,
        languages=0,
        detail_pages=0,
        max_total_urls=1000,
        routes=routes,
    )
    requested_path = tmp_path / "requested_urls.txt"
    requests = [line.strip() for line in requested_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any("page=4" in url for url in requests)
