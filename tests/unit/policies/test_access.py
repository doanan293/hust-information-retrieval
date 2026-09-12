import pytest

from hust_crawler.policies.access import classify_access, classify_page_gate


@pytest.mark.parametrize(
    ("status", "html", "outcome"),
    [
        (401, "", "login_required"),
        (403, "", "login_required"),
        (200, '<form action="/login"><input type="password"></form>', "login_required"),
        (200, '<div class="g-recaptcha"></div>', "captcha_blocked"),
    ],
)
def test_access_gates_are_terminal(status: int, html: str, outcome: str) -> None:
    decision = classify_access(status=status, url="https://example.com/", html=html, rendered=False)
    assert (decision.outcome, decision.use_playwright) == (outcome, False)


def test_public_shell_requests_browser_once() -> None:
    html = '<div id="app"></div><script src="/app.js"></script>'
    assert classify_access(status=200, url="https://example.com/", html=html, rendered=False).use_playwright
    assert not classify_access(status=200, url="https://example.com/", html=html, rendered=True).use_playwright


def test_public_article_that_mentions_captcha_is_not_treated_as_a_challenge() -> None:
    html = "<article><h1>CAPTCHA accessibility research</h1><p>Public report.</p></article>"

    decision = classify_access(
        status=200,
        url="https://example.com/research",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_comment_form_captcha_does_not_block_public_article() -> None:
    html = """
        <div id="news-body"><h1>Public article</h1><p>Useful public content.</p></div>
        <div id="formcomment" class="comment-form">
          <form action="/vi/comment/post/">
            <img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">
            <input name="code">
          </form>
        </div>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_captcha_in_public_contact_form_does_not_block_page() -> None:
    html = """
        <main>
          <h1>Contact the faculty</h1>
          <p>Our address, telephone number, and office hours are public information.</p>
          <form action="/en/contact/">
            <label>Your message</label><textarea name="message"></textarea>
            <img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">
          </form>
        </main>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/en/contact/",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


def test_captcha_flood_blocker_remains_an_access_gate() -> None:
    html = """
        <header>Faculty website navigation and other public chrome text.</header>
        <div class="floodblocker">
          <form id="formPassFlood" action="/">
            <p>Too many requests. Verify that you are human.</p>
            <div class="g-recaptcha"></div>
          </form>
        </div>
    """

    decision = classify_access(
        status=429,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "captcha_blocked"


def test_http_429_captcha_remains_an_access_gate_without_vendor_markup() -> None:
    html = """
        <header>Faculty website navigation and other public chrome text.</header>
        <form action="/">
          <p>Please verify that you are human.</p>
          <div class="g-recaptcha"></div>
        </form>
    """

    decision = classify_access(
        status=429,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "captcha_blocked"


def test_captcha_library_script_does_not_block_public_article() -> None:
    html = """
        <main><h1>Public article</h1><p>Useful public content.</p></main>
        <script src="https://captcha.example/api.js"></script>
    """

    decision = classify_access(
        status=200,
        url="https://example.com/article",
        html=html,
        rendered=False,
    )

    assert decision.outcome == "public"


@pytest.mark.parametrize("html,reason", [
    ("<main><h1>404</h1><p>Không tìm thấy nội dung</p></main>", "soft_404"),
    ('<form action="/login"><input type="password"></form>', "login"),
    ('<main><div class="g-recaptcha"></div></main>', "captcha"),
    ("<main></main>", "empty"),
])
def test_page_gate_blocks_noncontent_shells(html: str, reason: str) -> None:
    assert classify_page_gate(status=200, url="https://a.test/p", html=html, text="").reason == reason
