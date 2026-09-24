from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal
from urllib.parse import urlsplit
from xml.etree import ElementTree

from hust_crawler.policies.url import canonicalize_url
from .content import normalize_url


DiscoveryStrategy = Literal["sitemap-only", "hybrid-unified"]


@dataclass
class HostDiscoveryState:
    hostname: str
    robots_complete: bool = False
    pending_sitemaps: set[str] = field(default_factory=set)
    registered_sitemaps: dict[str, bool] = field(default_factory=dict)
    sitemaps_succeeded: int = 0
    sitemaps_failed: int = 0
    sitemaps_absent: int = 0
    sitemap_targets: set[str] = field(default_factory=set)
    html_targets: set[str] = field(default_factory=set)
    mode: Literal["pending", "sitemap", "sitemap_partial", "no_sitemap", "exact"] = "pending"

    def to_record(self) -> dict[str, object]:
        return {
            "hostname": self.hostname,
            "robots_complete": self.robots_complete,
            "pending_sitemaps": sorted(self.pending_sitemaps),
            "registered_sitemaps": dict(self.registered_sitemaps),
            "sitemaps_succeeded": self.sitemaps_succeeded,
            "sitemaps_failed": self.sitemaps_failed,
            "sitemaps_absent": self.sitemaps_absent,
            "sitemap_targets": sorted(self.sitemap_targets),
            "html_targets": sorted(self.html_targets),
            "mode": self.mode,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> HostDiscoveryState:
        return cls(
            hostname=str(data["hostname"]),
            robots_complete=bool(data.get("robots_complete", False)),
            pending_sitemaps=set(data.get("pending_sitemaps", ())),
            registered_sitemaps=dict(data.get("registered_sitemaps", {})),
            sitemaps_succeeded=int(data.get("sitemaps_succeeded", 0)),
            sitemaps_failed=int(data.get("sitemaps_failed", 0)),
            sitemaps_absent=int(data.get("sitemaps_absent", 0)),
            sitemap_targets=set(data.get("sitemap_targets", ())),
            html_targets=set(data.get("html_targets", ())),
            mode=data.get("mode", "pending"),  # type: ignore[arg-type]
        )

    def to_summary(self) -> dict[str, object]:
        if self.mode == "exact":
            return {"mode": "exact"}
        return {
            "mode": self.mode,
            "robots_complete": self.robots_complete,
            "pending_sitemaps": len(self.pending_sitemaps),
            "sitemaps_succeeded": self.sitemaps_succeeded,
            "sitemaps_failed": self.sitemaps_failed,
            "sitemaps_absent": self.sitemaps_absent,
            "sitemap_targets": len(self.sitemap_targets),
            "html_targets": len(self.html_targets),
        }


class SitemapDiscoveryCoordinator:
    def __init__(
        self,
        exact_hostnames: frozenset[str] = frozenset(),
        on_change: Callable[[dict[str, object]], None] | None = None,
        strategy: DiscoveryStrategy = "sitemap-only",
    ) -> None:
        self.strategy = strategy
        self.exact_hostnames = frozenset(exact_hostnames)
        self.on_change = on_change
        self.hosts: dict[str, HostDiscoveryState] = {}
        for h in self.exact_hostnames:
            self.hosts[h] = HostDiscoveryState(hostname=h, mode="exact")

    def register_exact_host(self, hostname: str) -> None:
        if hostname not in self.hosts:
            self.hosts[hostname] = HostDiscoveryState(hostname=hostname, mode="exact")
            self._notify(hostname)

    def _get_or_create_host(self, hostname: str) -> HostDiscoveryState:
        if hostname not in self.hosts:
            self.hosts[hostname] = HostDiscoveryState(hostname=hostname)
        return self.hosts[hostname]

    @staticmethod
    def _clean_url(url: str) -> str:
        canonical, _, _ = canonicalize_url(url, check_traps=False)
        return canonical or url

    def _recompute_mode(self, host: HostDiscoveryState) -> None:
        if host.mode == "exact":
            return
        if host.sitemap_targets:
            host.mode = "sitemap_partial" if host.sitemaps_failed else "sitemap"
        elif host.robots_complete and not host.pending_sitemaps:
            host.mode = "no_sitemap"
        else:
            host.mode = "pending"

    def _notify(self, hostname: str) -> None:
        if self.on_change is not None and hostname in self.hosts:
            self.on_change(self.hosts[hostname].to_record())

    def register_sitemap(self, hostname: str, url: str, *, required: bool) -> bool:
        clean = self._clean_url(url)
        host = self._get_or_create_host(hostname)
        if clean in host.registered_sitemaps:
            if required and not host.registered_sitemaps[clean]:
                host.registered_sitemaps[clean] = True
                self._notify(hostname)
            return False
        host.registered_sitemaps[clean] = required
        host.pending_sitemaps.add(clean)
        self._recompute_mode(host)
        self._notify(hostname)
        return True

    def complete_robots(self, hostname: str) -> None:
        host = self._get_or_create_host(hostname)
        host.robots_complete = True
        self._recompute_mode(host)
        self._notify(hostname)

    def complete_sitemap(
        self,
        hostname: str,
        url: str,
        *,
        result: Literal["success", "failed", "absent"],
        targets: int = 0,
    ) -> None:
        clean = self._clean_url(url)
        host = self._get_or_create_host(hostname)
        host.pending_sitemaps.discard(clean)
        is_required = host.registered_sitemaps.get(clean, False)
        if result == "success":
            host.sitemaps_succeeded += 1
        elif result == "absent":
            if is_required:
                host.sitemaps_failed += 1
            else:
                host.sitemaps_absent += 1
        elif result == "failed":
            host.sitemaps_failed += 1
        self._recompute_mode(host)
        self._notify(hostname)

    def record_sitemap_target(self, hostname: str, url: str) -> None:
        clean = self._clean_url(url)
        host = self._get_or_create_host(hostname)
        host.sitemap_targets.add(clean)
        self._recompute_mode(host)
        self._notify(hostname)

    def record_html_target(self, hostname: str, url: str) -> None:
        host = self._get_or_create_host(hostname)
        host.html_targets.add(self._clean_url(url))
        self._notify(hostname)

    def snapshot(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "sitemap_url_limits": "unbounded",
            "hosts": {
                h: self.hosts[h].to_summary() for h in sorted(self.hosts.keys())
            },
        }

    def restore(self, records: Iterable[dict[str, object]]) -> None:
        for record in records:
            state = HostDiscoveryState.from_record(record)
            self.hosts[state.hostname] = state


class SitemapParseError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SitemapParseResult:
    kind: Literal["index", "urlset"]
    locations: tuple[str, ...]


def _maybe_decompress(body: bytes, base_url: str, content_type: str = "") -> bytes:
    is_gzip = False
    if body.startswith(b"\x1f\x8b"):
        is_gzip = True
    elif "gzip" in content_type.lower():
        is_gzip = True
    else:
        path = urlsplit(base_url).path.lower()
        if path.endswith(".gz"):
            is_gzip = True

    if not is_gzip:
        return body

    try:
        return gzip.decompress(body)
    except Exception as exc:
        raise SitemapParseError("invalid_gzip") from exc


def _ordered_unique_locations(
    root: ElementTree.Element, child_name: str, base_url: str
) -> tuple[str, ...]:
    seen: set[str] = set()
    locations: list[str] = []
    for elem in root:
        elem_tag = elem.tag.rsplit("}", 1)[-1].lower()
        if elem_tag != child_name:
            continue
        for child in elem:
            child_tag = child.tag.rsplit("}", 1)[-1].lower()
            if child_tag == "loc":
                raw_loc = (child.text or "").strip()
                if not raw_loc:
                    continue
                normalized = normalize_url(raw_loc, base_url)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    locations.append(normalized)
    return tuple(locations)


def parse_sitemap(body: bytes, base_url: str, content_type: str = "") -> SitemapParseResult:
    source = _maybe_decompress(body, base_url, content_type)
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise SitemapParseError("invalid_xml") from exc
    except Exception as exc:
        raise SitemapParseError("invalid_xml") from exc

    local_name = root.tag.rsplit("}", 1)[-1].lower()
    if local_name not in {"sitemapindex", "urlset"}:
        raise SitemapParseError("unsupported_root")

    child_name = "sitemap" if local_name == "sitemapindex" else "url"
    locations = _ordered_unique_locations(root, child_name, base_url)
    return SitemapParseResult("index" if local_name == "sitemapindex" else "urlset", locations)
