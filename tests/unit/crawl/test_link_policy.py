import pytest

from hust_crawler.crawl.link_policy import (
    ClassifiedLink,
    classify_link,
    route_family_key,
    select_semantic_next,
)


@pytest.mark.parametrize("url,text,kind", [
    ("https://a.test/article/123", "Chi tiết", "content"),
    ("https://a.test/view?id=123", "Xem", "content"),
    ("https://a.test/news?page=2", "2", "pagination"),
    ("https://a.test/search?q=x", "Tìm", "search_filter"),
    ("https://a.test/archive/2026/09", "Tháng 9", "calendar_archive"),
    ("https://a.test/logout", "Đăng xuất", "action_auth"),
    ("https://a.test/report.pdf", "PDF", "asset"),
])
def test_link_kinds(url: str, text: str, kind: str) -> None:
    assert classify_link(url, source_url="https://a.test/news", text=text).kind == kind


def test_route_family_preserves_content_ids() -> None:
    assert route_family_key("https://a.test/article/123", "content") is None
    assert route_family_key("https://a.test/view?id=123", "content") is None
    assert route_family_key("https://a.test/news?page=123", "pagination") == "a.test/news?page={page}"
    assert route_family_key("https://a.test/news/page/123", "pagination") == "a.test/news/page/{page}"
    assert route_family_key("https://a.test/archive/2026/09", "calendar_archive") == "a.test/archive/{year}/{month}"


def test_wordpress_shortlink_post_id_is_content() -> None:
    link = classify_link(
        "https://a.test/?p=33214",
        source_url="https://a.test/news/an-article/",
        rel=("shortlink",),
    )

    assert (link.kind, link.ordinal, link.family_key) == ("content", None, None)


def test_wordpress_post_id_anchor_is_content() -> None:
    link = classify_link(
        "https://a.test/?p=33214",
        source_url="https://a.test/news/",
        text="FSS tuyển thực tập sinh",
    )

    assert (link.kind, link.ordinal, link.family_key) == ("content", None, None)


def test_embedded_resource_hint_overrides_extensionless_page_shape() -> None:
    link = classify_link(
        "https://a.test/index.php?second=cronjobs&p=random",
        source_url="https://a.test/news/",
        text="cron",
        resource_kind_hint="file",
    )

    assert link.kind == "asset"


def test_numeric_p_anchor_remains_pagination() -> None:
    link = classify_link(
        "https://a.test/?p=2",
        source_url="https://a.test/",
        text="2",
    )

    assert (link.kind, link.ordinal) == ("pagination", 2)


def test_p_anchor_with_next_label_is_pagination() -> None:
    link = classify_link(
        "https://a.test/?p=2",
        source_url="https://a.test/",
        text="Older Posts",
    )

    assert (link.kind, link.ordinal) == ("pagination", 2)


def test_shortlink_relation_overrides_other_pagination_signals() -> None:
    link = classify_link(
        "https://a.test/?p=33214",
        source_url="https://a.test/news/an-article/",
        text="33214",
        rel=("next", "shortlink"),
    )

    assert (link.kind, link.ordinal, link.family_key) == ("content", None, None)


def test_selects_only_one_semantic_next() -> None:
    links = (
        ClassifiedLink("https://a.test/news?page=2", "pagination", "a.test/news?page={page}", 2, False, None),
        ClassifiedLink("https://a.test/news?page=3", "pagination", "a.test/news?page={page}", 3, True, None),
        ClassifiedLink("https://a.test/news?page=99", "pagination", "a.test/news?page={page}", 99, False, None),
    )
    assert select_semantic_next(links, current_ordinal=2).url.endswith("page=3")
