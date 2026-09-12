from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import lxml.html as lxml_html


@dataclass(frozen=True, slots=True)
class AccessDecision:
    outcome: str
    use_playwright: bool
    reason: str | None


PageGateReason = Literal["soft_404", "login", "captcha", "empty", "unsupported"]


@dataclass(frozen=True, slots=True)
class PageGateDecision:
    expandable: bool
    reason: PageGateReason | None


_LOGIN_PATH_MARKERS = ("/login", "/signin", "/auth/")
_CAPTCHA_GATE_MARKERS = (
    "access-denied",
    "cf-challenge",
    "challenge-form",
    "challenge-stage",
    "floodblock",
    "formpassflood",
    "human-verification",
    "verify-human",
)


def _is_comment_scoped(element: lxml_html.HtmlElement) -> bool:
    for ancestor in (element, *element.iterancestors()):
        context = " ".join(
            (
                ancestor.get("id", ""),
                ancestor.get("class", ""),
                ancestor.get("action", ""),
            )
        ).casefold()
        if "comment" in context:
            return True
    return False


def _has_explicit_gate_context(element: lxml_html.HtmlElement) -> bool:
    for ancestor in (element, *element.iterancestors()):
        context = " ".join(
            (
                ancestor.get("id", ""),
                ancestor.get("class", ""),
                ancestor.get("action", ""),
            )
        ).casefold()
        if any(marker in context for marker in _CAPTCHA_GATE_MARKERS):
            return True
    return False


def _is_embedded_in_public_form(
    root: lxml_html.HtmlElement, element: lxml_html.HtmlElement
) -> bool:
    if not any(
        isinstance(ancestor.tag, str) and ancestor.tag.casefold() == "form"
        for ancestor in element.iterancestors()
    ):
        return False
    public_text = " ".join(
        str(value).strip()
        for value in root.xpath(
            ".//text()[not(ancestor::form) and not(ancestor::script) "
            "and not(ancestor::style) and not(ancestor::noscript)]"
        )
        if str(value).strip()
    )
    return len(" ".join(public_text.split())) >= 40


def _has_page_level_captcha(html: str, *, rate_limited: bool = False) -> bool:
    try:
        root = lxml_html.fromstring(html or "<html></html>")
    except (TypeError, ValueError):
        return False
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.lower() in {"script", "link"}:
            continue
        attributes = " ".join(str(value) for value in element.attrib.values()).casefold()
        if "captcha" in attributes or "cf-turnstile" in attributes:
            if rate_limited or _has_explicit_gate_context(element):
                return True
            if _is_comment_scoped(element) or _is_embedded_in_public_form(root, element):
                continue
            return True
    return False


def classify_access(*, status: int, url: str, html: str, rendered: bool) -> AccessDecision:
    lowered = html.lower()
    has_captcha_widget = _has_page_level_captcha(html, rate_limited=status == 429)
    if has_captcha_widget:
        return AccessDecision("captcha_blocked", False, "interactive_captcha")
    has_password = bool(re.search(r"type\s*=\s*['\"]password['\"]", lowered))
    has_article = any(marker in lowered for marker in ("<article", "<main", 'itemprop="articlebody"'))
    has_login_action = bool(re.search(r"action\s*=\s*['\"][^'\"]*(?:login|signin|auth)", lowered))
    has_login_gate = has_password and (
        has_login_action
        or any(marker in url.lower() for marker in _LOGIN_PATH_MARKERS)
        or not has_article
    )
    if status in {401, 403}:
        if has_login_gate:
            return AccessDecision("login_required", False, f"http_{status}")
        return AccessDecision("access_denied", not rendered, f"http_{status}")
    if has_login_gate:
        return AccessDecision("login_required", False, "access_gate")
    visible = re.sub(r"<script\b[^>]*>.*?</script>", "", lowered, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", visible).strip()
    shell = len(text) < 40 and "<script" in lowered
    if shell and not rendered:
        return AccessDecision("public", True, "html_shell")
    return AccessDecision("public", False, None)


def html_title(html: str) -> str:
    root = lxml_html.fromstring(html or "<html></html>")
    values = root.xpath("//title/text() | //h1[1]//text()")
    return " ".join(str(value).strip() for value in values if str(value).strip())


def classify_page_gate(
    *, status: int, url: str, html: str, text: str, supported: bool = True
) -> PageGateDecision:
    if not supported:
        return PageGateDecision(False, "unsupported")
    access = classify_access(status=status, url=url, html=html, rendered=True)
    if access.outcome == "login_required":
        return PageGateDecision(False, "login")
    if access.outcome == "captcha_blocked":
        return PageGateDecision(False, "captcha")
    heading = html_title(html).casefold().strip()
    visible = " ".join((heading, text.casefold()))
    soft_markers = ("404", "not found", "page not found", "không tìm thấy", "nội dung không tồn tại")
    heading_is_error = heading in {"404", "not found", "page not found"} or heading.startswith("404 -")
    phrase_is_error = len(text.strip()) < 1000 and any(marker in visible for marker in soft_markers[1:])
    if heading_is_error or phrase_is_error:
        return PageGateDecision(False, "soft_404")
    if not text.strip():
        return PageGateDecision(False, "empty")
    return PageGateDecision(True, None)
