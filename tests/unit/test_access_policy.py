from hust_crawler.access_policy import classify_access
from hust_crawler.models import AccessDecision


def test_public_article_with_login_link_remains_public() -> None:
    html = '<article><h1>Public</h1><p>Body text</p></article><a href="/login">Login</a>'
    assert classify_access(
        status=200, url="https://example.com/article", html=html, rendered=False
    ) == AccessDecision("public", False, None)


def test_access_gated_login_page_is_terminal() -> None:
    html = '<main><form action="/login"><input type="password"></form></main>'
    decision = classify_access(
        status=200, url="https://example.com/private", html=html, rendered=False
    )
    assert (decision.outcome, decision.escalate_playwright) == ("login_required", False)


def test_html_shell_escalates_only_before_rendering() -> None:
    html = '<html><body><div id="app"></div><script src="/app.js"></script></body></html>'
    assert classify_access(
        status=200, url="https://example.com/news", html=html, rendered=False
    ).escalate_playwright
    assert not classify_access(
        status=200, url="https://example.com/news", html=html, rendered=True
    ).escalate_playwright


def test_interactive_captcha_is_terminal() -> None:
    decision = classify_access(
        status=200,
        url="https://example.com/news",
        html='<div class="g-recaptcha"></div>',
        rendered=True,
    )
    assert decision.outcome == "captcha_blocked"
