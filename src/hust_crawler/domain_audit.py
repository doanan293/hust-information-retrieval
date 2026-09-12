from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import http.client
import ipaddress
import json
import re
import socket  # noqa: F401
import ssl
from http.cookiejar import CookieJar

from hust_crawler.policies.network import NonPublicAddressError, resolve_hostname
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPSHandler,
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    hostname: str
    active: bool
    reason: str
    scheme: str | None = None
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    content_digest: str | None = None
    browser_rendered: bool = False
    error: str | None = None
    checked_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class ProbeAttempt:
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    body: bytes = b""
    error: str | None = None




class UnsafeRedirectError(OSError):
    def __init__(self, target_url: str, reason: str) -> None:
        super().__init__(f"{reason}: {target_url}")
        self.target_url = target_url
        self.reason = reason


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_url = urljoin(req.full_url, newurl)
        source_hostname = (urlparse(req.full_url).hostname or "").lower()
        parsed_target = urlparse(target_url)
        target_hostname = (parsed_target.hostname or "").lower()
        if target_hostname == source_hostname and not _is_hust_hostname(target_hostname):
            try:
                if ipaddress.ip_address(target_hostname).is_loopback:
                    return super().redirect_request(
                        req, fp, code, msg, headers, target_url
                    )
            except ValueError:
                pass
        if not _is_hust_web_url(target_url):
            raise UnsafeRedirectError(target_url, "external_redirect")
        try:
            resolve_hostname(target_hostname)
        except OSError as exc:
            raise UnsafeRedirectError(target_url, "non_public_address") from exc
        return super().redirect_request(req, fp, code, msg, headers, target_url)


def _visible_text(body: bytes, hostname: str | None = None) -> str:
    text = body.decode("utf-8", errors="ignore")
    if hostname:
        text = re.sub(re.escape(hostname), "{host}", text, flags=re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(
        r"<(?P<hidden>head|script|style|template|noscript)\b[^>]*>.*?"
        r"</(?P=hidden)\s*>",
        " ",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"<title\b[^>]*>.*?</title\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split()).casefold()


def classify_http_status(status: int) -> tuple[bool, str]:
    if 200 <= status < 300:
        return True, "public_response"
    if 300 <= status < 400:
        return False, "redirect_error"
    if status in {401, 403}:
        return False, "access_denied"
    if status == 429:
        return False, "rate_limited"
    if status == 404:
        return False, "not_found"
    if status == 410:
        return False, "gone"
    if status >= 500:
        return False, "server_error"
    return False, "client_error"


def classify_response(
    status: int,
    body: bytes,
    content_type: str | None = None,
    final_url: str | None = None,
) -> tuple[bool, str]:
    active, reason = classify_http_status(status)
    normalized = body.decode("utf-8", errors="ignore").lower()
    normalized_type = (content_type or "").lower()
    login_markers = ("password", "sign in", "log in", "login", "đăng nhập")
    if status in {401, 403}:
        if "<html" in normalized and any(
            marker in normalized for marker in login_markers
        ):
            return False, "login_only"
        return False, "access_denied"
    if not active:
        return active, reason
    if 200 <= status < 300 and not body.strip():
        return False, "empty_response"
    machine_readable = (
        "json" in normalized_type
        or "xml" in normalized_type
        or normalized.lstrip().startswith(("{", "[", "<?xml"))
    )
    denial_markers = ("access denied", "forbidden", "unauthorized")
    if machine_readable and any(marker in normalized for marker in denial_markers):
        return False, "access_denied"
    if machine_readable:
        return False, "service_endpoint"
    html_denial_markers = (
        "<title>access denied</title>",
        "<title>forbidden</title>",
        "<h1>access denied</h1>",
        "<h1>forbidden</h1>",
    )
    if any(marker in normalized for marker in html_denial_markers):
        return False, "access_denied"
    placeholder_markers = (
        "welcome to nginx",
        "apache2 ubuntu default page",
        "website under construction",
        "website is under construction",
        "domain for sale",
        "this domain is parked",
        "http server test page powered by centos-webpanel.com",
        "<title>account suspended</title>",
        "openlitespeed is functioning normally",
        "trang thông tin này chưa kích hoạt",
        "<title>plesk ",
        "<title>vinahost plesk",
        "<title>web server's default page</title>",
        "<title>404</title>",
        "powered_by_cpanel.svg",
        "cpanel, inc.",
        "/cgi-sys/defaultwebpage.cgi",
        "website hiện không hoạt động",
        "webserver is functioning normally",
        "add anything here or just remove it",
        "welcome to wordpress. this is your first post.",
        "<title>apache tomcat",
    )
    if any(marker in normalized for marker in placeholder_markers):
        return False, "placeholder"
    service_markers = (
        "<title>cfssl</title>",
        "esxuiapp",
        "<title>bkoffice</title>",
        "email|pass|refresh_token|client_id",
        "<title>d-office-hust</title>",
        "<title>bksign | hệ thống ký số</title>",
        "<title>ediploma</title>",
        "<title>hustack</title>",
        "<title>tsa-exam",
    )
    if any(marker in normalized for marker in service_markers):
        return False, "service_endpoint"
    final_hostname = (urlparse(final_url).hostname or "").lower() if final_url else ""
    non_production_prefix = final_hostname.removesuffix(".hust.edu.vn")
    if re.search(
        r"(?:^|[-.])(?:dev|staging|demo|test)(?:[-.]|$)",
        non_production_prefix,
    ):
        return False, "non_production"
    login_titles = (
        "<title>login",
        "<title>log in",
        "<title>sign in",
        "<title>đăng nhập",
    )
    password_input = re.search(
        r"<input\b[^>]*\btype\s*=\s*['\"]?password\b",
        normalized,
    )
    login_path = urlparse(final_url).path.lower() if final_url else ""
    login_url = re.search(
        r"(?:^|/)(?:account/)?"
        r"(?:login|log-in|signin|sign-in|dang-nhap|weblogin|changepass)"
        r"(?:[._/-]|$)",
        login_path,
    )
    registration_url = re.search(r"(?:^|/)(?:register|signup|sign-up)(?:[._/-]|$)", login_path)
    visible_text = _visible_text(body)
    short_account_form = (
        "<form" in normalized
        and len(visible_text) < 500
        and any(
            marker in visible_text
            for marker in (
                "dang nhap",
                "mat khau",
                "mật khẩu",
                "password",
                "sign in",
                "log in",
                "đăng nhập",
            )
        )
    )
    if login_url or (registration_url and "<form" in normalized) or (
        password_input and any(marker in normalized for marker in login_titles)
    ) or short_account_form:
        return False, "login_only"
    html_response = "html" in normalized_type or bool(
        re.search(r"<(?:html|head|body|title|main|div|script)\b", normalized)
    )
    shell_text = visible_text.strip(" .").casefold()
    if html_response and (
        not visible_text
        or shell_text
        in {
            "loading",
            "please enable javascript",
            "javascript is required",
            "you need to enable javascript to run this app",
        }
    ):
        return False, "javascript_shell"
    return active, reason


def _is_hust_hostname(hostname: str | None) -> bool:
    return bool(
        hostname
        and (hostname == "hust.edu.vn" or hostname.endswith(".hust.edu.vn"))
    )


def _is_hust_web_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and _is_hust_hostname((parsed.hostname or "").lower())
        and (port is None or port == (80 if parsed.scheme == "http" else 443))
    )


def _extract_client_redirect(body: bytes, base_url: str) -> str | None:
    text = body.decode("utf-8", errors="ignore")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        if not re.search(r"http-equiv\s*=\s*['\"]?refresh", tag, re.I):
            continue
        content = re.search(r"content\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        content_value = content.group(2) if content else None
        if content_value is None:
            unquoted = re.search(r"content\s*=\s*([^\s>]+)", tag, re.I)
            content_value = unquoted.group(1) if unquoted else None
        if content_value is None:
            continue
        target = re.search(r"url\s*=\s*['\"]?([^'\";\s]+)", content_value, re.I)
        if target:
            return urljoin(base_url, html.unescape(target.group(1)))
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.I | re.S):
        statement = script.strip()
        javascript = re.fullmatch(
            r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]\s*;?",
            statement,
            re.I,
        )
        replace_call = re.fullmatch(
            r"(?:window\.)?location\.replace\(\s*['\"]([^'\"]+)['\"]\s*\)\s*;?",
            statement,
            re.I,
        )
        redirect = javascript or replace_call
        if redirect:
            return urljoin(base_url, redirect.group(1).strip())
    return None


def _same_host_public_urls(
    hostname: str,
    base_url: str,
    body: bytes,
) -> list[str]:
    text = html.unescape(body.decode("utf-8", errors="ignore"))
    anchors = re.findall(
        r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)",
        text,
        re.I,
    )
    locations = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
    urls: list[str] = []
    for raw_url in [*anchors, *locations]:
        candidate = urljoin(base_url, raw_url)
        parsed = urlparse(candidate)
        if parsed.hostname != hostname or parsed.scheme not in {"http", "https"}:
            continue
        lowered_path = parsed.path.lower()
        if any(
            marker in lowered_path
            for marker in (
                "login",
                "log-in",
                "signin",
                "sign-in",
                "register",
                "sign-up",
                "signup",
                "forgot",
                "reset-password",
                "password-reset",
                "/admin/",
                "dataprivacy",
                "registry-config",
                "cgi-sys",
            )
        ):
            continue
        normalized = parsed._replace(fragment="").geturl()
        if normalized.rstrip("/") == base_url.rstrip("/") or normalized in urls:
            continue
        urls.append(normalized)
    return urls[:5]


def _probe_public_subpage(
    hostname: str,
    root_url: str,
    root_attempt: ProbeAttempt,
    *,
    timeout: float,
    fetch,
    include_root_links: bool = True,
) -> ProbeAttempt | None:
    root_candidates = (
        _same_host_public_urls(hostname, root_url, root_attempt.body)
        if include_root_links
        else []
    )
    sitemap_url = urljoin(root_url, "/sitemap.xml")
    sitemap = fetch(sitemap_url, timeout)
    if sitemap.status is not None and 200 <= sitemap.status < 300:
        sitemap_candidates = _same_host_public_urls(
            hostname,
            sitemap.final_url or sitemap_url,
            sitemap.body,
        )
    else:
        sitemap_candidates = []
    for candidate in [*root_candidates[:5], *sitemap_candidates[:5]]:
        attempt = fetch(candidate, timeout)
        if attempt.status is None:
            continue
        active, _ = classify_response(
            attempt.status,
            attempt.body,
            attempt.content_type,
            attempt.final_url,
        )
        if active:
            return attempt
    return None


def extract_title(body: bytes) -> str | None:
    match = re.search(rb"<title\b[^>]*>(.*?)</title>", body, re.I | re.S)
    if not match:
        return None
    raw_title = match.group(1).decode("utf-8", errors="replace")
    title = html.unescape(re.sub(r"\s+", " ", raw_title)).strip()
    return title or None


def _content_digest(body: bytes, hostname: str | None = None) -> str | None:
    if not body:
        return None
    return hashlib.sha256(_visible_text(body, hostname).encode()).hexdigest()


def probe_host(
    hostname: str,
    *,
    timeout: float,
    attempts: int,
    resolve,
    fetch,
) -> ProbeResult:
    dns_error: OSError | None = None
    for _ in range(attempts):
        try:
            resolve(hostname)
            dns_error = None
            break
        except NonPublicAddressError as exc:
            return ProbeResult(
                hostname,
                False,
                "non_public_address",
                error=str(exc),
            )
        except OSError as exc:
            dns_error = exc
    if dns_error is not None:
        return ProbeResult(hostname, False, "dns_error", error=str(dns_error))

    last_attempt: ProbeAttempt | None = None
    last_scheme: str | None = None
    last_response: ProbeAttempt | None = None
    last_response_scheme: str | None = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{hostname}/"
        for attempt_number in range(attempts):
            attempt = fetch(url, timeout)
            last_attempt = attempt
            last_scheme = scheme
            if attempt.status is None:
                continue
            if attempt.error in {"external_redirect", "non_public_address"}:
                return ProbeResult(
                    hostname=hostname,
                    active=False,
                    reason=attempt.error,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    error=attempt.error,
                )
            client_redirect = _extract_client_redirect(
                attempt.body,
                attempt.final_url or url,
            )
            if client_redirect:
                target_hostname = urlparse(client_redirect).hostname
                if not _is_hust_web_url(client_redirect):
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="external_redirect",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        content_type=attempt.content_type,
                        title=extract_title(attempt.body),
                    )
                try:
                    resolve(target_hostname)
                except NonPublicAddressError as exc:
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="non_public_address",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        error=str(exc),
                    )
                except OSError as exc:
                    return ProbeResult(
                        hostname=hostname,
                        active=False,
                        reason="network_error",
                        scheme=scheme,
                        status=attempt.status,
                        final_url=client_redirect,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                redirected_attempt = fetch(client_redirect, timeout)
                last_attempt = redirected_attempt
                if redirected_attempt.status is None:
                    continue
                attempt = redirected_attempt
                last_response = attempt
                last_response_scheme = scheme
            last_response = attempt
            last_response_scheme = scheme
            active, reason = classify_response(
                attempt.status,
                attempt.body,
                attempt.content_type,
                attempt.final_url,
            )
            if active:
                return ProbeResult(
                    hostname=hostname,
                    active=True,
                    reason=reason,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    content_type=attempt.content_type,
                    title=extract_title(attempt.body),
                    content_digest=_content_digest(
                        attempt.body,
                        urlparse(attempt.final_url or url).hostname,
                    ),
                )
            deterministic_shell = reason in {"javascript_shell", "service_endpoint"}
            if (attempt_number == attempts - 1 or deterministic_shell) and reason in {
                "empty_response",
                "javascript_shell",
                "login_only",
                "not_found",
                "placeholder",
                "service_endpoint",
            }:
                public_page = _probe_public_subpage(
                    hostname,
                    url,
                    attempt,
                    timeout=timeout,
                    fetch=fetch,
                    include_root_links=reason
                    not in {"javascript_shell", "placeholder", "service_endpoint"},
                )
                if public_page is not None:
                    return ProbeResult(
                        hostname=hostname,
                        active=True,
                        reason="public_response",
                        scheme=scheme,
                        status=public_page.status,
                        final_url=public_page.final_url,
                        content_type=public_page.content_type,
                        title=extract_title(public_page.body),
                        content_digest=_content_digest(
                            public_page.body,
                            urlparse(public_page.final_url or url).hostname,
                        ),
                    )
            if deterministic_shell:
                return ProbeResult(
                    hostname=hostname,
                    active=False,
                    reason=reason,
                    scheme=scheme,
                    status=attempt.status,
                    final_url=attempt.final_url,
                    content_type=attempt.content_type,
                    title=extract_title(attempt.body),
                    content_digest=_content_digest(
                        attempt.body,
                        urlparse(attempt.final_url or url).hostname,
                    ),
                    error=attempt.error,
                )

    if last_attempt is None:
        return ProbeResult(hostname, False, "network_error", error="no attempts")
    if last_response is not None:
        _, reason = classify_response(
            last_response.status,
            last_response.body,
            last_response.content_type,
            last_response.final_url,
        )
        return ProbeResult(
            hostname=hostname,
            active=False,
            reason=reason,
            scheme=last_response_scheme,
            status=last_response.status,
            final_url=last_response.final_url,
            content_type=last_response.content_type,
            title=extract_title(last_response.body),
            content_digest=_content_digest(
                last_response.body,
                urlparse(last_response.final_url or "").hostname or hostname,
            ),
            error=last_response.error,
        )
    return ProbeResult(
        hostname,
        False,
        "network_error",
        scheme=last_scheme,
        error=last_attempt.error,
    )


def fetch_url(url: str, timeout: float) -> ProbeAttempt:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        HTTPCookieProcessor(CookieJar()),
        SafeRedirectHandler(),
    )
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140 Safari/537.36"
            ),
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(256 * 1024)
            return ProbeAttempt(
                status=response.status,
                final_url=response.geturl(),
                content_type=response.headers.get_content_type(),
                body=body,
            )
    except UnsafeRedirectError as exc:
        return ProbeAttempt(
            status=302,
            final_url=exc.target_url,
            error=exc.reason,
        )
    except HTTPError as exc:
        try:
            body = exc.read(256 * 1024)
        except OSError:
            body = b""
        return ProbeAttempt(
            status=exc.code,
            final_url=exc.geturl(),
            content_type=exc.headers.get_content_type() if exc.headers else None,
            body=body,
            error=str(exc),
        )
    except (OSError, TimeoutError, URLError, http.client.HTTPException) as exc:
        return ProbeAttempt(error=f"{type(exc).__name__}: {exc}")


def load_candidate_hostnames(path: Path) -> tuple[str, ...]:
    hostnames: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        hostname = raw_line.strip().lower().rstrip(".")
        if not hostname or hostname.startswith("#"):
            continue
        if hostname != "hust.edu.vn" and not hostname.endswith(".hust.edu.vn"):
            raise ValueError(
                f"{path}:{line_number}: hostname outside HUST scope: {hostname}"
            )
        hostnames.add(hostname)
    return tuple(sorted(hostnames))


def render_active_domains(hostnames: list[str]) -> str:
    return "".join(f"{hostname}\n" for hostname in sorted(set(hostnames)))


def render_report(results: list[ProbeResult]) -> str:
    return "".join(
        json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
        for result in sorted(results, key=lambda item: item.hostname)
    )


def run_audit(
    all_path: Path,
    active_path: Path,
    report_path: Path,
    *,
    workers: int,
    probe,
    browser_probe=None,
) -> list[ProbeResult]:
    hostnames = load_candidate_hostnames(all_path)
    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(probe, hostname): hostname for hostname in hostnames}
        for completed, future in enumerate(as_completed(futures), 1):
            hostname = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = ProbeResult(
                    hostname,
                    False,
                    "probe_error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            if completed % 25 == 0 or completed == len(hostnames):
                active_count = sum(item.active for item in results)
                print(
                    f"checked {completed}/{len(hostnames)}; active {active_count}",
                    flush=True,
                )

    if browser_probe is not None:
        browser_candidates = sorted(
            (result for result in results if result.reason == "javascript_shell"),
            key=lambda item: item.hostname,
        )
        if browser_candidates:
            results = _apply_browser_attempts(
                results,
                browser_probe(browser_candidates),
            )

    results = _deduplicate_content(_canonicalize_redirects(results))
    results.sort(key=lambda item: item.hostname)
    all_hostnames = sorted({*hostnames, *(result.hostname for result in results)})
    _atomic_text(all_path, "".join(f"{hostname}\n" for hostname in all_hostnames))
    _atomic_text(
        active_path,
        render_active_domains(
            [result.hostname for result in results if result.active],
        ),
    )
    _atomic_text(report_path, render_report(results))
    return results


def _canonicalize_redirects(results: list[ProbeResult]) -> list[ProbeResult]:
    by_hostname = {result.hostname: result for result in results}
    redirected: list[tuple[str, str, ProbeResult]] = []
    for result in results:
        if not result.active or not result.final_url:
            continue
        final_hostname = (urlparse(result.final_url).hostname or "").lower().rstrip(".")
        if not _is_hust_hostname(final_hostname):
            by_hostname[result.hostname] = replace(
                result,
                active=False,
                reason="external_redirect",
            )
        elif final_hostname != result.hostname:
            redirected.append((result.hostname, final_hostname, result))

    for source, target, evidence in redirected:
        target_result = by_hostname.get(target)
        if target_result is None or not target_result.active:
            by_hostname[target] = ProbeResult(
                hostname=target,
                active=True,
                reason="public_response",
                scheme=urlparse(evidence.final_url or "").scheme or evidence.scheme,
                status=evidence.status,
                final_url=evidence.final_url,
                content_type=evidence.content_type,
                title=evidence.title,
                content_digest=evidence.content_digest,
                browser_rendered=evidence.browser_rendered,
                checked_at=evidence.checked_at,
            )
        by_hostname[source] = replace(
            evidence,
            active=False,
            reason="redirect_duplicate",
        )
    return list(by_hostname.values())


def _deduplicate_content(results: list[ProbeResult]) -> list[ProbeResult]:
    by_digest: dict[str, list[ProbeResult]] = {}
    for result in results:
        if result.active and result.content_digest:
            by_digest.setdefault(result.content_digest, []).append(result)

    replacements: dict[str, ProbeResult] = {}
    for matches in by_digest.values():
        if len(matches) < 2:
            continue
        canonical = min(matches, key=lambda item: (len(item.hostname), item.hostname))
        for result in matches:
            if result.hostname != canonical.hostname:
                replacements[result.hostname] = replace(
                    result,
                    active=False,
                    reason="content_duplicate",
                )
    return [replacements.get(result.hostname, result) for result in results]


def _apply_browser_attempts(
    results: list[ProbeResult],
    attempts: dict[str, ProbeAttempt],
) -> list[ProbeResult]:
    replacements: dict[str, ProbeResult] = {}
    for result in results:
        attempt = attempts.get(result.hostname)
        if result.reason != "javascript_shell" or attempt is None:
            continue
        if attempt.status is None:
            replacements[result.hostname] = replace(result, error=attempt.error)
            continue
        active, reason = classify_response(
            attempt.status,
            attempt.body,
            attempt.content_type,
            attempt.final_url,
        )
        replacements[result.hostname] = replace(
            result,
            active=active,
            reason=reason,
            scheme=urlparse(attempt.final_url or "").scheme or result.scheme,
            status=attempt.status,
            final_url=attempt.final_url or result.final_url,
            content_type=attempt.content_type,
            title=extract_title(attempt.body),
            content_digest=_content_digest(
                attempt.body,
                urlparse(attempt.final_url or "").hostname or result.hostname,
            ),
            browser_rendered=True,
            error=attempt.error,
        )
    return [replacements.get(result.hostname, result) for result in results]


async def _bounded_close(resource, *, timeout: float = 3.0) -> None:
    try:
        await asyncio.wait_for(resource.close(), timeout=timeout)
    except TimeoutError:
        pass


async def _block_web_socket(web_socket) -> None:
    await web_socket.close(code=1008, reason="audit network policy")


def _browser_attempt_needs_retry(attempt: ProbeAttempt) -> bool:
    if attempt.status is None:
        return True
    _, reason = classify_response(
        attempt.status,
        attempt.body,
        attempt.content_type,
        attempt.final_url,
    )
    return reason == "javascript_shell"


def _browser_request_allowed(url: str, *, timeout: float) -> bool:
    policy_key = _browser_policy_key(url)
    if policy_key is None:
        return False
    _, hostname, _ = policy_key
    try:
        resolve_hostname(hostname, timeout=min(timeout, 5.0))
    except OSError:
        return False
    return True


def _browser_policy_key(url: str) -> tuple[str, str, int] | None:
    if not _is_hust_web_url(url):
        return None
    parsed = urlparse(url)
    return (
        parsed.scheme,
        (parsed.hostname or "").lower(),
        parsed.port or (80 if parsed.scheme == "http" else 443),
    )


def browser_probe_results(
    results: list[ProbeResult],
    *,
    timeout: float,
    workers: int,
) -> dict[str, ProbeAttempt]:
    async def render() -> dict[str, ProbeAttempt]:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        attempts: dict[str, ProbeAttempt] = {}
        semaphore = asyncio.Semaphore(workers)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                ignore_https_errors=True,
                service_workers="block",
            )
            browser_host_policy: dict[tuple[str, str, int], bool] = {}

            async def block_heavy_resources(route) -> None:
                if route.request.resource_type in {"font", "image", "media"}:
                    await route.abort()
                    return
                policy_key = _browser_policy_key(route.request.url)
                if policy_key is None:
                    await route.abort()
                    return
                allowed = browser_host_policy.get(policy_key)
                if allowed is None:
                    allowed = await asyncio.to_thread(
                        _browser_request_allowed,
                        route.request.url,
                        timeout=timeout,
                    )
                    if allowed:
                        browser_host_policy[policy_key] = True
                if allowed:
                    await route.continue_()
                    return
                await route.abort()

            await context.route("**/*", block_heavy_resources)

            await context.route_web_socket("**/*", _block_web_socket)

            async def render_one(result: ProbeResult) -> None:
                async with semaphore:
                    for _ in range(2):
                        page = await context.new_page()
                        response = None
                        navigation_error: str | None = None
                        try:
                            try:
                                response = await page.goto(
                                    result.final_url or f"https://{result.hostname}/",
                                    wait_until="domcontentloaded",
                                    timeout=timeout * 1000,
                                )
                            except PlaywrightTimeoutError as exc:
                                navigation_error = f"PlaywrightTimeoutError: {exc}"
                            await page.wait_for_timeout(2000)
                            body = (await page.content()).encode()
                            attempts[result.hostname] = ProbeAttempt(
                                status=response.status if response else result.status,
                                final_url=page.url,
                                content_type="text/html",
                                body=body,
                                error=navigation_error,
                            )
                        except Exception as exc:
                            attempts[result.hostname] = ProbeAttempt(
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        finally:
                            await _bounded_close(page)
                        if not _browser_attempt_needs_retry(attempts[result.hostname]):
                            break

            await asyncio.gather(*(render_one(result) for result in results))
            await _bounded_close(context)
            await _bounded_close(browser)
        return attempts

    return asyncio.run(render())


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hust-domain-audit")
    parser.add_argument(
        "--all-domains", type=Path, default=Path("docs/domain_all.txt")
    )
    parser.add_argument(
        "--active-domains", type=Path, default=Path("docs/domain_active.txt")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("docs/domain_status.jsonl")
    )
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--browser-workers", type=int, default=6)
    parser.add_argument("--browser-timeout", type=float, default=15.0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args(argv)
    if (
        args.workers <= 0
        or args.browser_workers <= 0
        or args.timeout <= 0
        or args.browser_timeout <= 0
        or args.retries < 0
    ):
        parser.error("workers and timeouts must be positive; retries cannot be negative")

    results = run_audit(
        args.all_domains,
        args.active_domains,
        args.report,
        workers=args.workers,
        probe=partial(
            probe_host,
            timeout=args.timeout,
            attempts=args.retries + 1,
            resolve=resolve_hostname,
            fetch=fetch_url,
        ),
        browser_probe=(
            None
            if args.no_browser
            else partial(
                browser_probe_results,
                timeout=args.browser_timeout,
                workers=args.browser_workers,
            )
        ),
    )
    print(
        json.dumps(
            {
                "active": sum(result.active for result in results),
                "checked": len(results),
                "report": str(args.report),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
