from __future__ import annotations

import pytest

from hust_crawler.crawl.frontier import Candidate, FrontierDecision, FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.state import FamilyObservation


def candidate(
    url: str,
    *,
    source: str = "html_link",
    discovered_from: str | None = None,
    link_kind: str | None = None,
) -> Candidate:
    return Candidate(
        url=url,
        source_mode="recursive",
        discovered_from=discovered_from if discovered_from is not None else "https://a.test/",
        kind="html",
        purpose="page",
        discovery_source=source,
        link_kind=link_kind,
    )


def test_family_closes_on_repeated_fingerprint() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    first = frontier.observe_family(FamilyObservation("f", "pagination", 2, "same", "t1", ("/a",)))
    second = frontier.observe_family(FamilyObservation("f", "pagination", 3, "same", "t2", ("/b",)))
    assert first.closed is False
    assert (second.closed, second.closure_reason) == (True, "repeated_content_fingerprint")


def test_hard_host_limit_applies_to_sitemap_and_html_sources() -> None:
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    frontier = FrontierPolicy(seeds, CrawlOptions(max_urls_per_host=2))
    assert frontier.consider(candidate("https://a.test/a", source="sitemap")).action == "schedule"
    assert frontier.consider(candidate("https://a.test/b")).action == "schedule"
    stopped = frontier.consider(candidate("https://a.test/c"))
    assert (stopped.action, stopped.reason) == ("reject", "host_url_limit")


def test_exact_hostname_boundary_rejects_subdomain_and_lookalike() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )
    for url in ("https://sub.a.test/x", "https://a.test.evil.test/x"):
        decision = frontier.consider(candidate(url))
        assert (decision.action, decision.reason) == ("reject", "host_out_of_scope")


def test_navigation_query_variants_stop_at_twenty() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )
    decisions = [
        frontier.consider(candidate(f"https://a.test/category/news?sort={i}"))
        for i in range(21)
    ]
    assert all(d.action == "schedule" for d in decisions[:20])
    assert (decisions[20].action, decisions[20].reason) == (
        "reject", "query_variant_limit"
    )


def test_root_path_query_variants_stop_at_twenty() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )
    decisions = [
        frontier.consider(candidate(f"https://a.test/?sort={i}"))
        for i in range(21)
    ]
    assert all(d.action == "schedule" for d in decisions[:20])
    assert (decisions[20].action, decisions[20].reason) == (
        "reject", "query_variant_limit"
    )


def test_semantic_pagination_is_not_limited_to_twenty_query_variants() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )

    decisions = [
        frontier.consider(
            candidate(
                f"https://a.test/news?page={number}&tag=undergraduate",
                link_kind="pagination",
            )
        )
        for number in range(1, 22)
    ]

    assert all(decision.action == "schedule" for decision in decisions)


def test_semantic_pagination_does_not_charge_query_variant_budget() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )
    for number in range(1, 21):
        decision = frontier.consider(
            candidate(
                f"https://a.test/news?page={number}",
                link_kind="pagination",
            )
        )
        assert decision.action == "schedule"

    decision = frontier.consider(candidate("https://a.test/news?sort=new"))

    assert decision.action == "schedule"


def test_wordpress_shortlinks_are_not_limited_as_navigation_queries() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )

    decisions = [
        frontier.consider(
            candidate(f"https://a.test/?p={post_id}", link_kind="content")
        )
        for post_id in range(1, 22)
    ]

    assert all(decision.action == "schedule" for decision in decisions)


def test_semantic_content_receives_content_priority() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )

    decision = frontier.consider(
        candidate("https://a.test/?p=33214", link_kind="content")
    )

    assert decision.priority == 500


def test_restore_does_not_charge_pagination_against_query_variant_budget() -> None:
    records = [
        {
            "url": f"https://a.test/news?page={number}",
            "frontier_action": "scheduled",
            "seed_type": "recursive",
            "discovery_source": "html_link",
            "link_kind": "pagination",
        }
        for number in range(1, 21)
    ]
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )

    frontier.restore(records)

    decision = frontier.consider(candidate("https://a.test/news?sort=new"))
    assert decision.action == "schedule"



def test_three_empty_pages_close_only_the_same_pagination_branch() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions()
    )
    for number in range(1, 4):
        url = f"https://a.test/news?page={number}"
        assert frontier.consider(candidate(url)).action == "schedule"
        frontier.observe_pagination(url, new_content_count=0)
    assert frontier.consider(candidate("https://a.test/news?page=4")).reason == "pagination_closed"
    assert frontier.consider(candidate("https://a.test/events?page=1")).action == "schedule"


def test_restore_rebuilds_dedup_and_all_budgets_idempotently() -> None:
    records = [
        {
            "url": "https://a.test/category/news?sort=1",
            "frontier_action": "scheduled",
            "seed_type": "recursive",
            "discovery_source": "html_link",
        }
    ]
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()),
        CrawlOptions(max_urls_per_host=1),
    )
    frontier.restore(records)
    frontier.restore(records)
    assert frontier.snapshot()["total_scheduled"] == 1
    assert frontier.consider(candidate("https://a.test/other")).reason == "host_url_limit"


def test_duplicate_canonical_urls_return_record_only() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    assert frontier.consider(candidate("https://a.test/page?a=1&b=2")).action == "schedule"
    decision = frontier.consider(candidate("https://a.test/page?b=2&a=1"))
    assert decision.action == "record_only"
    assert decision.reason == "already_scheduled"


def test_explicit_urls_do_not_expand_another_hostname() -> None:
    seeds = SeedSet(frozenset({"a.test"}), ("https://b.test/seed",), ())
    frontier = FrontierPolicy(seeds, CrawlOptions())
    # Explicit seed for b.test is allowed when explicit=True
    explicit_decision = frontier.consider(
        Candidate(
            url="https://b.test/seed",
            source_mode="exact",
            discovered_from=None,
            kind="html",
            purpose="page",
            explicit=True,
            discovery_source="seed",
        )
    )
    assert explicit_decision.action == "schedule"

    # Discovered candidate with exact source_mode cannot expand
    child = Candidate(
        url="https://b.test/child",
        source_mode="exact",
        discovered_from="https://b.test/seed",
        kind="html",
        purpose="page",
        explicit=False,
        discovery_source="html_link",
    )
    assert (frontier.consider(child).action, frontier.consider(child).reason) == (
        "reject",
        "exact_seed_no_expansion",
    )

    # Discovered asset attachment on exact seed host is allowed
    doc_child = Candidate(
        url="https://b.test/doc.pdf",
        source_mode="exact",
        discovered_from="https://b.test/seed",
        kind="file",
        purpose="asset",
        explicit=False,
        discovery_source="html_link",
    )
    assert frontier.consider(doc_child).action == "schedule"

    # Discovered asset attachment where parent host is not allowed is rejected
    external_doc = Candidate(
        url="https://other.test/doc.pdf",
        source_mode="exact",
        discovered_from="https://unallowed.test/seed",
        kind="file",
        purpose="asset",
        explicit=False,
        discovery_source="html_link",
    )
    assert (frontier.consider(external_doc).action, frontier.consider(external_doc).reason) == (
        "reject",
        "host_out_of_scope",
    )

    # Candidate attempting to expand into a different unallowed host
    out_of_scope = Candidate(
        url="https://other.test/page",
        source_mode="recursive",
        discovered_from="https://a.test/home",
        kind="html",
        purpose="page",
        explicit=False,
        discovery_source="html_link",
    )
    assert (frontier.consider(out_of_scope).action, frontier.consider(out_of_scope).reason) == (
        "reject",
        "host_out_of_scope",
    )


def test_content_urls_outrank_navigation_urls() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    robots_decision = frontier.consider(
        Candidate(
            url="https://a.test/robots.txt",
            source_mode="recursive",
            discovered_from=None,
            kind="html",
            purpose="robots",
        )
    )
    sitemap_decision = frontier.consider(
        candidate("https://a.test/article-1", source="sitemap")
    )
    content_decision = frontier.consider(
        candidate("https://a.test/article-2", source="html_link")
    )
    navigation_decision = frontier.consider(
        candidate("https://a.test/category/news", source="html_link")
    )
    assert robots_decision.priority == 1000
    assert sitemap_decision.priority == 800
    assert content_decision.priority == 500
    assert navigation_decision.priority == 100
    assert content_decision.priority > navigation_decision.priority


def test_malformed_urls_are_rejected() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    d1 = frontier.consider(candidate("not-a-url"))
    assert d1.action == "reject"
    assert d1.canonical_url is None
    d2 = frontier.consider(candidate("ftp://a.test/file"))
    assert d2.action == "reject"
    assert d2.reason == "unsupported_scheme"


def test_global_limit_returns_total_url_limit() -> None:
    seeds = SeedSet(frozenset({"a.test", "b.test"}), (), ())
    frontier = FrontierPolicy(seeds, CrawlOptions(max_urls_per_host=10, max_total_urls=2))
    assert frontier.consider(candidate("https://a.test/1")).action == "schedule"
    assert frontier.consider(candidate("https://b.test/1", discovered_from="https://b.test/")).action == "schedule"
    stopped = frontier.consider(candidate("https://a.test/2"))
    assert (stopped.action, stopped.reason) == ("reject", "total_url_limit")



def test_page_role_classification() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"lms.hust.edu.vn", "a.test"}), (), ()), CrawlOptions())
    assert frontier.page_role("https://lms.hust.edu.vn/") == "content"
    assert frontier.page_role("https://lms.hust.edu.vn/course/view.php?id=2") == "content"
    assert frontier.page_role("https://lms.hust.edu.vn/news/detail-slug") == "content"
    assert frontier.page_role("https://lms.hust.edu.vn/news?page=2") == "navigation"
    assert frontier.page_role("https://lms.hust.edu.vn/courses/list") == "navigation"
    assert frontier.page_role("https://lms.hust.edu.vn/category/cs") == "navigation"
    assert frontier.page_role("https://a.test/?sort=1") == "navigation"
    assert frontier.page_role("https://a.test/?page=2") == "navigation"


def test_pagination_branch_trailing_slash_normalization() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    branch1 = frontier._pagination_branch("https://a.test/news?page=1")
    branch2 = frontier._pagination_branch("https://a.test/news/?page=1")
    assert branch1 == branch2 == ("a.test", "/news", "")


def test_frontier_policy_handles_none_trap_exceptions() -> None:
    frontier = FrontierPolicy(
        SeedSet(frozenset({"a.test"}), (), ()),
        CrawlOptions(),
        trap_exceptions=None,
    )
    assert frontier.trap_exceptions == {}



@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://lms.hust.edu.vn/login", "auth_trap"),
        ("https://lms.hust.edu.vn/logout", "logout_path"),
        ("https://lms.hust.edu.vn/search?q=info", "search_trap"),
        ("https://lms.hust.edu.vn/events/calendar.ics", "calendar_export_trap"),
        ("https://lms.hust.edu.vn/news/feed", "feed_trap"),
        ("https://lms.hust.edu.vn/wp-json/wp/v2/posts", "api_endpoint_trap"),
        ("https://lms.hust.edu.vn/article/1/print", "action_endpoint_trap"),
    ],
)
def test_path_and_action_traps_are_rejected(url: str, reason: str) -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"lms.hust.edu.vn"}), (), ()), CrawlOptions())
    decision = frontier.consider(
        candidate(url, source="html_link", discovered_from="https://lms.hust.edu.vn/")
    )
    assert decision.action == "reject"
    assert decision.reason == reason


def test_trap_exceptions_allow_configured_traps() -> None:
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    frontier = FrontierPolicy(
        seeds,
        CrawlOptions(),
        trap_exceptions={"a.test": ("auth_trap",)},
    )
    decision = frontier.consider(candidate("https://a.test/login"))
    assert decision.action == "schedule"



def test_observe_pagination_resets_on_content() -> None:
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    frontier = FrontierPolicy(seeds, CrawlOptions())
    url1 = "https://a.test/news?page=1"
    url2 = "https://a.test/news?page=2"
    url3 = "https://a.test/news?page=3"
    frontier.consider(candidate(url1))
    frontier.observe_pagination(url1, new_content_count=0)
    frontier.consider(candidate(url2))
    frontier.observe_pagination(url2, new_content_count=0)
    # new content resets the counter
    frontier.observe_pagination(url2, new_content_count=5)
    frontier.consider(candidate(url3))
    frontier.observe_pagination(url3, new_content_count=0)
    # fourth page should still be schedulable because streak was broken
    assert frontier.consider(candidate("https://a.test/news?page=4")).action == "schedule"


def test_restore_ignores_non_scheduled() -> None:
    records = [
        {"url": "https://a.test/rej", "frontier_action": "rejected", "seed_type": "recursive"},
        {"url": "https://a.test/rec", "frontier_action": "record_only", "seed_type": "recursive"},
        {"url": "https://a.test/1", "frontier_action": "scheduled", "seed_type": "recursive", "discovery_source": "html_link"},
    ]
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    frontier.restore(records)
    assert frontier.snapshot()["total_scheduled"] == 1


def test_snapshot_structure() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    frontier.consider(candidate("https://a.test/1"))
    snap = frontier.snapshot()
    assert snap["total_scheduled"] == 1
    assert snap["host_scheduled"] == {"a.test": 1}
    assert snap["closed_pagination_branches"] == 0
    assert isinstance(snap["stats"], dict)
    assert snap["stats"]["frontier/scheduled"] == 1


def test_decision_type() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    decision = frontier.consider(candidate("https://a.test/1"))
    assert isinstance(decision, FrontierDecision)


def test_observe_pagination_stats_and_branches() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    url = "https://a.test/news?page=1"
    frontier.observe_pagination(url, new_content_count=0)
    assert frontier.snapshot()["stats"]["frontier/pagination_empty_observation"] == 1
    frontier.observe_pagination(url, new_content_count=2)
    assert frontier.snapshot()["stats"]["frontier/pagination_empty_observation"] == 1


def test_direct_external_asset_is_allowed_but_external_page_is_not() -> None:
    frontier = FrontierPolicy(SeedSet(frozenset({"a.test"}), (), ()), CrawlOptions())
    asset = frontier.consider(Candidate(
        "https://cdn.test/a.png", "recursive", "https://a.test/p", "image", "asset",
        discovery_source="html_link",
    ))
    page = frontier.consider(Candidate(
        "https://cdn.test/page", "recursive", "https://a.test/p", "html", "page",
        discovery_source="html_link",
    ))
    assert asset.action == "schedule"
    assert (page.action, page.reason) == ("reject", "host_out_of_scope")
