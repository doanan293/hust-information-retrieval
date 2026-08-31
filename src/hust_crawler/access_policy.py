from __future__ import annotations

import re

from .models import AccessDecision


_CAPTCHA_MARKERS = ("g-recaptcha", "hcaptcha", "cf-turnstile", "captcha")
_LOGIN_PATH_MARKERS = ("/login", "/signin", "/auth/")


def classify_access(*, status: int, url: str, html: str, rendered: bool) -> AccessDecision:
    lowered = html.lower()
    if status in {401, 403}:
        return AccessDecision("login_required", False, f"http_{status}")
    if any(marker in lowered for marker in _CAPTCHA_MARKERS):
        return AccessDecision("captcha_blocked", False, "interactive_captcha")
    has_password = bool(re.search(r"type\s*=\s*['\"]password['\"]", lowered))
    has_article = any(marker in lowered for marker in ("<article", "<main", "itemprop=\"articlebody\""))
    has_login_action = bool(re.search(r"action\s*=\s*['\"][^'\"]*(?:login|signin|auth)", lowered))
    if has_password and (has_login_action or any(marker in url.lower() for marker in _LOGIN_PATH_MARKERS) or not has_article):
        return AccessDecision("login_required", False, "access_gate")
    visible = re.sub(r"<script\b[^>]*>.*?</script>", "", lowered, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", visible).strip()
    shell = len(text) < 40 and "<script" in lowered
    if shell and not rendered:
        return AccessDecision("public", True, "html_shell")
    return AccessDecision("public", False, None)
