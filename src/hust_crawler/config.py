from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Mapping

import yaml

from typing import TYPE_CHECKING
from typing import Literal

if TYPE_CHECKING:
    from .crawl.profiles import RuntimeTuning




def normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if not hostname or any(char.isspace() for char in hostname):
        raise ValueError(f"invalid hostname: {value!r}")
    parsed = urlsplit(f"//{hostname}")
    if parsed.hostname != hostname or parsed.port is not None or "/" in hostname:
        raise ValueError(f"expected hostname, got: {value!r}")
    return hostname


_normalize_hostname = normalize_hostname


def load_hostnames(path: Path) -> frozenset[str]:
    hostnames: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
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
    hostnames: frozenset[str]
    contact: str
    concurrent_requests: int = 64
    concurrent_per_host: int = 2
    playwright_max_pages: int = 8
    download_delay_seconds: float = 0.25
    throttle_start_seconds: float = 0.25
    throttle_max_seconds: float = 5.0
    retry_times: int = 3
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)
    retry_backoff_base_seconds: float = 2.0
    retry_max_delay_seconds: float = 3600.0
    download_timeout_seconds: float = 60.0
    max_response_bytes: int = 100 * 1024 * 1024
    user_agent_name: str = "HUSTPublicCrawler"
    user_agent_version: str = "1.0"
    access_policy_revision: int = 2
    browser_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )
    robots_txt_obey: bool = False
    cookies_enabled: bool = False
    redirect_max_times: int = 20
    autothrottle_enabled: bool = True
    autothrottle_target_concurrency: float = 2.0
    resource_limit_percent: int = 80
    assets: Literal["content-only", "all"] = "content-only"
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_file_bytes: int = 100 * 1024**3
    max_query_variants_per_path: int = 20
    pagination_empty_pages: int = 3
    max_urls_per_host: int = 100_000
    max_total_urls: int = 1_000_000
    journal_batch_size: int = 500
    journal_flush_seconds: float = 2.0
    progress_interval_seconds: float = 10.0

    @classmethod
    def load(
        cls,
        config_path: Path,
        domains_path: Path = Path("docs/domain_active.txt"),
        *,
        environ: Mapping[str, str] | None = None,
        hostnames: frozenset[str] | None = None,
    ) -> "CrawlerConfig":
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        environment = os.environ if environ is None else environ
        hostnames = load_hostnames(domains_path) if hostnames is None else frozenset(hostnames)
        contact = environment.get("CRAWLER_CONTACT", "").strip()
        if not contact:
            contact = str(raw.get("contact", "")).strip()
        if not contact:
            raise ValueError("contact is required")
        validate_network_contact(contact)
        required = (
            "user_agent_name", "user_agent_version", "concurrent_requests",
            "access_policy_revision", "browser_user_agent",
            "concurrent_per_host", "download_delay_seconds", "download_timeout_seconds",
            "max_response_bytes", "robots_txt_obey", "cookies_enabled", "redirect_max_times",
            "autothrottle_enabled", "autothrottle_target_concurrency", "throttle_start_seconds",
            "throttle_max_seconds", "retry_times", "retry_statuses", "retry_backoff_base_seconds",
            "retry_max_delay_seconds", "playwright_max_pages", "resource_limit_percent", "assets",
            "max_file_bytes", "max_total_file_bytes", "max_query_variants_per_path",
            "pagination_empty_pages", "max_urls_per_host", "max_total_urls", "journal_batch_size",
            "journal_flush_seconds", "progress_interval_seconds",
        )
        missing = sorted(name for name in required if name not in raw)
        if "contact" not in raw and not environment.get("CRAWLER_CONTACT", "").strip():
            missing.insert(0, "contact")
        if missing:
            raise ValueError(f"missing required crawler settings: {', '.join(missing)}")

        def required_bool(name: str) -> bool:
            value = raw[name]
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            return value

        values = {
            "hostnames": hostnames,
            "contact": contact,
            "user_agent_name": str(raw["user_agent_name"]), "user_agent_version": str(raw["user_agent_version"]),
            "access_policy_revision": int(raw["access_policy_revision"]),
            "browser_user_agent": str(raw["browser_user_agent"]),
            "concurrent_requests": int(raw["concurrent_requests"]), "concurrent_per_host": int(raw["concurrent_per_host"]),
            "playwright_max_pages": int(raw["playwright_max_pages"]), "download_delay_seconds": float(raw["download_delay_seconds"]),
            "throttle_start_seconds": float(raw["throttle_start_seconds"]), "throttle_max_seconds": float(raw["throttle_max_seconds"]),
            "retry_times": int(raw["retry_times"]), "retry_statuses": tuple(int(status) for status in raw["retry_statuses"]),
            "retry_backoff_base_seconds": float(raw["retry_backoff_base_seconds"]), "retry_max_delay_seconds": float(raw["retry_max_delay_seconds"]),
            "download_timeout_seconds": float(raw["download_timeout_seconds"]), "max_response_bytes": int(raw["max_response_bytes"]),
            "robots_txt_obey": required_bool("robots_txt_obey"), "cookies_enabled": required_bool("cookies_enabled"),
            "redirect_max_times": int(raw["redirect_max_times"]), "autothrottle_enabled": required_bool("autothrottle_enabled"),
            "autothrottle_target_concurrency": float(raw["autothrottle_target_concurrency"]), "resource_limit_percent": int(raw["resource_limit_percent"]),
            "assets": raw["assets"], "max_file_bytes": int(raw["max_file_bytes"]), "max_total_file_bytes": int(raw["max_total_file_bytes"]),
            "max_query_variants_per_path": int(raw["max_query_variants_per_path"]), "pagination_empty_pages": int(raw["pagination_empty_pages"]),
            "max_urls_per_host": int(raw["max_urls_per_host"]), "max_total_urls": int(raw["max_total_urls"]),
            "journal_batch_size": int(raw["journal_batch_size"]), "journal_flush_seconds": float(raw["journal_flush_seconds"]),
            "progress_interval_seconds": float(raw["progress_interval_seconds"]),
        }
        positive = tuple(name for name in required if name not in {"user_agent_name", "user_agent_version", "browser_user_agent", "robots_txt_obey", "cookies_enabled", "autothrottle_enabled", "assets", "retry_statuses"})
        for name in positive:
            if values[name] <= 0:
                raise ValueError(f"{name} must be positive")
        if not values["retry_statuses"] or any(status < 100 or status > 599 for status in values["retry_statuses"]):
            raise ValueError("retry_statuses must contain HTTP status codes")
        if values["download_delay_seconds"] <= 0:
            raise ValueError("download_delay_seconds must be positive")
        if values["throttle_start_seconds"] <= 0 or values["throttle_max_seconds"] <= 0 or values["throttle_start_seconds"] > values["throttle_max_seconds"]:
            raise ValueError("throttle delays must be positive and ordered")
        if values["autothrottle_target_concurrency"] > values["concurrent_per_host"]:
            raise ValueError("autothrottle_target_concurrency must not exceed concurrent_per_host")
        if not 1 <= values["resource_limit_percent"] <= 100:
            raise ValueError("resource_limit_percent must be between 1 and 100")
        if values["assets"] not in {"content-only", "all"}:
            raise ValueError("assets must be content-only or all")
        return cls(**values)


_RUNTIME_CONFIG_KEYS = {
    "concurrent_requests",
    "concurrent_per_host",
    "playwright_max_pages",
    "download_delay_seconds",
    "throttle_start_seconds",
    "throttle_max_seconds",
    "autothrottle_enabled", "autothrottle_target_concurrency", "download_timeout_seconds",
    "retry_times", "retry_backoff_base_seconds", "retry_max_delay_seconds",
    "playwright_max_pages", "resource_limit_percent", "journal_batch_size",
    "journal_flush_seconds", "progress_interval_seconds",
}


def _normalized_snapshot(values: dict[str, object]) -> dict[str, object]:
    def normalize(value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (set, frozenset)):
            return sorted(normalize(item) for item in value)
        if isinstance(value, tuple):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
        return value

    return {key: normalize(value) for key, value in sorted(values.items())}


def config_snapshot(config: CrawlerConfig, *, concurrency: int) -> dict[str, object]:
    """Return a deterministic JSON-safe configuration snapshot for phase state."""

    values = asdict(config)
    values["concurrent_requests"] = concurrency

    return _normalized_snapshot(values)


def semantic_config_snapshot(config: CrawlerConfig) -> dict[str, object]:
    """Return the configuration fields that define phase identity."""

    values = asdict(config)
    for key in _RUNTIME_CONFIG_KEYS:
        values.pop(key, None)
    return _normalized_snapshot(values)


def runtime_config_snapshot(config: CrawlerConfig, tuning: "RuntimeTuning") -> dict[str, object]:
    """Return the active performance knobs recorded for a run."""

    values = asdict(config)
    values = {key: values[key] for key in _RUNTIME_CONFIG_KEYS}
    values.update(asdict(tuning))
    return _normalized_snapshot(values)
