from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from posixpath import normpath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class UrlDecision:
    accepted: bool
    canonical_url: str | None
    reason: str | None
    target_kind: str


def canonicalize_url(
    url: str,
    *,
    trap_query_keys: tuple[str, ...] = ("sessionid", "jsessionid", "phpsessid"),
    check_traps: bool = True,
) -> tuple[str | None, str | None, str | None]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, None, "malformed_url"
    if parts.scheme.lower() not in {"http", "https"}:
        return None, None, "unsupported_scheme"
    if parts.username or parts.password or parts.hostname is None:
        return None, None, "credentials_or_missing_host"
    hostname = parts.hostname.lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return None, None, "invalid_port"
    if port and port not in {80, 443}:
        return None, hostname, "non_default_port"
    scheme = parts.scheme.lower()
    netloc = hostname
    if (scheme, port) not in {("http", 80), ("https", 443), ("http", None), ("https", None)}:
        netloc = f"{hostname}:{port}"
    path = normpath("/" + parts.path.lstrip("/"))
    if parts.path.endswith("/") and not path.endswith("/"):
        path += "/"
    if path == "/.":
        path = "/"
    lowered_path = path.lower()
    if check_traps and lowered_path.rstrip("/").split("/")[-1] in {"logout", "signout", "log-out"}:
        return None, hostname, "logout_path"
    segments = [segment for segment in path.split("/") if segment]
    if check_traps and len(segments) >= 4 and len(set(segments[-4:])) == 1:
        return None, hostname, "repeated_path_trap"
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if len(pairs) > 50:
        return None, hostname, "too_many_query_pairs"
    trap_keys = {key.lower() for key in trap_query_keys}
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        key_lower = key.lower()
        if key_lower in trap_keys or key_lower in {"fbclid", "gclid"} or key_lower.startswith("utm_"):
            continue
        if check_traps and key_lower in {"q", "query", "search", "keyword"} and (
            "search" in lowered_path or "find" in lowered_path
        ):
            return None, hostname, "search_trap"
        cleaned.append((key, value))
    if check_traps and "calendar" in lowered_path:
        for segment in segments:
            if segment.isdigit() and len(segment) == 4 and int(segment) > date.today().year + 2:
                return None, hostname, "calendar_trap"
    canonical = urlunsplit((scheme, netloc, path, urlencode(sorted(cleaned)), ""))
    return canonical, hostname, None


class UrlPolicy:
    def __init__(
        self,
        allowed_hostnames: frozenset[str],
        trap_query_keys: tuple[str, ...] = ("sessionid", "jsessionid", "phpsessid"),
    ) -> None:
        self.allowed_hostnames = frozenset(allowed_hostnames)
        self.trap_query_keys = trap_query_keys

    def decide(self, url: str, *, target_kind: str = "page") -> UrlDecision:
        canonical, hostname, reason = canonicalize_url(url, trap_query_keys=self.trap_query_keys)
        if reason is not None:
            return UrlDecision(False, None, reason, target_kind)
        assert canonical is not None and hostname is not None
        if hostname not in self.allowed_hostnames:
            return UrlDecision(False, None, "host_out_of_scope", target_kind)
        return UrlDecision(True, canonical, None, target_kind)


_AUTH_PATHS = frozenset({"login", "signin", "sign-in", "auth", "sso", "cas", "oauth"})
_ACTION_ENDPOINTS = frozenset(
    {"print", "share", "email", "submit", "comment", "cart", "checkout", "do_action"}
)


def classify_frontier_trap(url: str) -> str | None:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    segments = [s for s in path.split("/") if s]
    last_segment = segments[-1] if segments else ""

    if "wp-json" in segments or path.startswith("/wp-json"):
        return "api_endpoint_trap"

    if last_segment in _AUTH_PATHS:
        return "auth_trap"

    if path.endswith((".ics", ".ical")) or "calendar.ics" in path:
        return "calendar_export_trap"
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_dict = {k.lower(): v.lower() for k, v in pairs}
    if (
        query_dict.get("format") in {"ical", "ics"}
        or "export_ical" in query_dict
        or query_dict.get("action") == "export_calendar"
    ):
        return "calendar_export_trap"

    if last_segment in {"feed", "rss", "atom"}:
        return "feed_trap"
    if any(last_segment.endswith(ext) for ext in (".xml", ".rss", ".atom")):
        stem = last_segment.rsplit(".", 1)[0]
        if stem in {"feed", "rss", "atom"}:
            return "feed_trap"

    if last_segment in {"search", "find"}:
        return "search_trap"

    if last_segment in _ACTION_ENDPOINTS:
        return "action_endpoint_trap"
    if query_dict.get("action") in {"print", "share", "email", "export", "download"}:
        return "action_endpoint_trap"

    return None
