import pytest

from hust_crawler.policies.url import (
    UrlDecision,
    UrlPolicy,
    canonicalize_url,
    classify_frontier_trap,
)



def test_exact_host_and_tracking_cleanup() -> None:
    decision = UrlPolicy(frozenset({"example.com"})).decide(
        "HTTPS://EXAMPLE.COM:443/a/../b?utm_source=x&z=2&a=1#top"
    )
    assert decision == UrlDecision(True, "https://example.com/b?a=1&z=2", None, "page")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://x.example.com/", "host_out_of_scope"),
        ("https://user:secret@example.com/", "credentials_or_missing_host"),
        ("https://example.com:99999/", "invalid_port"),
        ("https://example.com/logout", "logout_path"),
        ("https://example.com/search?q=x", "search_trap"),
        ("https://example.com/calendar/2099/12", "calendar_trap"),
        ("https://example.com/a/a/a/a", "repeated_path_trap"),
    ],
)
def test_rejected_urls_have_stable_reasons(url: str, reason: str) -> None:
    assert UrlPolicy(frozenset({"example.com"})).decide(url).reason == reason


def test_content_and_policy_share_canonical_form() -> None:
    raw = "https://EXAMPLE.com/a/../b?utm_source=x&z=2&a=1#part"
    policy_url = UrlPolicy(frozenset({"example.com"})).decide(raw).canonical_url
    assert canonicalize_url(raw)[0] == policy_url == "https://example.com/b?a=1&z=2"


def test_tracking_parameters_do_not_create_distinct_urls() -> None:
    first, _, _ = canonicalize_url("https://a.test/news?id=7&utm_source=x")
    second, _, _ = canonicalize_url("https://a.test/news?fbclid=y&id=7")
    assert first == second == "https://a.test/news?id=7"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://a.test/wp-json/wp/v2/posts", "api_endpoint_trap"),
        ("https://a.test/search?q=admission", "search_trap"),
        ("https://a.test/events/calendar.ics", "calendar_export_trap"),
        ("https://a.test/news/feed", "feed_trap"),
        ("https://a.test/article/print", "action_endpoint_trap"),
    ],
)
def test_frontier_traps(url: str, reason: str) -> None:
    assert classify_frontier_trap(url) == reason
