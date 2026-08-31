from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Mapping

import yaml


_DEFAULT_ACCEPTED_MIME_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "application/pdf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
)
_DEFAULT_MIME_PREFIXES = ("text/", "image/", "video/")
_DEFAULT_MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".webm", ".mov"
)


def _normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if not hostname or any(char.isspace() for char in hostname):
        raise ValueError(f"invalid hostname: {value!r}")
    parsed = urlsplit(f"//{hostname}")
    if parsed.hostname != hostname or parsed.port is not None or "/" in hostname:
        raise ValueError(f"expected hostname, got: {value!r}")
    return hostname


def load_hostnames(path: Path) -> frozenset[str]:
    hostnames: set[str] = set()
    in_domain_section = False
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if value.lower().startswith("list active domain"):
            in_domain_section = True
            continue
        if path.name == "domain_active.txt" and not in_domain_section:
            continue
        if value.lower().startswith(("http://", "https://")):
            raise ValueError(f"{path}:{line_number}: expected hostname, got URL: {value!r}")
        if value.startswith("-") or ":" in value or " " in value:
            continue
        try:
            hostnames.add(_normalize_hostname(value))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return frozenset(hostnames)


def validate_network_contact(contact: str) -> None:
    normalized = contact.strip().lower()
    if not normalized or ".invalid" in normalized:
        raise ValueError("a non-placeholder operator contact is required")


@dataclass(frozen=True, slots=True)
class CrawlerConfig:
    data_dir: Path
    hostnames: frozenset[str]
    playwright_hosts: frozenset[str]
    contact: str
    concurrent_requests: int = 16
    concurrent_per_host: int = 1
    throttle_start_seconds: float = 1.0
    throttle_max_seconds: float = 60.0
    retry_times: int = 3
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)
    retry_backoff_base_seconds: float = 2.0
    retry_max_delay_seconds: float = 3600.0
    retry_jitter: float = 0.2
    download_timeout_seconds: float = 60.0
    playwright_auto_fallback: bool = True
    playwright_abort_resource_types: tuple[str, ...] = ("image", "media")
    sitemap_max_depth: int = 5
    trap_query_keys: tuple[str, ...] = ("sessionid", "jsessionid", "phpsessid")
    trap_exceptions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    accepted_mime_types: tuple[str, ...] = _DEFAULT_ACCEPTED_MIME_TYPES
    accepted_mime_prefixes: tuple[str, ...] = _DEFAULT_MIME_PREFIXES
    media_extensions: tuple[str, ...] = _DEFAULT_MEDIA_EXTENSIONS
    max_response_bytes: int = 100 * 1024 * 1024
    warc_rotate_bytes: int = 1024 * 1024 * 1024
    min_free_bytes: int = 50 * 1024**3
    min_free_percent: float = 10.0

    @classmethod
    def load(
        cls,
        config_path: Path,
        domains_path: Path = Path("docs/domain_active.txt"),
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "CrawlerConfig":
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        environment = os.environ if environ is None else environ
        hostnames = load_hostnames(domains_path)
        configured_playwright = frozenset(
            _normalize_hostname(value) for value in raw.get("playwright_hosts", [])
        )
        unknown = configured_playwright - hostnames
        if unknown:
            raise ValueError(f"playwright hosts outside allowlist: {sorted(unknown)}")
        contact = environment.get("CRAWLER_CONTACT", "").strip()
        if not contact:
            contact = str(raw.get("contact", "")).strip()
        if not contact:
            raise ValueError("contact is required")
        values = {
            "data_dir": Path(raw.get("data_dir", "data")),
            "hostnames": hostnames,
            "playwright_hosts": configured_playwright,
            "contact": contact,
            "concurrent_requests": int(raw.get("concurrent_requests", 16)),
            "concurrent_per_host": int(raw.get("concurrent_per_host", 1)),
            "throttle_start_seconds": float(raw.get("throttle_start_seconds", 1.0)),
            "throttle_max_seconds": float(raw.get("throttle_max_seconds", 60.0)),
            "retry_times": int(raw.get("retry_times", 3)),
            "retry_statuses": tuple(int(status) for status in raw.get("retry_statuses", (408, 425, 429, 500, 502, 503, 504))),
            "retry_backoff_base_seconds": float(raw.get("retry_backoff_base_seconds", 2.0)),
            "retry_max_delay_seconds": float(raw.get("retry_max_delay_seconds", 3600.0)),
            "retry_jitter": float(raw.get("retry_jitter", 0.2)),
            "download_timeout_seconds": float(raw.get("download_timeout_seconds", 60.0)),
            "playwright_auto_fallback": bool(raw.get("playwright_auto_fallback", True)),
            "playwright_abort_resource_types": tuple(str(value) for value in raw.get("playwright_abort_resource_types", ("image", "media"))),
            "sitemap_max_depth": int(raw.get("sitemap_max_depth", 5)),
            "trap_query_keys": tuple(str(value).lower() for value in raw.get("trap_query_keys", ("sessionid", "jsessionid", "phpsessid"))),
            "trap_exceptions": tuple(
                (_normalize_hostname(host), tuple(str(reason) for reason in reasons))
                for host, reasons in (raw.get("trap_exceptions", {}) or {}).items()
            ),
            "accepted_mime_types": tuple(str(value).lower() for value in raw.get("accepted_mime_types", _DEFAULT_ACCEPTED_MIME_TYPES)),
            "accepted_mime_prefixes": tuple(str(value).lower() for value in raw.get("accepted_mime_prefixes", _DEFAULT_MIME_PREFIXES)),
            "media_extensions": tuple(str(value).lower() for value in raw.get("media_extensions", _DEFAULT_MEDIA_EXTENSIONS)),
            "max_response_bytes": int(raw.get("max_response_bytes", 100 * 1024 * 1024)),
            "warc_rotate_bytes": int(raw.get("warc_rotate_bytes", 1024 * 1024 * 1024)),
            "min_free_bytes": int(raw.get("min_free_bytes", 50 * 1024**3)),
            "min_free_percent": float(raw.get("min_free_percent", 10.0)),
        }
        positive = ("concurrent_requests", "concurrent_per_host", "retry_times", "retry_backoff_base_seconds", "retry_max_delay_seconds", "download_timeout_seconds", "sitemap_max_depth", "max_response_bytes", "warc_rotate_bytes", "min_free_bytes")
        for name in positive:
            if values[name] <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= values["retry_jitter"] <= 1:
            raise ValueError("retry_jitter must be between 0 and 1")
        if not values["retry_statuses"] or any(status < 100 or status > 599 for status in values["retry_statuses"]):
            raise ValueError("retry_statuses must contain HTTP status codes")
        if values["throttle_start_seconds"] <= 0 or values["throttle_max_seconds"] <= 0 or values["throttle_start_seconds"] > values["throttle_max_seconds"]:
            raise ValueError("throttle delays must be positive and ordered")
        if values["min_free_percent"] < 0 or values["min_free_percent"] > 100:
            raise ValueError("min_free_percent must be between 0 and 100")
        known_exception_hosts = {host for host, _ in values["trap_exceptions"]}
        if not known_exception_hosts <= hostnames:
            raise ValueError("trap exception host outside allowlist")
        return cls(**values)
