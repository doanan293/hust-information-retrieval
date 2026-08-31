import pytest

from hust_crawler.url_policy import UrlPolicy


def test_exact_hostname_and_canonicalization() -> None:
    policy = UrlPolicy(frozenset({"example.com"}))
    decision = policy.decide("HTTPS://EXAMPLE.COM:443/a/../b?utm_source=x&z=2&a=1#top")
    assert decision.accepted
    assert decision.canonical_url == "https://example.com/b?a=1&z=2"


def test_subdomain_is_not_implicitly_allowed() -> None:
    decision = UrlPolicy(frozenset({"example.com"})).decide("https://x.example.com/")
    assert (decision.accepted, decision.reason) == (False, "host_out_of_scope")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("mailto:a@example.com", "unsupported_scheme"),
        ("https://example.com/logout", "logout_path"),
        ("https://example.com/search?q=x", "search_trap"),
        ("https://example.com/calendar/2099/12", "calendar_trap"),
    ],
)
def test_rejects_unsupported_and_trap_urls(url: str, reason: str) -> None:
    assert UrlPolicy(frozenset({"example.com"})).decide(url).reason == reason


def test_normalization_is_idempotent() -> None:
    policy = UrlPolicy(frozenset({"example.com"}))
    first = policy.decide("https://example.com/a/../b?z=2&a=1").canonical_url
    assert first is not None
    assert policy.decide(first).canonical_url == first


def test_external_page_and_embedded_media_are_rejected() -> None:
    policy = UrlPolicy(frozenset({"example.com"}))
    page = policy.decide("https://cdn.example.net/gallery", target_kind="page")
    media = policy.decide(
        "https://cdn.example.net/photo.jpg",
        target_kind="media",
        source_url="https://example.com/article",
    )
    assert (page.accepted, page.reason) == (False, "host_out_of_scope")
    assert (media.accepted, media.reason) == (False, "host_out_of_scope")


def test_external_media_requires_in_scope_parent() -> None:
    decision = UrlPolicy(frozenset({"example.com"})).decide(
        "https://cdn.example.net/photo.jpg",
        target_kind="media",
        source_url="https://outside.test/article",
    )
    assert (decision.accepted, decision.reason) == (False, "host_out_of_scope")


def test_historical_external_media_is_rejected() -> None:
    decision = UrlPolicy(frozenset({"example.com"})).decide_historical(
        "https://cdn.test/photo.jpg", "image/jpeg"
    )
    assert (decision.accepted, decision.reason) == (False, "host_out_of_scope")
