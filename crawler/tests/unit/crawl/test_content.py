import pytest

from hust_crawler.crawl.content import (
    discover_html,
    extract_html,
    normalize_url,
    resource_kind,
)


DECLARED_UTF8_HTML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html><head><title>Thông báo</title></head>'
    '<body><main><p>Nội dung tiếng Việt.</p></main>'
    '<a href="/tin-moi">Tin mới</a></body></html>'
).encode("utf-8")


def test_extract_html_accepts_bytes_with_xml_encoding_declaration() -> None:
    result = extract_html(DECLARED_UTF8_HTML, "https://site.test/article", "utf-8")
    assert result["title"] == "Thông báo"
    assert result["text"] == "Nội dung tiếng Việt."


def test_discover_html_accepts_bytes_with_xml_encoding_declaration() -> None:
    assert discover_html(DECLARED_UTF8_HTML, "https://site.test/article", "utf-8") == [
        {"url": "https://site.test/tin-moi", "text": "Tin mới", "kind": "html", "rel": []}
    ]


def test_article_and_outgoing_links() -> None:
    result = extract_html(
        '<nav>Menu</nav><article><h1>Tin mới</h1><p>Nội dung.</p></article>'
        '<a href="/b"> B </a><a href="https://outside.test/c">C</a>',
        "https://site.test/a",
    )

    assert result["title"] == "Tin mới"
    assert "Nội dung." in result["text"] and "Menu" not in result["text"]
    assert result["links"] == [
        {"url": "https://site.test/b", "text": "B"},
        {"url": "https://outside.test/c", "text": "C"},
    ]


def test_query_and_encoding_survive() -> None:
    assert (
        normalize_url("/a%2Fb?page=2#part", "https://site.test/x")
        == "https://site.test/a%2Fb?page=2"
    )
    assert (
        normalize_url(
            "/find?q=a%2Fb&tag=one&tag=two&utm_source=newsletter&empty=",
            "https://site.test/x",
        )
        == "https://site.test/find?q=a%2Fb&tag=one&tag=two&empty="
    )


@pytest.mark.parametrize(
    "value",
    [
        "mailto:editor@site.test",
        "javascript:void(0)",
        "https://user:secret@site.test/private",
        "https://site.test:invalid/page",
        "//:443/missing-host",
    ],
)
def test_normalize_url_rejects_unsafe_or_malformed_values(value: str) -> None:
    assert normalize_url(value, "https://site.test/") is None


def test_html_base_image_labels_and_duplicate_href_labels() -> None:
    html = """
        <html><head><base href="https://cdn.site.test/news/"><title>  Site story </title></head>
        <body><main><p>First paragraph.</p><p>Second paragraph.</p></main>
        <a href="item?view=full#comments"><img alt="Photo story"></a>
        <a href="item?view=full">Read story</a>
        <a href="item?view=full">Read story</a>
        <a href="/labelled" aria-label="Accessible label"></a>
        <a href="/titled" title="Title fallback"></a></body></html>
    """

    result = extract_html(html, "https://site.test/original")

    assert result["title"] == "Site story"
    assert result["text"] == "First paragraph.\nSecond paragraph."
    assert result["links"] == [
        {"url": "https://cdn.site.test/news/item?view=full", "text": "Photo story"},
        {"url": "https://cdn.site.test/news/item?view=full", "text": "Read story"},
        {"url": "https://cdn.site.test/labelled", "text": "Accessible label"},
        {"url": "https://cdn.site.test/titled", "text": "Title fallback"},
    ]
    assert result["warnings"] == []


def test_malformed_html_preserves_paragraph_boundaries() -> None:
    result = extract_html(
        "<main><p>Paragraph one.<!-- editorial note --><p>Paragraph two."
        "<aside>Related links</aside></main>",
        "https://site.test/page",
    )

    assert result["text"] == "Paragraph one.\nParagraph two."


def test_missing_title_and_text_emit_warnings() -> None:
    result = extract_html(
        '<html><head><title> </title></head><body><nav>Menu</nav><script>noise()</script></body></html>',
        "https://site.test/empty",
    )

    assert result["title"] == ""
    assert result["text"] == ""
    assert result["links"] == []
    assert result["warnings"] == ["missing_title", "missing_text"]


def test_discovery_includes_media_and_srcset_but_discards_transients() -> None:
    html = """
        <base href="https://assets.site.test/v1/">
        <a href="/article?id=1">Article</a>
        <a href="/article?id=2">Article</a>
        <img src="hero.jpg?size=large" alt="Hero"
             srcset="hero-small.jpg 1x, hero-large.webp?crop=wide 2x">
        <img src="/index.php?second=cronjobs&amp;p=random" alt="cron">
        <video src="movie.mp4" poster="poster.png"></video>
        <source src="movie.webm" srcset="still-480.jpg 480w, still-960.jpg 960w">
        <script src="bundle"></script>
        <link rel="stylesheet" href="styles">
        <link rel="preload" as="font" href="font-file">
    """

    assert discover_html(html, "https://site.test/page") == [
        {"url": "https://assets.site.test/article?id=1", "text": "Article", "kind": "html", "rel": []},
        {"url": "https://assets.site.test/article?id=2", "text": "Article", "kind": "html", "rel": []},
        {
            "url": "https://assets.site.test/v1/hero.jpg?size=large",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/v1/hero-small.jpg",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/v1/hero-large.webp?crop=wide",
            "text": "Hero",
            "kind": "file",
            "rel": [],
        },
        {
            "url": "https://assets.site.test/index.php?second=cronjobs&p=random",
            "text": "cron",
            "kind": "file",
            "rel": [],
        },
        {"url": "https://assets.site.test/v1/movie.mp4", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/poster.png", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/movie.webm", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/still-480.jpg", "text": "", "kind": "file", "rel": []},
        {"url": "https://assets.site.test/v1/still-960.jpg", "text": "", "kind": "file", "rel": []},
    ]


@pytest.mark.parametrize(
    ("url", "content_type", "expected"),
    [
        ("https://site.test/story", "", "html"),
        ("https://site.test/index.HTML?view=1", "", "html"),
        ("https://site.test/report.pdf", "", "file"),
        ("https://site.test/photo.JPG", "", "file"),
        ("https://site.test/movie.mp4", "", "file"),
        ("https://site.test/app.js", "", "transient"),
        ("https://site.test/font.woff2", "", "transient"),
        ("https://site.test/report.pdf", "text/html; charset=utf-8", "html"),
        ("https://site.test/download", "application/pdf", "file"),
        ("https://site.test/index.html", "image/jpeg", "file"),
        ("https://site.test/photo.jpg", "text/css", "transient"),
        ("https://site.test/font", "application/font-woff", "transient"),
        ("https://site.test/archive.unrecognized", "", "unknown"),
    ],
)
def test_resource_kind_uses_mime_before_extension(
    url: str, content_type: str, expected: str
) -> None:
    assert resource_kind(url, content_type) == expected


def test_legacy_title_text_and_links_fields_remain_present() -> None:
    from hust_crawler.crawl.article import analyze_article

    article = analyze_article("<main><h1>A</h1><p>B</p><a href='/c'>C</a></main>", "https://a.test/").article
    assert article["title"] == "A"
    assert article["text"] == "A\nB\nC"
    assert article["links"] == [{"url": "https://a.test/c", "text": "C"}]
