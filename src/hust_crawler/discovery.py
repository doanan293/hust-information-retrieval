from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree

from .cdxj import load_latest
from .url_policy import UrlPolicy


@dataclass(frozen=True, slots=True)
class DiscoveredUrl:
    url: str
    source: str
    parent_url: str | None = None
    target_kind: str = "page"
    leaf: bool = False


@dataclass(frozen=True, slots=True)
class SitemapParseResult:
    discoveries: tuple[DiscoveredUrl, ...]
    error: str | None = None


def root_seeds(
    hostnames: frozenset[str], *, pilot: bool = False
) -> tuple[DiscoveredUrl, ...]:
    seeds: list[DiscoveredUrl] = []
    for host in sorted(hostnames):
        seeds.extend(
            (
                DiscoveredUrl(f"https://{host}/", "root"),
                DiscoveredUrl(f"https://{host}/robots.txt", "robots", target_kind="robots"),
                DiscoveredUrl(
                    f"https://{host}/sitemap.xml", "conventional_sitemap", target_kind="sitemap"
                ),
            )
        )
        if not pilot:
            seeds.extend(
                (
                    DiscoveredUrl(
                        f"https://{host}/sitemap_index.xml",
                        "conventional_sitemap",
                        target_kind="sitemap",
                    ),
                    DiscoveredUrl(
                        f"https://{host}/sitemap-index.xml",
                        "conventional_sitemap",
                        target_kind="sitemap",
                    ),
                )
            )
    return tuple(seeds)


def parse_robots_sitemaps(text: str, base_url: str) -> tuple[str, ...]:
    urls = {
        urljoin(base_url, line.partition(":")[2].strip())
        for line in text.splitlines()
        if line.partition(":")[0].strip().lower() == "sitemap"
        and line.partition(":")[2].strip()
    }
    return tuple(sorted(urls))


def parse_sitemap_xml(payload: bytes, source_url: str) -> SitemapParseResult:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return SitemapParseResult((), "malformed_sitemap")
    root_kind = root.tag.rsplit("}", 1)[-1].lower()
    target_kind = "sitemap" if root_kind == "sitemapindex" else "page"
    if root_kind not in {"sitemapindex", "urlset"}:
        return SitemapParseResult((), "unsupported_sitemap_root")
    discoveries = tuple(
        DiscoveredUrl(
            loc.text.strip(),
            "sitemap",
            parent_url=source_url,
            target_kind=target_kind,
        )
        for loc in root.iter()
        if loc.tag.rsplit("}", 1)[-1].lower() == "loc" and loc.text and loc.text.strip()
    )
    return SitemapParseResult(discoveries)


def historical_seeds(index_path: Path, policy: UrlPolicy) -> tuple[DiscoveredUrl, ...]:
    seeds: list[DiscoveredUrl] = []
    for entry in sorted(load_latest(index_path).values(), key=lambda value: value.url):
        decision = policy.decide_historical(entry.url, entry.mime)
        if decision.accepted and decision.canonical_url:
            seeds.append(
                DiscoveredUrl(
                    decision.canonical_url,
                    "history",
                    target_kind=decision.target_kind,
                    leaf=decision.leaf,
                )
            )
    return tuple(seeds)
