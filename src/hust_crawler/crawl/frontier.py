from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit

from hust_crawler.policies.url import canonicalize_url, classify_frontier_trap

from .assets import AssetGroup, AssetRole
from .link_policy import LinkKind
from .options import CrawlOptions
from .seeds import SeedMode, SeedSet
from .state import FamilyObservation, RouteFamilyState

_PAGINATION_QUERY_KEYS = frozenset({"page", "p", "paged", "pg", "offset", "start", "skip"})
_FILTER_QUERY_KEYS = frozenset(
    {
        "sort",
        "order",
        "filter",
        "dir",
        "orderby",
        "sortby",
        "category",
        "tag",
        "view",
        "display",
        "lang",
        "tab",
    }
)
_NAVIGATION_PATH_SEGMENTS = frozenset(
    {
        "list",
        "listing",
        "all",
        "browse",
        "category",
        "categories",
        "tag",
        "tags",
        "archive",
        "archives",
        "search",
        "find",
    }
)

DiscoverySource = Literal["seed", "sitemap", "html_link"]
Purpose = Literal["robots", "sitemap", "page", "asset"]


@dataclass(frozen=True, slots=True)
class Candidate:
    url: str
    source_mode: SeedMode
    discovered_from: str | None
    kind: str
    purpose: Purpose
    explicit: bool = False
    discovery_source: DiscoverySource = "seed"
    link_kind: LinkKind | None = None
    family_key: str | None = None
    ordinal: int | None = None
    asset_role: AssetRole | None = None
    asset_group: AssetGroup | None = None


@dataclass(frozen=True, slots=True)
class FrontierDecision:
    action: Literal["schedule", "record_only", "reject"]
    canonical_url: str | None
    reason: str | None
    priority: int


class FrontierPolicy:
    def __init__(
        self,
        seeds: SeedSet,
        options: CrawlOptions,
        trap_exceptions: (
            Mapping[str, tuple[str, ...]]
            | tuple[tuple[str, tuple[str, ...]], ...]
            | None
        ) = None,
        route_families: Iterable[RouteFamilyState] = (),
    ) -> None:
        self.seeds = seeds
        self.options = options
        if not trap_exceptions:
            self.trap_exceptions = {}
        elif isinstance(trap_exceptions, Mapping):
            self.trap_exceptions = {k: set(v) for k, v in trap_exceptions.items()}
        else:
            self.trap_exceptions = {k: set(v) for k, v in trap_exceptions}

        self._scheduled_urls: set[str] = set()
        self._total_scheduled: int = 0
        self._host_scheduled: dict[str, int] = defaultdict(int)
        self._query_variants: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._consecutive_empty_pages: dict[tuple[str, str, str], int] = defaultdict(int)
        self._closed_branches: set[tuple[str, str, str]] = set()
        self._route_families: dict[str, RouteFamilyState] = {
            f.family_key: f for f in route_families
        }
        self.stats: dict[str, int] = defaultdict(int)

    def family_is_closed(self, family_key: str) -> bool:
        family = self._route_families.get(family_key)
        return family.closed if family is not None else False

    def observe_family(self, observation: FamilyObservation) -> RouteFamilyState:
        current = self._route_families.get(
            observation.family_key,
            RouteFamilyState(
                family_key=observation.family_key,
                kind=observation.kind,
                scheduled_next_ordinal=observation.ordinal,
                content_fingerprints=(),
                target_fingerprints=(),
                accepted_targets=(),
                consecutive_zero_novelty=0,
                closed=False,
                closure_reason=None,
            ),
        )
        if current.closed:
            return current

        new_targets = tuple(
            sorted(set(observation.content_targets) - set(current.accepted_targets))
        )
        repeated_content = (
            observation.content_fingerprint in current.content_fingerprints
        )
        repeated_targets = (
            observation.target_fingerprint in current.target_fingerprints
        )
        zero_count = (
            current.consecutive_zero_novelty + 1 if not new_targets else 0
        )
        closure_reason = (
            "repeated_content_fingerprint"
            if repeated_content
            else "repeated_target_set"
            if repeated_targets
            else "zero_novelty"
            if zero_count >= self.options.pagination_empty_pages
            else None
        )
        closed = closure_reason is not None

        new_state = RouteFamilyState(
            family_key=observation.family_key,
            kind=observation.kind,
            scheduled_next_ordinal=observation.ordinal,
            content_fingerprints=tuple(
                dict.fromkeys((*current.content_fingerprints, observation.content_fingerprint))
            ),
            target_fingerprints=tuple(
                dict.fromkeys((*current.target_fingerprints, observation.target_fingerprint))
            ),
            accepted_targets=tuple(
                sorted(set(current.accepted_targets) | set(observation.content_targets))
            ),
            consecutive_zero_novelty=zero_count,
            closed=closed,
            closure_reason=closure_reason,
        )
        self._route_families[observation.family_key] = new_state
        return new_state

    def _pagination_branch(self, url: str) -> tuple[str, str, str] | None:
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        has_page_query = any(k.lower() in _PAGINATION_QUERY_KEYS for k, _ in pairs)
        segments = [s for s in parsed.path.split("/") if s]
        has_page_path = (
            len(segments) >= 2
            and segments[-2].lower() in {"page", "p", "pages"}
            and segments[-1].isdigit()
        )

        if not has_page_query and not has_page_path:
            return None

        if has_page_path:
            norm_path = ("/" + "/".join(segments[:-2])) if segments[:-2] else "/"
        else:
            norm_path = parsed.path.rstrip("/") or "/"

        filtered_query = urlencode(
            sorted([(k, v) for k, v in pairs if k.lower() not in _PAGINATION_QUERY_KEYS])
        )
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (hostname, norm_path, filtered_query)

    def observe_pagination(self, page_url: str, new_content_count: int) -> None:
        canonical, _, _ = canonicalize_url(page_url)
        if not canonical:
            return
        branch = self._pagination_branch(canonical)
        if not branch:
            return
        if new_content_count == 0:
            self.stats["frontier/pagination_empty_observation"] += 1
            self._consecutive_empty_pages[branch] += 1
            if self._consecutive_empty_pages[branch] >= self.options.pagination_empty_pages:
                self._closed_branches.add(branch)
        else:
            self._consecutive_empty_pages[branch] = 0

    def page_role(
        self, url: str, link_kind: LinkKind | None = None
    ) -> Literal["content", "navigation"]:
        if link_kind == "content":
            return "content"
        if link_kind == "pagination":
            return "navigation"
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if any(k.lower() in _PAGINATION_QUERY_KEYS for k, _ in pairs):
            return "navigation"
        if any(k.lower() in _FILTER_QUERY_KEYS for k, _ in pairs):
            return "navigation"
        path = parsed.path.rstrip("/")
        if not path:
            return "content"
        segments = [s.lower() for s in path.split("/") if s]
        if any(s in _NAVIGATION_PATH_SEGMENTS for s in segments):
            return "navigation"
        if len(segments) >= 2 and segments[-2] in {"page", "p", "pages"} and segments[-1].isdigit():
            return "navigation"
        return "content"


    def consider(self, candidate: Candidate) -> FrontierDecision:
        canonical, hostname, reason = canonicalize_url(
            candidate.url,
            check_traps=candidate.discovery_source == "html_link",
        )
        if not canonical:
            return FrontierDecision("reject", None, reason or "malformed_url", 0)
        assert hostname is not None

        # Reject malformed/out-of-scope
        if candidate.explicit:
            if hostname not in self.seeds.allowed_hostnames:
                return FrontierDecision("reject", canonical, "host_out_of_scope", 0)
        else:
            if candidate.purpose == "asset":
                if candidate.discovered_from:
                    _, parent_host, _ = canonicalize_url(candidate.discovered_from)
                    if parent_host is None or parent_host not in self.seeds.allowed_hostnames:
                        return FrontierDecision("reject", canonical, "host_out_of_scope", 0)
                else:
                    if hostname not in self.seeds.allowed_hostnames:
                        return FrontierDecision("reject", canonical, "host_out_of_scope", 0)
            else:
                if candidate.source_mode != "recursive":
                    return FrontierDecision("reject", canonical, "exact_seed_no_expansion", 0)
                if candidate.discovered_from:
                    parent_host = (urlsplit(candidate.discovered_from).hostname or "").lower().rstrip(".")
                    if parent_host not in self.seeds.recursive_hostnames or hostname != parent_host:
                        return FrontierDecision("reject", canonical, "host_out_of_scope", 0)
                else:
                    if hostname not in self.seeds.recursive_hostnames:
                        return FrontierDecision("reject", canonical, "host_out_of_scope", 0)

        # Return record_only for canonical URLs already in _scheduled_urls
        if canonical in self._scheduled_urls:
            return FrontierDecision("record_only", canonical, "already_scheduled", 0)

        # Reject classify_frontier_trap(canonical) for html_link candidates
        if candidate.discovery_source == "html_link":
            trap = classify_frontier_trap(canonical)
            if trap:
                exceptions = self.trap_exceptions.get(hostname, set())
                if trap not in exceptions:
                    self.stats[f"frontier/reject/{trap}"] += 1
                    self.stats[f"frontier/host/{hostname}/reject/{trap}"] += 1
                    return FrontierDecision("reject", canonical, trap, 0)

        # Reject a closed pagination branch
        if candidate.purpose != "asset":
            branch = self._pagination_branch(canonical)
            if branch and branch in self._closed_branches:
                self.stats["frontier/reject/pagination_closed"] += 1
                self.stats[f"frontier/host/{hostname}/reject/pagination_closed"] += 1
                return FrontierDecision("reject", canonical, "pagination_closed", 0)

        # Reject total_url_limit or host_url_limit for every source
        if self._total_scheduled >= self.options.max_total_urls:
            self.stats["frontier/reject/total_url_limit"] += 1
            return FrontierDecision("reject", canonical, "total_url_limit", 0)

        if self._host_scheduled[hostname] >= self.options.max_urls_per_host:
            self.stats["frontier/reject/host_url_limit"] += 1
            self.stats[f"frontier/host/{hostname}/reject/host_url_limit"] += 1
            return FrontierDecision("reject", canonical, "host_url_limit", 0)

        # Enforce query variants only when source is html_link, page role is navigation,
        # and parsed.query is non-empty
        parsed = urlsplit(canonical)
        role = self.page_role(canonical, candidate.link_kind)
        charges_query_variant_budget = (
            candidate.purpose != "asset"
            and candidate.discovery_source == "html_link"
            and candidate.link_kind not in {"content", "pagination"}
            and role == "navigation"
            and parsed.query
        )
        if charges_query_variant_budget:
            path_key = (hostname, parsed.path)
            if parsed.query not in self._query_variants[path_key]:
                if len(self._query_variants[path_key]) >= self.options.max_query_variants_per_path:
                    self.stats["frontier/reject/query_variant_limit"] += 1
                    self.stats[f"frontier/host/{hostname}/reject/query_variant_limit"] += 1
                    return FrontierDecision("reject", canonical, "query_variant_limit", 0)

        # Assign priority: explicit/robots=1000, sitemap=800, content=500, navigation=100
        if candidate.explicit or candidate.source_mode == "exact" or candidate.purpose == "robots":
            priority = 1000
        elif candidate.purpose == "sitemap" or candidate.discovery_source == "sitemap":
            priority = 800
        elif candidate.purpose == "asset" or role == "content":
            priority = 500
        else:
            priority = 100

        # Atomically add the canonical URL to all counters, query sets, and stats
        self._scheduled_urls.add(canonical)
        self._total_scheduled += 1
        self._host_scheduled[hostname] += 1
        self.stats["frontier/scheduled"] += 1
        self.stats[f"frontier/host/{hostname}/scheduled"] += 1

        if charges_query_variant_budget:
            path_key = (hostname, parsed.path)
            self._query_variants[path_key].add(parsed.query)

        return FrontierDecision("schedule", canonical, None, priority)

    def restore(self, records: Iterable[Mapping[str, object]]) -> None:
        for record in records:
            if record.get("frontier_action") != "scheduled":
                continue
            url = str(record.get("url") or "")
            if not url or url in self._scheduled_urls:
                continue
            canonical, hostname, _ = canonicalize_url(url)
            if not canonical or not hostname:
                continue
            if canonical in self._scheduled_urls:
                continue
            self._scheduled_urls.add(canonical)
            self._total_scheduled += 1
            self._host_scheduled[hostname] += 1
            parsed = urlsplit(canonical)
            disc_src = record.get("discovery_source")
            if (
                disc_src == "html_link"
                and record.get("link_kind") not in {"content", "pagination"}
                and self.page_role(canonical) == "navigation"
                and parsed.query
            ):
                path_key = (hostname, parsed.path)
                self._query_variants[path_key].add(parsed.query)

    def snapshot(self) -> dict[str, object]:
        return {
            "total_scheduled": self._total_scheduled,
            "host_scheduled": dict(self._host_scheduled),
            "closed_pagination_branches": len(self._closed_branches),
            "stats": dict(self.stats),
            "route_families": {
                "total": len(self._route_families),
                "closed": sum(state.closed for state in self._route_families.values()),
                "closure_reasons": dict(
                    sorted(
                        Counter(
                            state.closure_reason
                            for state in self._route_families.values()
                            if state.closure_reason
                        ).items()
                    )
                ),
            },
        }
