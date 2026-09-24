import json
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import hust_crawler.domain_audit as domain_audit
from hust_crawler.domain_audit import (
    ProbeAttempt,
    ProbeResult,
    classify_response,
    classify_http_status,
    fetch_url,
    load_candidate_hostnames,
    main,
    probe_host,
    render_active_domains,
    render_report,
    run_audit,
)


@pytest.mark.parametrize(
    ("status", "active", "reason"),
    [
        (200, True, "public_response"),
        (204, True, "public_response"),
        (302, False, "redirect_error"),
        (401, False, "access_denied"),
        (403, False, "access_denied"),
        (429, False, "rate_limited"),
        (404, False, "not_found"),
        (410, False, "gone"),
        (500, False, "server_error"),
        (503, False, "server_error"),
    ],
)
def test_classify_http_status(status: int, active: bool, reason: str) -> None:
    assert classify_http_status(status) == (active, reason)


@pytest.mark.parametrize(
    "body",
    [
        b"<title>Welcome to nginx!</title>",
        b"<h1>Apache2 Ubuntu Default Page</h1>",
        b"<title>Website under construction</title>",
        b"<title>HTTP Server Test Page powered by CentOS-WebPanel.com</title>",
        b"<title>Account Suspended</title>",
        b"OpenLiteSpeed is functioning normally",
        "Trang thông tin này chưa kích hoạt.".encode(),
        b"<title>Plesk Obsidian 18.0.60</title>",
        b"<title>Web Server's Default Page</title>",
        b"<title>404</title>",
        (
            b'<title>403 Forbidden</title><p>Access is forbidden to the requested page:</p>'
            b'<a href="http://cpanel.com/">cPanel, Inc.</a>'
        ),
        (
            b'<meta http-equiv="refresh" content="0;URL=/cgi-sys/defaultwebpage.cgi">'
            b"<body></body>"
        ),
        "<title>Website hiện không hoạt động</title>".encode(),
        b"webserver is functioning normally",
        b"ADD ANYTHING HERE OR JUST REMOVE IT... Welcome to WordPress. This is your first post.",
        b"<title>Apache Tomcat/10.1.55</title>",
        b"<title>Apache Tomcat 10 (10.1.55) - Documentation Index</title><h1>Documentation</h1>",
    ],
)
def test_placeholder_page_is_not_active(body: bytes) -> None:
    assert classify_response(200, body) == (False, "placeholder")


def test_empty_success_response_is_not_an_active_website() -> None:
    assert classify_response(200, b"") == (False, "empty_response")
    assert classify_response(204, b"") == (False, "empty_response")


def test_machine_readable_success_access_denial_is_not_active() -> None:
    body = b'{"error":"access denied","status":"unauthorized"}'

    assert classify_response(200, body, "application/json") == (
        False,
        "access_denied",
    )


@pytest.mark.parametrize("content_type", ["application/json", "application/xml"])
def test_machine_readable_service_root_is_not_public_content(
    content_type: str,
) -> None:
    body = b'{"version":"1"}' if "json" in content_type else b"<status>ok</status>"

    assert classify_response(200, body, content_type) == (
        False,
        "service_endpoint",
    )


def test_machine_readable_access_denial_is_not_an_active_website() -> None:
    body = (
        b'{"status":"Failure","message":"forbidden: User '
        b'\\"system:anonymous\\" cannot get path \\"/\\""}'
    )

    assert classify_response(403, body) == (False, "access_denied")


def test_login_only_page_is_not_an_active_website() -> None:
    body = b'<html><title>Sign in</title><form><input type="password"></form></html>'

    assert classify_response(401, body) == (False, "login_only")


def test_public_content_with_a_login_control_remains_active() -> None:
    body = (
        b"<html><title>Learning portal</title><main><h1>Available courses</h1>"
        b"<p>Public course catalogue and student guidance.</p></main>"
        b'<form><input type="password"></form></html>'
    )

    assert classify_response(200, body) == (True, "public_response")


def test_short_visible_password_form_is_login_only() -> None:
    body = (
        b"<title>QLCB</title><form>Ten dang nhap: <input name='u'>"
        b"Mat khau: <input name='p'></form>"
    )

    assert classify_response(200, body, "text/html") == (False, "login_only")


@pytest.mark.parametrize(
    "body",
    [
        b"<title>CFSSL</title><div id='cfssl'></div>",
        b"<html ng-app='esxUiApp'><body></body></html>",
        b"<title>BKOffice</title><div id='app'></div>",
        b"Invalid input format: email|pass|refresh_token|client_id",
        b"<title>D-Office-HUST</title><app-root>Please enable JavaScript</app-root>",
        "<title>BKSign | Hệ thống ký số</title><div id='root'></div>".encode(),
        b"<title>eDiploma</title><div id='root'></div>",
        b"<title>HUSTack</title><div id='root'></div>",
        b"<title>TSA-Exam | Phan mem thi</title><div id='root'></div>",
    ],
)
def test_service_shell_without_public_content_is_not_active(body: bytes) -> None:
    assert classify_response(200, body, "text/html") == (
        False,
        "service_endpoint",
    )


def test_probe_result_records_content_digest_for_duplicate_detection() -> None:
    result = ProbeResult("example.hust.edu.vn", True, "public_response")

    assert hasattr(result, "content_digest")


def test_probe_digest_ignores_the_serving_hostname() -> None:
    def probe(hostname: str) -> ProbeResult:
        return probe_host(
            hostname,
            timeout=1,
            attempts=1,
            resolve=lambda value: None,
            fetch=lambda url, timeout: ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=(
                    b"<title>Same site</title><a href='https://"
                    + hostname.encode()
                    + b"/news'>News</a>"
                ),
            ),
        )

    first = probe("short.hust.edu.vn")
    second = probe("www.short.hust.edu.vn")

    assert first.content_digest == second.content_digest


def test_probe_digest_ignores_non_visible_dynamic_markup() -> None:
    def probe(hostname: str, nonce: str) -> ProbeResult:
        return probe_host(
            hostname,
            timeout=1,
            attempts=1,
            resolve=lambda value: None,
            fetch=lambda url, timeout: ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=(
                    b"<title>Same site</title><main><h1>Public news</h1>"
                    b"<p>The same substantive public content is available here.</p></main>"
                    + f"<script>window.nonce='{nonce}'</script>".encode()
                ),
            ),
        )

    first = probe("short.hust.edu.vn", "first-token")
    second = probe("www.short.hust.edu.vn", "second-token")

    assert first.content_digest == second.content_digest


def test_script_only_aliases_are_inactive_and_ignore_dynamic_script_content() -> None:
    def probe(hostname: str, nonce: str) -> ProbeResult:
        return probe_host(
            hostname,
            timeout=1,
            attempts=1,
            resolve=lambda value: None,
            fetch=lambda url, timeout: ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=(
                    b"<title>Internal portal</title><div id='root'></div>"
                    + f"<script>boot('{nonce}')</script>".encode()
                ),
            ),
        )

    first = probe("short.hust.edu.vn", "first-token")
    second = probe("www.short.hust.edu.vn", "second-token")

    assert (first.active, first.reason) == (False, "javascript_shell")
    assert (second.active, second.reason) == (False, "javascript_shell")
    assert first.content_digest == second.content_digest


def test_probe_rejects_non_public_dns_before_fetching() -> None:
    calls: list[str] = []

    result = probe_host(
        "internal.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: (_ for _ in ()).throw(
            domain_audit.NonPublicAddressError("192.168.50.254")
        ),
        fetch=lambda url, timeout: calls.append(url),
    )

    assert result.active is False
    assert result.reason == "non_public_address"
    assert calls == []


def test_resolve_hostname_rejects_private_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        domain_audit.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (domain_audit.socket.AF_INET, 0, 0, "", ("192.168.50.254", 443)),
        ],
    )

    with pytest.raises(domain_audit.NonPublicAddressError, match="192.168.50.254"):
        domain_audit.resolve_hostname("internal.hust.edu.vn")


def test_resolve_hostname_has_a_hard_deadline(monkeypatch) -> None:
    def delayed_resolution(*args, **kwargs):
        time.sleep(0.05)
        return [
            (domain_audit.socket.AF_INET, 0, 0, "", ("203.0.113.10", 443)),
        ]

    monkeypatch.setattr(domain_audit.socket, "getaddrinfo", delayed_resolution)

    with pytest.raises(TimeoutError, match="DNS resolution timed out"):
        domain_audit.resolve_hostname("slow.hust.edu.vn", timeout=0.01)


def test_html_access_denial_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<html><title>Access denied</title><h1>Forbidden</h1></html>",
        "text/html",
    ) == (False, "access_denied")


def test_probe_finds_public_content_from_same_host_sitemap() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url == "https://example.hust.edu.vn/":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b'<title>Login</title><form><input type="password"></form>',
            )
        if url == "https://example.hust.edu.vn/sitemap.xml":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="application/xml",
                body=(
                    b"<urlset><url><loc>https://example.hust.edu.vn/news</loc>"
                    b"</url></urlset>"
                ),
            )
        if url == "https://example.hust.edu.vn/news":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b"<title>News</title><h1>Public research news</h1>",
            )
        return ProbeAttempt(error="unexpected URL")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is True
    assert result.final_url == "https://example.hust.edu.vn/news"
    assert calls == [
        "https://example.hust.edu.vn/",
        "https://example.hust.edu.vn/sitemap.xml",
        "https://example.hust.edu.vn/news",
    ]


def test_probe_finds_public_sitemap_content_behind_script_shell_root() -> None:
    def fetch(url: str, timeout: float) -> ProbeAttempt:
        if url == "https://example.hust.edu.vn/":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b"<title>Portal</title><div id='root'></div><script>boot()</script>",
            )
        if url == "https://example.hust.edu.vn/sitemap.xml":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="application/xml",
                body=(
                    b"<urlset><url><loc>https://example.hust.edu.vn/news</loc>"
                    b"</url></urlset>"
                ),
            )
        if url == "https://example.hust.edu.vn/news":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b"<title>News</title><h1>Public research news</h1>",
            )
        return ProbeAttempt(status=404, final_url=url, body=b"not found")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is True
    assert result.final_url == "https://example.hust.edu.vn/news"


def test_probe_does_not_follow_placeholder_vendor_links() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url == "https://example.hust.edu.vn/":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=(
                    b"<title>Apache Tomcat/10.1.55</title>"
                    b"<a href='/docs/'>Documentation</a>"
                ),
            )
        if url.endswith("/sitemap.xml"):
            return ProbeAttempt(status=404, final_url=url, body=b"not found")
        return ProbeAttempt(
            status=200,
            final_url=url,
            content_type="text/html",
            body=b"<title>Apache Tomcat Documentation</title><h1>Documentation</h1>",
        )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert (result.active, result.reason) == (False, "placeholder")
    assert "https://example.hust.edu.vn/docs/" not in calls


def test_probe_rejects_login_only_redirect_with_non_login_title() -> None:
    def fetch(url: str, timeout: float) -> ProbeAttempt:
        if url.endswith("/sitemap.xml"):
            return ProbeAttempt(status=404, final_url=url, body=b"not found")
        return ProbeAttempt(
            status=200,
            final_url="https://example.hust.edu.vn/Account/Login.aspx",
            content_type="text/html",
            body=(
                b"<title>Student portal</title><form>"
                b'<input type="password"></form>'
            ),
        )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "login_only"


def test_probe_does_not_treat_login_page_assets_as_public_content() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url.endswith("/sitemap.xml"):
            return ProbeAttempt(status=404, final_url=url, body=b"not found")
        if url.endswith("/styles/site.css"):
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/css",
                body=b"body { color: black; }",
            )
        return ProbeAttempt(
            status=200,
            final_url="https://example.hust.edu.vn/login",
            content_type="text/html",
            body=(
                b"<title>Login</title>"
                b'<link rel="stylesheet" href="/styles/site.css">'
                b'<a href="/forgot">Forgot password</a>'
                b'<form><input type="password"></form>'
            ),
        )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "login_only"
    assert "https://example.hust.edu.vn/styles/site.css" not in calls
    assert "https://example.hust.edu.vn/forgot" not in calls


def test_probe_rejects_login_path_without_standard_password_input() -> None:
    def fetch(url: str, timeout: float) -> ProbeAttempt:
        if url.endswith("/sitemap.xml"):
            return ProbeAttempt(status=404, final_url=url, body=b"not found")
        return ProbeAttempt(
            status=200,
            final_url="https://example.hust.edu.vn/login_page.php",
            content_type="text/html",
            body=b"<title>Portal</title><form><div id='password-control'></div></form>",
        )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "login_only"


def test_probe_follows_javascript_redirect_for_canonical_host_detection() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url == "https://alias.hust.edu.vn/":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=(
                    b"<html><script>window.location = "
                    b"'https://canonical.hust.edu.vn';</script></html>"
                ),
            )
        if url == "https://canonical.hust.edu.vn":
            return ProbeAttempt(
                status=200,
                final_url="https://canonical.hust.edu.vn/",
                content_type="text/html",
                body=b"<title>Canonical public site</title><h1>News</h1>",
            )
        return ProbeAttempt(error="unexpected URL")

    result = probe_host(
        "alias.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is True
    assert result.final_url == "https://canonical.hust.edu.vn/"
    assert calls == [
        "https://alias.hust.edu.vn/",
        "https://canonical.hust.edu.vn",
    ]


def test_probe_rejects_client_redirect_to_non_public_hust_host() -> None:
    fetched: list[str] = []

    def resolve(hostname: str) -> None:
        if hostname == "private.hust.edu.vn":
            raise domain_audit.NonPublicAddressError("private target")

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        fetched.append(url)
        return ProbeAttempt(
            status=200,
            final_url=url,
            content_type="text/html",
            body=b"<script>location='https://private.hust.edu.vn/'</script>",
        )

    result = probe_host(
        "alias.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=resolve,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "non_public_address"
    assert fetched == ["https://alias.hust.edu.vn/"]


def test_probe_follows_quoted_meta_refresh_before_classifying() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url == "https://example.hust.edu.vn/":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b'<meta http-equiv="refresh" content="0;URL=\'/ui\'">',
            )
        if url == "https://example.hust.edu.vn/ui":
            return ProbeAttempt(
                status=200,
                final_url="https://example.hust.edu.vn/ui/",
                content_type="text/html",
                body=b"<html ng-app='esxUiApp'><body></body></html>",
            )
        return ProbeAttempt(error="unexpected URL")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "service_endpoint"
    assert calls == [
        "https://example.hust.edu.vn/",
        "https://example.hust.edu.vn/ui",
        "https://example.hust.edu.vn/sitemap.xml",
    ]


def test_event_handler_location_assignment_is_not_a_page_redirect() -> None:
    body = (
        b"<title>Public news</title><h1>News</h1>"
        b"<button onclick=\"window.location='/login'\">Login</button>"
    )
    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=lambda url, timeout: ProbeAttempt(
            status=200,
            final_url=url,
            content_type="text/html",
            body=body,
        ),
    )

    assert result.active is True
    assert result.final_url == "https://example.hust.edu.vn/"


@pytest.mark.parametrize(
    "body",
    [
        (
            b"<title>Public news</title><h1>News</h1>"
            b"&lt;meta http-equiv='refresh' content='0;url=https://outside.example/'&gt;"
        ),
        (
            b"<title>Public news</title><h1>News</h1>"
            b"<!-- <meta http-equiv='refresh' content='0;url=https://outside.example/'> -->"
        ),
    ],
)
def test_non_dom_meta_refresh_text_is_not_followed(body: bytes) -> None:
    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=lambda url, timeout: ProbeAttempt(
            status=200,
            final_url=url,
            content_type="text/html",
            body=body,
        ),
    )

    assert result.active is True
    assert result.final_url == "https://example.hust.edu.vn/"


def test_probe_reserves_sitemap_budget_after_five_root_links() -> None:
    def fetch(url: str, timeout: float) -> ProbeAttempt:
        if url == "https://example.hust.edu.vn/":
            links = b"".join(
                f'<a href="/dead-{index}">Dead</a>'.encode() for index in range(5)
            )
            return ProbeAttempt(
                status=200,
                final_url="https://example.hust.edu.vn/login",
                content_type="text/html",
                body=b"<title>Login</title><form></form>" + links,
            )
        if url == "https://example.hust.edu.vn/sitemap.xml":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="application/xml",
                body=b"<urlset><url><loc>https://example.hust.edu.vn/news</loc></url></urlset>",
            )
        if url == "https://example.hust.edu.vn/news":
            return ProbeAttempt(
                status=200,
                final_url=url,
                content_type="text/html",
                body=b"<title>Public news</title><h1>Research</h1>",
            )
        return ProbeAttempt(status=404, final_url=url, body=b"not found")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=1,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is True
    assert result.final_url == "https://example.hust.edu.vn/news"


def test_registration_only_page_is_not_public_content() -> None:
    body = b"<title>Register Account</title><form>Create account</form>"

    assert classify_response(
        200,
        body,
        "text/html",
        "https://login.example.hust.edu.vn/register",
    ) == (False, "login_only")


def test_vietnamese_login_path_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<title>Portal</title><form><input type='password'></form>",
        "text/html",
        "https://example.hust.edu.vn/dang-nhap",
    ) == (False, "login_only")


@pytest.mark.parametrize("path", ["/weblogin.htm", "/changepass.php"])
def test_account_access_paths_are_not_public_content(path: str) -> None:
    assert classify_response(
        200,
        b"<title>Staff portal</title><form><input name='account'></form>",
        "text/html",
        f"https://example.hust.edu.vn{path}",
    ) == (False, "login_only")


def test_non_production_hostname_is_not_public_content() -> None:
    assert classify_response(
        200,
        b"<title>University</title><main><h1>Public news</h1></main>",
        "text/html",
        "https://hust-dev.hust.edu.vn/",
    ) == (False, "non_production")


def test_probe_retries_https_then_falls_back_to_http() -> None:
    calls: list[str] = []

    def resolve(hostname: str) -> None:
        assert hostname == "example.hust.edu.vn"

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        calls.append(url)
        if url.startswith("https://"):
            return ProbeAttempt(error="connection refused")
        return ProbeAttempt(
            status=200,
            final_url=url,
            body=b"<title>Working service</title><h1>Public information</h1>",
        )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=resolve,
        fetch=fetch,
    )

    assert result.active is True
    assert result.scheme == "http"
    assert result.title == "Working service"
    assert calls == [
        "https://example.hust.edu.vn/",
        "https://example.hust.edu.vn/",
        "http://example.hust.edu.vn/",
    ]


def test_probe_retries_temporary_server_error() -> None:
    responses = iter(
        [
            ProbeAttempt(status=503, final_url="https://example.hust.edu.vn/"),
            ProbeAttempt(
                status=200,
                final_url="https://example.hust.edu.vn/",
                body=b"working",
            ),
        ]
    )

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=lambda hostname: None,
        fetch=lambda url, timeout: next(responses),
    )

    assert result.active is True
    assert result.status == 200


def test_probe_retries_rate_limit_but_rejects_persistent_rate_limit() -> None:
    transient = iter(
        [
            ProbeAttempt(status=429, final_url="https://example.hust.edu.vn/"),
            ProbeAttempt(
                status=200,
                final_url="https://example.hust.edu.vn/",
                body=b"working",
            ),
        ]
    )
    recovered = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=lambda hostname: None,
        fetch=lambda url, timeout: next(transient),
    )
    limited = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=lambda hostname: None,
        fetch=lambda url, timeout: ProbeAttempt(
            status=429,
            final_url=url,
            body=b"rate limited",
        ),
    )

    assert recovered.active is True
    assert limited.active is False
    assert limited.reason == "rate_limited"


def test_probe_reports_dns_failure_without_http_attempt() -> None:
    def fail_dns(hostname: str) -> None:
        raise OSError("name not known")

    result = probe_host(
        "missing.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=fail_dns,
        fetch=lambda url, timeout: pytest.fail("HTTP should not be attempted"),
    )

    assert result.active is False
    assert result.reason == "dns_error"


def test_probe_retries_transient_dns_failure() -> None:
    dns_attempts = 0

    def resolve(hostname: str) -> None:
        nonlocal dns_attempts
        dns_attempts += 1
        if dns_attempts == 1:
            raise OSError("temporary failure")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=resolve,
        fetch=lambda url, timeout: ProbeAttempt(
            status=200,
            final_url=url,
            body=b"working",
        ),
    )

    assert dns_attempts == 2
    assert result.active is True


def test_probe_keeps_http_failure_when_fallback_has_transport_errors() -> None:
    def fetch(url: str, timeout: float) -> ProbeAttempt:
        if url.startswith("https://"):
            return ProbeAttempt(
                status=404,
                final_url=url,
                body=b"<title>Not found</title>",
                error="HTTP Error 404: Not Found",
            )
        return ProbeAttempt(error="connection refused")

    result = probe_host(
        "example.hust.edu.vn",
        timeout=1,
        attempts=2,
        resolve=lambda hostname: None,
        fetch=fetch,
    )

    assert result.active is False
    assert result.reason == "not_found"
    assert result.scheme == "https"
    assert result.status == 404


def test_candidate_hostnames_are_normalized_unique_and_hust_scoped(tmp_path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text(
        "B.HUST.EDU.VN\na.hust.edu.vn\nb.hust.edu.vn\noutside.test\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside HUST scope"):
        load_candidate_hostnames(source)

    source.write_text(
        "B.HUST.EDU.VN\na.hust.edu.vn\nb.hust.edu.vn\n",
        encoding="utf-8",
    )
    assert load_candidate_hostnames(source) == (
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    )


def test_active_domain_output_is_plain_sorted_hostname_list() -> None:
    output = render_active_domains(
        ["b.hust.edu.vn", "a.hust.edu.vn", "b.hust.edu.vn"],
    )

    assert output == "a.hust.edu.vn\nb.hust.edu.vn\n"


def test_report_is_stable_jsonl_sorted_by_hostname() -> None:
    results = [
        ProbeResult("b.hust.edu.vn", False, "dns_error", error="not found"),
        ProbeResult(
            "a.hust.edu.vn",
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://a.hust.edu.vn/",
            title="A",
        ),
    ]

    lines = render_report(results).splitlines()

    assert [json.loads(line)["hostname"] for line in lines] == [
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    ]
    assert json.loads(lines[0])["active"] is True
    assert json.loads(lines[1])["reason"] == "dns_error"


def test_fetch_url_reads_bounded_http_response() -> None:
    seen_user_agents: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen_user_agents.append(self.headers["User-Agent"])
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Local service</title><p>ok</p>")

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        attempt = fetch_url(
            f"http://127.0.0.1:{server.server_port}/",
            timeout=1,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert attempt.status == 200
    assert attempt.body == b"<title>Local service</title><p>ok</p>"
    assert seen_user_agents == [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140 Safari/537.36"
    ]


def test_fetch_url_follows_cookie_dependent_redirect() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if "audit_session=ready" in self.headers.get("Cookie", ""):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<title>Login</title>")
                return
            self.send_response(302)
            self.send_header("Set-Cookie", "audit_session=ready; Path=/")
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        attempt = fetch_url(f"http://127.0.0.1:{server.server_port}/", timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert attempt.status == 200
    assert attempt.body == b"<title>Login</title>"


def test_fetch_url_does_not_follow_redirect_outside_hust_scope() -> None:
    requests = 0

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal requests
            requests += 1
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        attempt = fetch_url(f"http://127.0.0.1:{server.server_port}/", timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert requests == 1
    assert attempt.status == 302
    assert attempt.final_url == "http://169.254.169.254/latest/meta-data/"
    assert attempt.error == "external_redirect"


def test_fetch_url_does_not_follow_hust_redirect_to_non_public_address(
    monkeypatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "https://private.hust.edu.vn/")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    def resolve(hostname: str, timeout: float = 5.0) -> None:
        assert hostname == "private.hust.edu.vn"
        raise domain_audit.NonPublicAddressError("private target")

    monkeypatch.setattr(domain_audit, "resolve_hostname", resolve)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        attempt = fetch_url(f"http://127.0.0.1:{server.server_port}/", timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert attempt.status == 302
    assert attempt.final_url == "https://private.hust.edu.vn/"
    assert attempt.error == "non_public_address"


def test_fetch_url_does_not_follow_redirect_to_non_web_port(monkeypatch) -> None:
    monkeypatch.setattr(
        domain_audit,
        "resolve_hostname",
        lambda hostname, timeout=5.0: pytest.fail("must reject before DNS"),
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "https://public.hust.edu.vn:2375/")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        attempt = fetch_url(f"http://127.0.0.1:{server.server_port}/", timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert attempt.status == 302
    assert attempt.final_url == "https://public.hust.edu.vn:2375/"
    assert attempt.error == "external_redirect"


def test_redirect_loop_is_not_an_active_website() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        local_url = f"http://127.0.0.1:{server.server_port}/"
        result = probe_host(
            "loop.hust.edu.vn",
            timeout=1,
            attempts=1,
            resolve=lambda hostname: None,
            fetch=lambda url, timeout: fetch_url(local_url, timeout),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.active is False
    assert result.reason == "redirect_error"


def test_run_audit_updates_both_lists_and_writes_report(tmp_path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("b.hust.edu.vn\na.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    active_path.write_text(
        "Tên nhóm: BK\nList active domain:\nold.hust.edu.vn\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=2,
        probe=lambda hostname: ProbeResult(
            hostname,
            hostname.startswith("a."),
            "public_response" if hostname.startswith("a.") else "dns_error",
        ),
    )

    assert [result.hostname for result in results] == [
        "a.hust.edu.vn",
        "b.hust.edu.vn",
    ]
    assert all_path.read_text() == "a.hust.edu.vn\nb.hust.edu.vn\n"
    assert active_path.read_text() == "a.hust.edu.vn\n"
    assert len(report_path.read_text().splitlines()) == 2


def test_run_audit_promotes_only_public_browser_rendered_shells(tmp_path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text(
        "login.hust.edu.vn\nportal.hust.edu.vn\n",
        encoding="utf-8",
    )
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"
    browser_calls: list[list[str]] = []

    def browser_probe(results: list[ProbeResult]) -> dict[str, ProbeAttempt]:
        browser_calls.append([result.hostname for result in results])
        return {
            "login.hust.edu.vn": ProbeAttempt(
                status=200,
                final_url="https://login.hust.edu.vn/login",
                content_type="text/html",
                body=b"<title>Portal</title><form><input type='password'></form>",
            ),
            "portal.hust.edu.vn": ProbeAttempt(
                status=200,
                final_url="https://portal.hust.edu.vn/",
                content_type="text/html",
                body=b"<title>Portal</title><main><h1>Public news</h1></main>",
            ),
        }

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            False,
            "javascript_shell",
            scheme="https",
            status=200,
            final_url=f"https://{hostname}/",
            content_type="text/html",
        ),
        browser_probe=browser_probe,
    )

    assert browser_calls == [["login.hust.edu.vn", "portal.hust.edu.vn"]]
    assert active_path.read_text() == "portal.hust.edu.vn\n"
    assert [(item.hostname, item.active, item.reason) for item in results] == [
        ("login.hust.edu.vn", False, "login_only"),
        ("portal.hust.edu.vn", True, "public_response"),
    ]
    assert results[1].browser_rendered is True


def test_browser_resource_close_has_a_hard_timeout() -> None:
    class HangingResource:
        async def close(self) -> None:
            await asyncio.Event().wait()

    asyncio.run(domain_audit._bounded_close(HangingResource(), timeout=0.01))


def test_browser_web_sockets_are_closed_by_network_policy() -> None:
    class FakeWebSocket:
        closed_with: tuple[int, str] | None = None

        async def close(self, *, code: int, reason: str) -> None:
            self.closed_with = (code, reason)

    web_socket = FakeWebSocket()
    asyncio.run(domain_audit._block_web_socket(web_socket))

    assert web_socket.closed_with == (1008, "audit network policy")


def test_browser_retries_only_an_unrendered_javascript_shell() -> None:
    shell = ProbeAttempt(
        status=200,
        final_url="https://example.hust.edu.vn/",
        content_type="text/html",
        body=b"<title>Portal</title><div id='root'></div>",
    )
    public = ProbeAttempt(
        status=200,
        final_url="https://example.hust.edu.vn/",
        content_type="text/html",
        body=b"<title>Portal</title><main><h1>Public news</h1></main>",
    )

    assert domain_audit._browser_attempt_needs_retry(shell) is True
    assert domain_audit._browser_attempt_needs_retry(public) is False


def test_browser_request_policy_blocks_external_and_non_public_targets(
    monkeypatch,
) -> None:
    resolved: list[str] = []

    def resolve(hostname: str, timeout: float = 5.0) -> None:
        resolved.append(hostname)
        if hostname == "private.hust.edu.vn":
            raise domain_audit.NonPublicAddressError("private target")

    monkeypatch.setattr(domain_audit, "resolve_hostname", resolve)

    assert domain_audit._browser_request_allowed(
        "https://public.hust.edu.vn/app.js", timeout=1
    )
    assert not domain_audit._browser_request_allowed(
        "https://private.hust.edu.vn/api", timeout=1
    )
    assert not domain_audit._browser_request_allowed(
        "https://example.com/tracker.js", timeout=1
    )
    assert not domain_audit._browser_request_allowed(
        "https://public.hust.edu.vn:2375/admin", timeout=1
    )
    assert resolved == ["public.hust.edu.vn", "private.hust.edu.vn"]


def test_browser_policy_cache_key_includes_scheme_and_effective_port() -> None:
    assert domain_audit._browser_policy_key("https://public.hust.edu.vn/app.js") == (
        "https",
        "public.hust.edu.vn",
        443,
    )
    assert domain_audit._browser_policy_key("http://public.hust.edu.vn/") == (
        "http",
        "public.hust.edu.vn",
        80,
    )
    assert (
        domain_audit._browser_policy_key("https://public.hust.edu.vn:2375/admin")
        is None
    )


def test_run_audit_keeps_only_canonical_hust_redirect_target(tmp_path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("alias.hust.edu.vn\ncanonical.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://canonical.hust.edu.vn/",
            title="Canonical site",
        ),
    )

    assert active_path.read_text() == "canonical.hust.edu.vn\n"
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("alias.hust.edu.vn", False, "redirect_duplicate"),
        ("canonical.hust.edu.vn", True, "public_response"),
    ]


def test_run_audit_excludes_redirect_outside_hust_scope(tmp_path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text("journal.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url="https://journal.example.org/",
            title="External journal",
        ),
    )

    assert active_path.read_text() == ""
    assert results[0].active is False
    assert results[0].reason == "external_redirect"


def test_run_audit_keeps_shortest_hostname_for_identical_content(tmp_path) -> None:
    all_path = tmp_path / "domain_all.txt"
    all_path.write_text(
        "unit-meta.hust.edu.vn\nunit.hust.edu.vn\n",
        encoding="utf-8",
    )
    active_path = tmp_path / "domain_active.txt"
    report_path = tmp_path / "domain_status.jsonl"

    results = run_audit(
        all_path,
        active_path,
        report_path,
        workers=1,
        probe=lambda hostname: ProbeResult(
            hostname,
            True,
            "public_response",
            scheme="https",
            status=200,
            final_url=f"https://{hostname}/",
            title="Same public site",
            content_digest="same-body-digest",
        ),
    )

    assert active_path.read_text() == "unit.hust.edu.vn\n"
    assert [(result.hostname, result.active, result.reason) for result in results] == [
        ("unit-meta.hust.edu.vn", False, "content_duplicate"),
        ("unit.hust.edu.vn", True, "public_response"),
    ]


def test_command_updates_domain_files_with_configured_paths(
    tmp_path, monkeypatch
) -> None:
    all_path = tmp_path / "all.txt"
    all_path.write_text("a.hust.edu.vn\n", encoding="utf-8")
    active_path = tmp_path / "active.txt"
    active_path.write_text("List active domain:\n", encoding="utf-8")
    report_path = tmp_path / "status.jsonl"
    monkeypatch.setattr(domain_audit, "resolve_hostname", lambda hostname: None)
    fetch_count = 0

    def fetch(url: str, timeout: float) -> ProbeAttempt:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count <= 2:
            return ProbeAttempt(error="temporary failure")
        return ProbeAttempt(
            status=200,
            final_url=url,
            body=b"<title>Working</title><h1>Public information</h1>",
        )

    monkeypatch.setattr(domain_audit, "fetch_url", fetch)

    exit_code = main(
        [
            "--all-domains",
            str(all_path),
            "--active-domains",
            str(active_path),
            "--report",
            str(report_path),
            "--workers",
            "1",
            "--timeout",
            "1",
            "--retries",
            "2",
        ]
    )

    assert exit_code == 0
    assert fetch_count == 3
    assert active_path.read_text() == "a.hust.edu.vn\n"
    assert json.loads(report_path.read_text())["active"] is True
