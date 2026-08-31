from __future__ import annotations

from datetime import date
from posixpath import normpath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import UrlDecision


class UrlPolicy:
    _TRACKING_PREFIXES = ("utm_",)
    _TRACKING_KEYS = {"fbclid", "gclid", "sessionid", "jsessionid", "phpsessid"}

    def __init__(self, allowed_hostnames: frozenset[str]) -> None:
        self.allowed_hostnames = allowed_hostnames

    def _canonicalize(self, url: str) -> tuple[str | None, str | None, str | None]:
        try:
            parts = urlsplit(url)
        except ValueError:
            return None, None, "malformed_url"
        if parts.scheme not in {"http", "https"}:
            return None, None, "unsupported_scheme"
        if parts.username or parts.password or parts.hostname is None:
            return None, None, "credentials_or_missing_host"
        hostname = parts.hostname.lower().rstrip(".")
        try:
            port = parts.port
        except ValueError:
            return None, None, "invalid_port"
        if port and port not in {80, 443}:
            return None, None, "non_default_port"
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
        if lowered_path.rstrip("/").split("/")[-1] in {"logout", "signout", "log-out"}:
            return None, hostname, "logout_path"
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 4 and len(set(segments[-4:])) == 1:
            return None, hostname, "repeated_path_trap"
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if len(pairs) > 50:
            return None, hostname, "too_many_query_pairs"
        cleaned: list[tuple[str, str]] = []
        for key, value in pairs:
            key_lower = key.lower()
            if key_lower in self._TRACKING_KEYS or any(key_lower.startswith(prefix) for prefix in self._TRACKING_PREFIXES):
                continue
            if key_lower in {"q", "query", "search", "keyword"} and ("search" in lowered_path or "find" in lowered_path):
                return None, hostname, "search_trap"
            cleaned.append((key, value))
        if "calendar" in lowered_path:
            for segment in segments:
                if segment.isdigit() and len(segment) == 4 and int(segment) > date.today().year + 2:
                    return None, hostname, "calendar_trap"
        canonical = urlunsplit((scheme, netloc, path, urlencode(sorted(cleaned)), ""))
        return canonical, hostname, None

    def decide(
        self,
        url: str,
        *,
        target_kind: str = "page",
        source_url: str | None = None,
    ) -> UrlDecision:
        canonical, hostname, reason = self._canonicalize(url)
        if reason is not None:
            return UrlDecision(False, None, reason, target_kind, False)
        assert canonical is not None and hostname is not None
        if hostname in self.allowed_hostnames:
            return UrlDecision(True, canonical, None, target_kind, target_kind == "media")
        return UrlDecision(False, None, "host_out_of_scope", target_kind, False)

    def decide_historical(self, url: str, mime: str) -> UrlDecision:
        target_kind = "media" if mime.lower().split(";", 1)[0].startswith(("image/", "video/")) else "page"
        canonical, hostname, reason = self._canonicalize(url)
        if reason is not None:
            return UrlDecision(False, None, reason, target_kind, False)
        assert canonical is not None and hostname is not None
        if hostname in self.allowed_hostnames:
            return UrlDecision(True, canonical, None, target_kind, target_kind == "media")
        return UrlDecision(False, None, "host_out_of_scope", target_kind, False)
