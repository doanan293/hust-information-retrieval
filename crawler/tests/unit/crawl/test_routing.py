import pytest

from hust_crawler.crawl.routing import ResponseRoute, route_response


@pytest.mark.parametrize(
    ("url", "content_type", "action"),
    [
        ("https://a.test/image?id=1", "image/jpeg", "file"),
        ("https://a.test/photo.jpg", "text/html; charset=utf-8", "html"),
        ("https://a.test/asset", "text/css", "transient"),
        ("https://a.test/data.custom", "chemical/x-example", "unknown"),
    ],
)
def test_page_routes_use_mime_before_extension(url, content_type, action) -> None:
    assert route_response(
        url=url, status=200, content_type=content_type, purpose="page"
    ).action == action


@pytest.mark.parametrize("purpose", ["robots", "sitemap"])
@pytest.mark.parametrize("status", [404, 410])
def test_optional_probe_absence_is_not_an_error(purpose, status) -> None:
    route = route_response(
        url=f"https://a.test/{purpose}",
        status=status,
        content_type="text/html",
        purpose=purpose,
    )
    assert route.action == "optional_absent"
    assert route.error is None


@pytest.mark.parametrize(("status", "error"), [(403, "http_403"), (503, "http_503")])
def test_failed_probe_remains_an_exact_http_error(status, error) -> None:
    route = route_response(
        url="https://a.test/sitemap.xml",
        status=status,
        content_type="text/html",
        purpose="sitemap",
    )
    assert route.action == "http_error"
    assert route.error == error


def test_successful_probe_purpose_overrides_generic_mime() -> None:
    assert route_response(
        url="https://a.test/robots.txt",
        status=200,
        content_type="application/octet-stream",
        purpose="robots",
    ) == ResponseRoute("robots", "robots", 200, "application/octet-stream")


def test_asset_purpose_is_terminal_even_when_server_returns_html() -> None:
    route = route_response(
        url="https://cdn.test/a.pdf",
        status=200,
        content_type="text/html",
        purpose="asset",
    )
    assert route.action == "file"
