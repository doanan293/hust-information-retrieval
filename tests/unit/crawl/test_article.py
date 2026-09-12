from hust_crawler.crawl.article import (
    ArticleAnalysis,
    ArticleAsset,
    analyze_article,
)


def test_article_asset_and_analysis_types() -> None:
    asset = ArticleAsset(url="https://a.test/img.png", role="inline_image", alt="image")
    assert asset.url == "https://a.test/img.png"
    assert asset.role == "inline_image"
    assert asset.alt == "image"


def test_visible_article_h1_wins_over_generic_document_title() -> None:
    result = analyze_article(
        '<html lang="vi"><head><title>Đại học Bách khoa Hà Nội</title><meta property="article:published_time" content="2026-09-08T08:00:00+07:00"></head><body><main><h1>Thông tin tuyển sinh 2026</h1><p>Nội dung.</p></main></body></html>',
        "https://hust.edu.vn/article",
    )
    assert isinstance(result, ArticleAnalysis)
    assert isinstance(result.assets, tuple)
    assert result.article["title"] == "Thông tin tuyển sinh 2026"
    assert result.article["language"] == "vi"
    assert result.article["published_at"] == "2026-09-08T08:00:00+07:00"


def test_og_title_used_when_no_visible_h1() -> None:
    html = '<head><title>Site</title><meta property="og:title" content="OG Headline"></head><body><main><p>Content</p></main></body>'
    result = analyze_article(html, "https://a.test/")
    assert result.article["title"] == "OG Headline"


def test_meta_author_and_modified_and_canonical() -> None:
    html = """
    <html>
    <head>
        <title>Title</title>
        <link rel="canonical" href="/canonical-path">
        <meta name="author" content="Nguyen Van A">
        <meta property="article:modified_time" content="2026-09-08T10:00:00Z">
        <meta name="description" content="A brief summary">
    </head>
    <body>
        <main><h1>Title</h1><p>Content</p></main>
    </body>
    </html>
    """
    result = analyze_article(html, "https://a.test/orig")
    assert result.article["canonical_url"] == "https://a.test/canonical-path"
    assert result.article["author"] == "Nguyen Van A"
    assert result.article["modified_at"] == "2026-09-08T10:00:00Z"
    assert result.article["summary"] == "A brief summary"


def test_time_datetime_extracts_published_at_when_meta_absent() -> None:
    html = """
    <html>
    <body>
        <article>
            <h1>Article</h1>
            <time datetime="2026-09-07T12:00:00+07:00">September 7</time>
            <p>Text</p>
        </article>
    </body>
    </html>
    """
    result = analyze_article(html, "https://a.test/")
    assert result.article["published_at"] == "2026-09-07T12:00:00+07:00"


def test_missing_metadata_fields_are_none_with_warnings() -> None:
    html = "<html><body><main><p>Only paragraph, no title</p></main></body></html>"
    result = analyze_article(html, "https://a.test/")
    assert result.article["title"] == ""
    assert result.article["language"] is None
    assert result.article["author"] is None
    assert result.article["published_at"] is None
    assert "missing_title" in result.article["warnings"]


def test_ambiguous_canonical_and_published_at_produces_warning() -> None:
    html = """
    <html>
    <head>
        <link rel="canonical" href="/canonical-one">
        <link rel="canonical" href="/canonical-two">
        <meta property="article:published_time" content="2026-09-08T08:00:00Z">
        <meta property="og:published_time" content="2026-09-07T08:00:00Z">
    </head>
    <body><main><h1>Title</h1><p>Text</p></main></body>
    </html>
    """
    result = analyze_article(html, "https://a.test/")
    assert "ambiguous_canonical_url" in result.article["warnings"]
    assert "ambiguous_published_at" in result.article["warnings"]


def test_content_html_preserves_semantics_and_removes_executable_markup() -> None:
    source = '''<main onclick="steal()"><h1>Title</h1><p style="color:red">Hello <strong>world</strong>.</p><script>alert(1)</script><a href="javascript:alert(2)">bad</a><a href="/safe">safe</a><table><tr><th>Year</th><td>2026</td></tr></table></main>'''
    article = analyze_article(source, "https://a.test/base").article
    rendered = article["content_html"]
    assert "<h1>Title</h1>" in rendered
    assert "<strong>world</strong>" in rendered
    assert "<table>" in rendered
    assert 'href="https://a.test/safe"' in rendered
    assert "script" not in rendered
    assert "onclick" not in rendered
    assert "style=" not in rendered
    assert "javascript:" not in rendered


def test_content_html_replaces_xml_control_characters_without_dropping_markup() -> None:
    source = (
        b"<main><h1>Title</h1><p>Before <span>hello\x1dworld</span> after</p></main>"
    )

    article = analyze_article(source, "https://a.test/article").article

    assert "<h1>Title</h1>" in article["content_html"]
    assert "<p>Before hello world after</p>" in article["content_html"]
    assert "\x1d" not in article["content_html"]
    assert article["text"] == "Title\nBefore hello world after"


def test_content_html_replaces_xml_control_characters_in_allowed_attributes() -> None:
    source = (
        b'<main><h1>Title</h1><p><a href="/safe" title="hello\x1dworld">Link</a></p></main>'
    )

    article = analyze_article(source, "https://a.test/article").article

    assert 'href="https://a.test/safe"' in article["content_html"]
    assert 'title="hello world"' in article["content_html"]


def test_content_html_handles_semantic_elements_and_malicious_cases() -> None:
    source = """
    <article>
        <!-- A comment to remove -->
        <h1>Semantics</h1>
        <ol start="1">
            <li>Item 1</li>
        </ol>
        <blockquote><p>Quote</p></blockquote>
        <pre><code>x = 1</code></pre>
        <figure>
            <img src="/img.png" alt="Pic" width="100" height="100">
            <figcaption>Caption</figcaption>
        </figure>
        <table>
            <thead>
                <tr><th colspan="2" rowspan="1">Header</th></tr>
            </thead>
            <tbody>
                <tr><td>1</td><td>2</td></tr>
            </tbody>
        </table>
        <form action="/submit"><input type="text"><button>Submit</button></form>
        <iframe src="https://evil.test/frame"></iframe>
        <svg><circle cx="50" cy="50" r="40"/></svg>
        <div hidden>Secret hidden</div>
        <p aria-hidden="true">Aria hidden</p>
        <a HREF="JAVASCRIPT:evil()" OnClick="alert(3)">Bad Link</a>
    </article>
    """
    article = analyze_article(source, "https://a.test/").article
    html = article["content_html"]

    # Allowed elements preserved
    assert "<h1>Semantics</h1>" in html
    assert '<ol start="1">' in html
    assert "<li>Item 1</li>" in html
    assert "<blockquote>" in html
    assert "<pre><code>x = 1</code></pre>" in html
    assert '<img src="https://a.test/img.png" alt="Pic" width="100" height="100">' in html
    assert "<figcaption>Caption</figcaption>" in html
    assert '<th colspan="2" rowspan="1">Header</th>' in html
    assert "<td>1</td>" in html

    # Excluded elements & comments dropped
    assert "<!--" not in html
    assert "<form" not in html
    assert "<input" not in html
    assert "<button" not in html
    assert "<iframe" not in html
    assert "<svg" not in html
    assert "Secret hidden" not in html
    assert "Aria hidden" not in html
    assert "javascript:" not in html.lower()
    assert "onclick" not in html.lower()
    assert "alert(3)" not in html


def test_blocks_preserve_order_and_heading_paths() -> None:
    article = analyze_article(
        '<main><h1>A</h1><p>Intro</p><h2>B</h2><ul><li>One</li><li>Two</li></ul><blockquote>Quote</blockquote></main>',
        "https://a.test/",
    ).article
    assert [(b["type"], b.get("text")) for b in article["content_blocks"]] == [
        ("heading", "A"),
        ("paragraph", "Intro"),
        ("heading", "B"),
        ("list", None),
        ("quote", "Quote"),
    ]
    assert article["content_blocks"][3]["heading_path"] == ["A", "B"]
    assert article["text"] == "A\nIntro\nB\nOne\nTwo\nQuote"


def test_blocks_heading_hierarchy_and_replacement() -> None:
    # Test skipped levels (h1 -> h3), and replacement of same-level heading
    html = """
    <main>
        <h1>Main Title</h1>
        <p>Under h1</p>
        <h3>Skipped to Sub-sub</h3>
        <p>Under h3</p>
        <h3>Sibling Sub-sub</h3>
        <p>Under sibling h3</p>
        <h1>New Main Title</h1>
        <p>Under new h1</p>
    </main>
    """
    article = analyze_article(html, "https://a.test/").article
    blocks = article["content_blocks"]
    assert [b["heading_path"] for b in blocks if b["type"] == "paragraph"] == [
        ["Main Title"],
        ["Main Title", "Skipped to Sub-sub"],
        ["Main Title", "Sibling Sub-sub"],
        ["New Main Title"],
    ]
    assert article["headings"] == [
        {"level": 1, "text": "Main Title", "heading_path": ["Main Title"]},
        {"level": 3, "text": "Skipped to Sub-sub", "heading_path": ["Main Title", "Skipped to Sub-sub"]},
        {"level": 3, "text": "Sibling Sub-sub", "heading_path": ["Main Title", "Sibling Sub-sub"]},
        {"level": 1, "text": "New Main Title", "heading_path": ["New Main Title"]},
    ]


def test_blocks_table_figure_code_and_nested_containers() -> None:
    html = """
    <article>
        <h1>Article</h1>
        <section>
            <div>
                <pre><code>def foo():\n    return 42</code></pre>
            </div>
        </section>
        <figure>
            <img src="/chart.png" alt="Chart">
            <figcaption>Performance Chart</figcaption>
        </figure>
        <table>
            <thead><tr><th>Metric</th><th>Value</th></tr></thead>
            <tbody><tr><td>Speed</td><td>Fast</td></tr></tbody>
        </table>
        <hr>
    </article>
    """
    article = analyze_article(html, "https://a.test/").article
    blocks = article["content_blocks"]
    types = [b["type"] for b in blocks]
    assert types == ["heading", "code", "figure", "table", "separator"]

    code_block = blocks[1]
    assert code_block["text"] == "def foo():\n    return 42"

    fig_block = blocks[2]
    assert fig_block["asset_url"] == "https://a.test/chart.png"
    assert fig_block["alt"] == "Chart"
    assert fig_block["caption"] == "Performance Chart"

    table_block = blocks[3]
    assert table_block["headers"] == [["Metric", "Value"]]
    assert table_block["rows"] == [["Speed", "Fast"]]

    # Synchronized text
    assert article["text"] == (
        "Article\n"
        "def foo():\n"
        "    return 42\n"
        "Performance Chart\n"
        "Metric Value\n"
        "Speed Fast"
    )


def test_article_assets_include_only_selected_same_host_images_and_documents() -> None:
    source = '''<body><header><img src="/logo.png"></header><main><h1>A</h1><figure><img src="/photo.jpg" alt="Photo"><figcaption>Caption</figcaption></figure><a href="/report.pdf">Report</a><img src="https://cdn.test/external.jpg"></main></body>'''
    result = analyze_article(source, "https://a.test/article")
    assert [(a.url, a.role, a.external) for a in result.assets] == [
        ("https://a.test/photo.jpg", "inline_image", False),
        ("https://a.test/report.pdf", "document_attachment", False),
        ("https://cdn.test/external.jpg", "inline_image", True),
    ]
    assert any("external_asset:https://cdn.test/external.jpg" in w for w in result.article["warnings"])


def test_extracts_every_supported_semantic_asset_source() -> None:
    analysis = analyze_article(
        """
        <main><h1>T</h1><p>Nội dung đủ dài cho bài viết.</p>
          <img src="/a.jpg" srcset="/a-2.jpg 2x, /a-3.webp 3x" alt="Ảnh A">
          <picture><source srcset="/p.avif 1x, /p.webp 2x"></picture>
          <video src="/v.mp4" poster="/poster.png"><source src="/v.webm"></video>
          <audio src="/a.mp3"><source src="/a.ogg"></audio>
          <a href="/report.pdf">Báo cáo</a>
          <object data="/sheet.xlsx" type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></object>
          <embed src="/slides.pptx" type="application/vnd.openxmlformats-officedocument.presentationml.presentation">
          <iframe src="/frame.html"></iframe>
        </main>
        """,
        "https://a.test/news/1",
    )
    by_url = {asset.url: asset.role for asset in analysis.assets}
    assert by_url == {
        "https://a.test/a.jpg": "inline_image",
        "https://a.test/a-2.jpg": "inline_image",
        "https://a.test/a-3.webp": "inline_image",
        "https://a.test/p.avif": "inline_image",
        "https://a.test/p.webp": "inline_image",
        "https://a.test/v.mp4": "media_reference",
        "https://a.test/poster.png": "inline_image",
        "https://a.test/v.webm": "media_reference",
        "https://a.test/a.mp3": "media_reference",
        "https://a.test/a.ogg": "media_reference",
        "https://a.test/report.pdf": "document_attachment",
        "https://a.test/sheet.xlsx": "document_attachment",
        "https://a.test/slides.pptx": "document_attachment",
    }
    assert "https://a.test/frame.html" not in by_url


def test_asset_metadata_is_mode_independent_and_role_dedup_is_stable() -> None:
    analysis = analyze_article(
        '<main><h1>T</h1><p>Body</p><img src="/x.jpg" srcset="/x.jpg 2x" alt="Sơ đồ"></main>',
        "https://a.test/p",
    )
    assert len(analysis.assets) == 1
    assert analysis.article["assets"] == [{
        "url": "https://a.test/x.jpg",
        "role": "inline_image",
        "alt": "Sơ đồ",
        "caption": "",
        "external": False,
    }]
