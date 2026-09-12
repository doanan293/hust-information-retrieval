from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any, AsyncIterator, Iterable, Iterator, Mapping, cast
from urllib.parse import urlsplit

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response

from hust_crawler.config import CrawlerConfig
from hust_crawler.policies.access import classify_page_gate
from .article import analyze_article
from .assets import (
    AssetPolicy,
    classify_asset,
    is_recognizable_binary,
    recognizable_asset,
)
from .content import normalize_url, resource_kind
from .frontier import Candidate, DiscoverySource, FrontierDecision, FrontierPolicy, Purpose
from .link_policy import ClassifiedLink, classify_link, select_semantic_next
from .options import CrawlOptions
from .routing import route_response
from .seeds import SeedSet
from .sitemaps import SitemapDiscoveryCoordinator, SitemapParseError, parse_sitemap
from .state import FamilyObservation
from .writer import CrawlWriter, StorageLimit


class UnifiedSpider(scrapy.Spider):
    name = "hust_crawl"

    def __init__(
        self,
        *,
        seeds: SeedSet,
        config: CrawlerConfig,
        options: CrawlOptions,
        frontier: FrontierPolicy,
        writer: CrawlWriter,
        policy: AssetPolicy,
        coordinator: SitemapDiscoveryCoordinator,
        resume_records: Iterable[Mapping[str, object]] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.seeds = seeds
        self.config = config
        self.options = options
        self.frontier = frontier
        self.writer = writer
        self.policy = policy
        self.coordinator = coordinator
        self.resume_records = tuple(resume_records)

        exact_hosts = frozenset(
            (urlsplit(u).hostname or "").lower().rstrip(".")
            for u in seeds.exact_urls
            if urlsplit(u).hostname
        ) - seeds.recursive_hostnames
        for eh in exact_hosts:
            self.coordinator.register_exact_host(eh)

    def _create_request_for_scheduled(
        self, candidate: Candidate, decision: FrontierDecision
    ) -> scrapy.Request | None:
        canonical = decision.canonical_url
        assert canonical is not None

        # Skip completed URLs on resume
        if hasattr(self.writer, "state") and hasattr(self.writer.state, "is_complete"):
            if self.writer.state.is_complete(canonical):
                return None

        # Persist scheduled decision
        self.writer.record_scheduled(
            {
                "url": canonical,
                "status": "scheduled",
                "seed_type": candidate.source_mode,
                "discovered_from": candidate.discovered_from,
                "frontier_action": "scheduled",
                "discovery_source": candidate.discovery_source,
            "response_purpose": candidate.purpose,
            "explicit": candidate.explicit,
            "link_kind": candidate.link_kind,
            "family_key": candidate.family_key,
            "ordinal": candidate.ordinal,
            "asset_role": candidate.asset_role,
            "asset_group": candidate.asset_group,
            "priority": decision.priority,
        }
        )

        req_host = (urlsplit(canonical).hostname or "").lower().rstrip(".")

        # Call coordinator.record_html_target for scheduled page candidates
        if candidate.purpose == "page":
            self.coordinator.record_html_target(req_host, canonical)

        meta: dict[str, Any] = {
            "allowed_hostnames": frozenset({req_host}),
            "input_url": canonical,
            "seed_mode": candidate.source_mode,
            "explicit": candidate.explicit,
            "response_purpose": candidate.purpose,
            "discovery_source": candidate.discovery_source,
            "discovered_from": candidate.discovered_from,
            "handle_httpstatus_all": True,
            "request_scope": "asset" if candidate.purpose == "asset" else "page",
            "asset_mode": self.options.assets,
            "link_kind": candidate.link_kind,
            "family_key": candidate.family_key,
            "ordinal": candidate.ordinal,
        }
        if candidate.purpose == "asset":
            meta.update(
                {
                    "asset_group": candidate.asset_group or recognizable_asset(canonical) or "document",
                    "asset_role": candidate.asset_role,
                    "referring_page": candidate.discovered_from,
                }
            )

        return scrapy.Request(
            canonical,
            callback=self.parse_response,
            errback=self.on_request_error,
            priority=decision.priority,
            meta=meta,
            dont_filter=True,
        )

    def _candidate_request(self, candidate: Candidate) -> scrapy.Request | None:
        if candidate.purpose in {"robots", "sitemap"}:
            decision = self.frontier.consider(candidate)
            if decision.action == "reject":
                if decision.canonical_url:
                    self.writer.record_skipped(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "skipped",
                            "reason": decision.reason,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": candidate.purpose,
                        }
                    )
                return None
            return self._create_request_for_scheduled(candidate, decision)

        is_binary = is_recognizable_binary(candidate.url)
        is_asset = candidate.purpose == "asset" or is_binary

        if is_asset:
            if self.options.assets == "content-only":
                decision = self.frontier.consider(candidate)
                if decision.canonical_url and decision.action in {"schedule", "record_only"}:
                    self.writer.record_discovered(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "discovered_not_downloaded",
                            "seed_type": candidate.source_mode,
                            "discovered_from": candidate.discovered_from,
                            "content_type": None,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": "asset",
                            "asset_role": candidate.asset_role,
                        }
                    )
                elif decision.action == "reject" and decision.canonical_url:
                    self.writer.record_skipped(
                        {
                            "url": decision.canonical_url,
                            "final_url": decision.canonical_url,
                            "status": "skipped",
                            "reason": decision.reason,
                            "discovery_source": candidate.discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                return None
            else:
                if is_binary and recognizable_asset(candidate.url) is None and candidate.purpose != "asset":
                    decision = self.frontier.consider(candidate)
                    if decision.canonical_url:
                        self.writer.record_skipped(
                            {
                                "url": decision.canonical_url,
                                "final_url": decision.canonical_url,
                                "status": "skipped",
                                "reason": "unsupported_asset_type",
                                "discovery_source": candidate.discovery_source,
                                "response_purpose": "asset",
                            }
                        )
                    return None

        decision = self.frontier.consider(candidate)
        if decision.action == "reject":
            if decision.canonical_url:
                self.writer.record_skipped(
                    {
                        "url": decision.canonical_url,
                        "final_url": decision.canonical_url,
                        "status": "skipped",
                        "reason": decision.reason,
                        "discovery_source": candidate.discovery_source,
                        "response_purpose": candidate.purpose,
                    }
                )
            return None

        if decision.action == "record_only":
            return None

        return self._create_request_for_scheduled(candidate, decision)

    def start_requests(self) -> Iterator[scrapy.Request]:
        for record in self.resume_records:
            url = str(record["url"])
            purpose_value = str(record.get("response_purpose") or "page")
            purpose = cast(
                Purpose,
                purpose_value if purpose_value in {"robots", "sitemap", "page", "asset"} else "page",
            )
            source_value = str(record.get("discovery_source") or "html_link")
            discovery_source = cast(
                DiscoverySource,
                source_value if source_value in {"seed", "sitemap", "html_link"} else "html_link",
            )
            candidate = Candidate(
                url=url,
                source_mode="exact" if record.get("seed_type") == "exact" else "recursive",
                discovered_from=(
                    str(record["discovered_from"])
                    if record.get("discovered_from") is not None
                    else None
                ),
                kind=resource_kind(url),
                purpose=purpose,
                explicit=bool(record.get("explicit", False)),
                discovery_source=discovery_source,
                link_kind=cast(Any, record.get("link_kind")),
                family_key=(
                    str(record["family_key"])
                    if record.get("family_key") is not None
                    else None
                ),
                ordinal=cast(int | None, record.get("ordinal")),
                asset_role=cast(Any, record.get("asset_role")),
                asset_group=cast(Any, record.get("asset_group")),
            )
            decision = FrontierDecision(
                "schedule", url, None, int(record.get("priority") or 0)
            )
            request = self._create_request_for_scheduled(candidate, decision)
            if request is not None:
                yield request

        # Recursive hostname seeds (schedule robots, 3 sitemaps, and homepage)
        for hostname in sorted(self.seeds.recursive_hostnames):
            robots_cand = Candidate(
                f"https://{hostname}/robots.txt",
                "recursive",
                None,
                "file",
                "robots",
                explicit=True,
                discovery_source="seed",
            )
            req = self._candidate_request(robots_cand)
            if req is not None:
                yield req

            for sm_name in ("sitemap.xml", "sitemap_index.xml", "sitemap-index.xml"):
                sm_url = f"https://{hostname}/{sm_name}"
                self.coordinator.register_sitemap(hostname, sm_url, required=False)
                sm_cand = Candidate(
                    sm_url,
                    "recursive",
                    None,
                    "file",
                    "sitemap",
                    explicit=True,
                    discovery_source="sitemap",
                )
                req = self._candidate_request(sm_cand)
                if req is not None:
                    yield req

            root_cand = Candidate(
                f"https://{hostname}/",
                "recursive",
                None,
                "html",
                "page",
                explicit=False,
                discovery_source="seed",
            )
            req = self._candidate_request(root_cand)
            if req is not None:
                yield req

        # Exact URL seeds
        for url in self.seeds.exact_urls:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            mode = "recursive" if host in self.seeds.recursive_hostnames else "exact"
            kind = resource_kind(url)
            group = recognizable_asset(url)
            if group is not None or is_recognizable_binary(url):
                role_map = {
                    "image": "inline_image",
                    "media": "media_reference",
                    "document": "document_attachment",
                }
                cand = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    kind=kind,
                    purpose="asset",
                    explicit=True,
                    discovery_source="seed",
                    asset_role=role_map.get(group, "document_attachment") if group else "document_attachment",
                    asset_group=group,
                )
            else:
                cand = Candidate(
                    url=url,
                    source_mode=mode,
                    discovered_from=None,
                    kind=kind,
                    purpose="page",
                    explicit=True,
                    discovery_source="seed",
                )
            req = self._candidate_request(cand)
            if req is not None:
                yield req

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for req in self.start_requests():
            yield req

    def _expansion_gate_reason(
        self, response: Response, stored_article: dict[str, Any]
    ) -> str | None:
        if stored_article.get("duplicate_of"):
            return "duplicate"

        link_kind = response.meta.get("link_kind")
        if link_kind in {"search_filter", "calendar_archive", "action_auth"}:
            return link_kind

        encoding = getattr(response, "encoding", None) or "utf-8"
        if hasattr(response, "text"):
            html = response.text
        else:
            html = response.body.decode(encoding, errors="replace")

        gate = classify_page_gate(
            status=response.status,
            url=response.url,
            html=html,
            text=str(stored_article.get("text") or ""),
        )
        if not gate.expandable:
            return gate.reason

        return None

    def parse_response(self, response: Response) -> Iterator[scrapy.Request]:
        input_url = response.meta.get("input_url", response.url)
        purpose = response.meta.get("response_purpose", "page")
        seed_mode = response.meta.get("seed_mode", "exact")
        request_scope = response.meta.get("request_scope", "page")
        discovery_source = response.meta.get("discovery_source", "seed")
        asset_role = response.meta.get("asset_role")
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1", errors="replace")
        route = route_response(
            url=response.url, status=response.status, content_type=content_type, purpose=purpose
        )

        # 1. Header guard stop
        if "download_stopped" in getattr(response, "flags", []) or response.meta.get("header_stop_reason"):
            self.writer.record_discovered(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "discovered_not_downloaded",
                    "seed_type": seed_mode,
                    "discovered_from": response.meta.get("referring_page"),
                    "content_type": content_type or None,
                    "discovery_source": discovery_source,
                    "response_purpose": "asset" if request_scope == "asset" else purpose,
                    "asset_role": asset_role,
                }
            )
            self.writer.checkpoint(input_url)
            return

        # 2. Terminal asset response
        if request_scope == "asset":
            if response.status >= 400:
                self.writer.write_error(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "error": route.error or f"http_{response.status}",
                    }
                )
                self.writer.checkpoint(input_url)
                return

            decision = self.policy.validate_response(content_type, response.url)
            if decision.persist:
                try:
                    self.writer.write_file(
                        input_url,
                        response.url,
                        content_type,
                        response.body,
                        asset_group=decision.group or response.meta.get("asset_group", "document"),
                        asset_role=asset_role or "document_attachment",
                        referring_page=response.meta.get("referring_page"),
                    )
                except StorageLimit as exc:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": response.url,
                            "status": "skipped",
                            "reason": exc.reason,
                            "asset_role": asset_role,
                            "discovery_source": discovery_source,
                            "response_purpose": "asset",
                        }
                    )
            else:
                self.writer.record_skipped(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "status": "skipped",
                        "reason": decision.reason or "unsupported_asset_type",
                        "asset_role": asset_role,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                    }
                )
            self.writer.checkpoint(input_url)
            return

        # 3. Ambiguous page candidate returning non-HTML
        mime = content_type.partition(";")[0].strip().lower()
        if purpose == "page" and mime and mime not in {"text/html", "application/xhtml+xml"}:
            if self.options.assets == "content-only":
                self.writer.record_discovered(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "status": "discovered_not_downloaded",
                        "seed_type": seed_mode,
                        "discovered_from": response.meta.get("referring_page"),
                        "content_type": content_type or None,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                        "asset_role": asset_role,
                    }
                )
                self.writer.checkpoint(input_url)
                return
            else:
                val_decision = self.policy.validate_response(content_type, response.url)
                if val_decision.persist:
                    group = val_decision.group or recognizable_asset(response.url, content_type) or "document"
                    role_map = {
                        "image": "inline_image",
                        "media": "media_reference",
                        "document": "document_attachment",
                    }
                    derived_role = role_map.get(group, "document_attachment")
                    try:
                        self.writer.write_file(
                            input_url,
                            response.url,
                            content_type,
                            response.body,
                            asset_group=group,
                            asset_role=derived_role,
                            referring_page=response.meta.get("referring_page"),
                        )
                    except StorageLimit as exc:
                        self.writer.record_skipped(
                            {
                                "url": input_url,
                                "final_url": response.url,
                                "status": "skipped",
                                "reason": exc.reason,
                                "asset_role": derived_role,
                                "discovery_source": discovery_source,
                                "response_purpose": "asset",
                            }
                        )
                    self.writer.checkpoint(input_url)
                    return
                else:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": response.url,
                            "status": "skipped",
                            "reason": val_decision.reason or "unsupported_asset_type",
                            "discovery_source": discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                    self.writer.checkpoint(input_url)
                    return

        if route.action == "html":
            analysis = analyze_article(
                response.body, response.url, getattr(response, "encoding", None)
            )
            article_data = {
                "url": input_url,
                "final_url": response.url,
                **analysis.article,
                "page_role": self.frontier.page_role(
                    response.url, cast(Any, response.meta.get("link_kind"))
                ),
                "status": response.status,
                "content_type": content_type,
            }
            if hasattr(self.writer, "write_article"):
                stored_article = self.writer.write_article(article_data)
            elif hasattr(self.writer, "put_article"):
                self.writer.put_article(article_data)
                stored_article = article_data
            else:
                stored_article = article_data

            gate_reason = self._expansion_gate_reason(response, stored_article)
            if gate_reason is not None:
                if (
                    hasattr(self, "crawler")
                    and self.crawler
                    and hasattr(self.crawler, "stats")
                    and self.crawler.stats
                ):
                    self.crawler.stats.inc_value(f"frontier/terminal/{gate_reason}")
                self.writer.checkpoint(input_url)
                return

            for asset in analysis.assets:
                if hasattr(self.writer, "state") and hasattr(self.writer.state, "add_asset_referrer"):
                    self.writer.state.add_asset_referrer(asset.url, response.url, asset.role)
                cand = Candidate(
                    url=asset.url,
                    source_mode=seed_mode,
                    discovered_from=response.url,
                    kind="file",
                    purpose="asset",
                    explicit=False,
                    discovery_source="html_link",
                    asset_role=asset.role,
                    asset_group=classify_asset(url=asset.url, asset_role=asset.role),
                )
                req = self._candidate_request(cand)
                if req is not None:
                    yield req

            if seed_mode == "recursive":
                cl_self = classify_link(response.url, source_url=response.url)
                current_family_key = response.meta.get("family_key") or cl_self.family_key
                current_ordinal = response.meta.get("ordinal") or cl_self.ordinal

                classified_discoveries: list[ClassifiedLink] = []
                for d in analysis.discoveries:
                    try:
                        cl = classify_link(
                            d["url"],
                            source_url=response.url,
                            text=d.get("text", ""),
                            rel=tuple(d.get("rel", ())),
                            resource_kind_hint=d.get("kind"),
                        )
                        classified_discoveries.append(cl)
                    except Exception:
                        continue

                accepted_content_urls: list[str] = []
                new_scheduled_content_count = 0
                pagination_by_family: dict[str, list[ClassifiedLink]] = defaultdict(list)

                for cl in classified_discoveries:
                    if cl.kind == "asset":
                        continue
                    elif cl.kind in {"search_filter", "calendar_archive", "action_auth"}:
                        self.writer.record_skipped(
                            {
                                "url": cl.url,
                                "final_url": cl.url,
                                "status": "skipped",
                                "reason": cl.kind,
                                "discovery_source": "html_link",
                                "response_purpose": "page",
                            }
                        )
                    elif cl.kind == "content":
                        cand = Candidate(
                            url=cl.url,
                            source_mode="recursive",
                            discovered_from=response.url,
                            kind="html",
                            purpose="page",
                            explicit=False,
                            discovery_source="html_link",
                            link_kind="content",
                        )
                        decision = self.frontier.consider(cand)
                        if decision.action in {"schedule", "record_only"}:
                            accepted_content_urls.append(decision.canonical_url or cl.url)
                        if decision.action == "schedule":
                            if self.frontier.page_role(cand.url, cand.link_kind) == "content":
                                new_scheduled_content_count += 1
                            req = self._create_request_for_scheduled(cand, decision)
                            if req is not None:
                                yield req
                        elif decision.action == "reject" and decision.canonical_url:
                            self.writer.record_skipped(
                                {
                                    "url": decision.canonical_url,
                                    "final_url": decision.canonical_url,
                                    "status": "skipped",
                                    "reason": decision.reason,
                                    "discovery_source": cand.discovery_source,
                                    "response_purpose": cand.purpose,
                                }
                            )
                    elif cl.kind == "pagination":
                        if cl.family_key:
                            pagination_by_family[cl.family_key].append(cl)

                if current_family_key:
                    content_fingerprint = str(stored_article.get("content_id") or "")
                    content_targets = tuple(sorted(set(accepted_content_urls)))
                    target_fingerprint = hashlib.sha256(
                        "\n".join(content_targets).encode("utf-8")
                    ).hexdigest()
                    observation = FamilyObservation(
                        family_key=current_family_key,
                        kind="pagination",
                        ordinal=current_ordinal,
                        content_fingerprint=content_fingerprint,
                        target_fingerprint=target_fingerprint,
                        content_targets=content_targets,
                    )
                    family_state = self.frontier.observe_family(observation)
                    if hasattr(self.writer, "state") and hasattr(self.writer.state, "put_route_family"):
                        self.writer.state.put_route_family(family_state)
                    self.frontier.observe_pagination(response.url, new_scheduled_content_count)

                for f_key, links in pagination_by_family.items():
                    if not self.frontier.family_is_closed(f_key):
                        next_link = select_semantic_next(
                            links,
                            current_ordinal=current_ordinal if f_key == current_family_key else None,
                        )
                        if next_link is not None:
                            cand = Candidate(
                                url=next_link.url,
                                source_mode="recursive",
                                discovered_from=response.url,
                                kind="html",
                                purpose="page",
                                explicit=False,
                                discovery_source="html_link",
                                link_kind=next_link.kind,
                                family_key=next_link.family_key,
                                ordinal=next_link.ordinal,
                            )
                            req = self._candidate_request(cand)
                            if req is not None:
                                yield req

            self.writer.checkpoint(input_url)

        elif route.action == "robots":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if seed_mode == "recursive":
                encoding = getattr(response, "encoding", None) or "utf-8"
                for line in response.body.decode(encoding, errors="replace").splitlines():
                    key, separator, value = line.partition(":")
                    if not separator or key.strip().lower() != "sitemap":
                        continue
                    normalized = normalize_url(value.strip(), response.url)
                    if normalized is None:
                        continue
                    sm_host = (urlsplit(normalized).hostname or "").lower().rstrip(".")
                    if sm_host == hostname:
                        if self.coordinator.register_sitemap(hostname, normalized, required=True):
                            cand = Candidate(
                                normalized,
                                "recursive",
                                response.url,
                                "file",
                                "sitemap",
                                explicit=False,
                                discovery_source="sitemap",
                            )
                            req = self._candidate_request(cand)
                            if req is not None:
                                yield req
            self.coordinator.complete_robots(hostname)
            self.writer.checkpoint(input_url)

        elif route.action == "sitemap":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            try:
                parsed = parse_sitemap(response.body, response.url, route.content_type)
            except SitemapParseError:
                self.coordinator.complete_sitemap(hostname, response.url, result="failed", targets=0)
                self.writer.write_error({"url": input_url, "error": "sitemap_parse_error"})
                self.writer.checkpoint(input_url)
                return

            if parsed.kind == "index":
                for loc in parsed.locations:
                    loc_host = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if loc_host == hostname:
                        if self.coordinator.register_sitemap(hostname, loc, required=False):
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                "sitemap",
                                False,
                                discovery_source="sitemap",
                            )
                            req = self._candidate_request(cand)
                            if req is not None:
                                yield req
                self.coordinator.complete_sitemap(hostname, response.url, result="success", targets=0)
            else:
                valid_targets = 0
                for loc in parsed.locations:
                    cand_host = (urlsplit(loc).hostname or "").lower().rstrip(".")
                    if cand_host == hostname:
                        self.coordinator.record_sitemap_target(hostname, loc)
                        valid_targets += 1
                        is_bin = is_recognizable_binary(loc)
                        group = recognizable_asset(loc)
                        if is_bin or group is not None:
                            role_map = {
                                "image": "inline_image",
                                "media": "media_reference",
                                "document": "document_attachment",
                            }
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                purpose="asset",
                                explicit=False,
                                discovery_source="sitemap",
                                asset_role=role_map.get(group, "document_attachment") if group else "document_attachment",
                                asset_group=group,
                            )
                        else:
                            try:
                                cl = classify_link(loc, source_url=response.url)
                                link_kind = cl.kind
                                family_key = cl.family_key
                                ordinal = cl.ordinal
                            except Exception:
                                link_kind = None
                                family_key = None
                                ordinal = None
                            cand = Candidate(
                                loc,
                                seed_mode,
                                response.url,
                                resource_kind(loc),
                                purpose="page",
                                explicit=False,
                                discovery_source="sitemap",
                                link_kind=link_kind,
                                family_key=family_key,
                                ordinal=ordinal,
                            )
                        req = self._candidate_request(cand)
                        if req is not None:
                            yield req
                self.coordinator.complete_sitemap(
                    hostname, response.url, result="success", targets=valid_targets
                )

            self.writer.checkpoint(input_url)

        elif route.action == "optional_absent":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if purpose == "sitemap":
                self.coordinator.complete_sitemap(hostname, response.url, result="absent", targets=0)
            elif purpose == "robots":
                self.coordinator.complete_robots(hostname)
            self.writer.record_absent(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "absent",
                    "http_status": response.status,
                }
            )
            self.writer.checkpoint(input_url)

        elif route.action == "http_error":
            hostname = (urlsplit(response.url).hostname or "").lower().rstrip(".")
            if purpose == "sitemap":
                self.coordinator.complete_sitemap(hostname, response.url, result="failed", targets=0)
            elif purpose == "robots":
                self.coordinator.complete_robots(hostname)

            # Check for HTTPS homepage fallback
            if (
                response.url.startswith("https://")
                and not response.meta.get("http_fallback")
                and purpose == "page"
            ):
                parsed = urlsplit(response.url)
                if parsed.path in {"", "/"}:
                    http_url = f"http://{parsed.netloc}/"
                    cand = Candidate(
                        http_url,
                        seed_mode,
                        None,
                        "html",
                        "page",
                        explicit=response.meta.get("explicit", False),
                        discovery_source=discovery_source,
                    )
                    req = self._candidate_request(cand)
                    if req is not None:
                        req.meta["http_fallback"] = True
                        yield req

            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "error": route.error or f"http_{response.status}",
                }
            )

        elif route.action in {"file", "transient", "unknown"}:
            self.writer.checkpoint(input_url)

    def on_request_error(self, failure: Any) -> Iterator[scrapy.Request] | None:
        request = failure.request
        input_url = request.meta.get("input_url", request.url)
        seed_mode = request.meta.get("seed_mode", "exact")
        purpose = request.meta.get("response_purpose", "page")
        request_scope = request.meta.get("request_scope", "page")
        discovery_source = request.meta.get("discovery_source", "seed")
        hostname = (urlsplit(request.url).hostname or "").lower().rstrip(".")

        error_str = str(failure.value)
        error_name = getattr(failure.type, "__name__", "")

        # 1. robots_disallowed
        if isinstance(failure.value, IgnoreRequest) and "robots.txt" in error_str.lower():
            self.writer.record_skipped(
                {"url": input_url, "status": "skipped", "reason": "robots_disallowed"}
            )
            self.writer.checkpoint(input_url)
            return None

        # 2. captcha_blocked / login_required
        access_reason = next(
            (
                reason
                for reason in ("captcha_blocked", "login_required")
                if error_str.startswith(f"{reason}:")
            ),
            None,
        )
        if isinstance(failure.value, IgnoreRequest) and access_reason is not None:
            self.writer.record_skipped(
                {"url": input_url, "status": "skipped", "reason": access_reason}
            )
            self.writer.checkpoint(input_url)
            return None

        # 3. Asset-only IgnoreRequest mapping
        if request_scope == "asset" and isinstance(failure.value, IgnoreRequest):
            asset_safety_reasons = {
                "non_public_address",
                "unsupported_scheme",
                "credentials_not_allowed",
                "non_default_port",
                "redirect_without_location",
                "redirect_limit",
                "redirect_loop",
            }
            matched_reason = next((r for r in asset_safety_reasons if r in error_str), None)
            if matched_reason:
                self.writer.record_skipped(
                    {
                        "url": input_url,
                        "final_url": request.url,
                        "status": "skipped",
                        "reason": matched_reason,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                    }
                )
                self.writer.checkpoint(input_url)
                return None

        if request_scope == "asset":
            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": request.url,
                    "error": "request_failed",
                    "message": error_str,
                }
            )
            self.writer.checkpoint(input_url)
            return None

        # 4. Coordinator notification
        if purpose == "sitemap":
            self.coordinator.complete_sitemap(hostname, request.url, result="failed", targets=0)
        elif purpose == "robots":
            self.coordinator.complete_robots(hostname)

        # 5. HTTPS transport error fallback
        fallback_requests: list[scrapy.Request] = []
        if request.url.startswith("https://") and not request.meta.get("http_fallback"):
            parsed = urlsplit(request.url)
            is_homepage = purpose == "page" and parsed.path in {"", "/"}
            is_robots = purpose == "robots" or parsed.path == "/robots.txt"
            is_sitemap = purpose == "sitemap"

            if is_homepage or is_robots or is_sitemap:
                http_url = f"http://{parsed.netloc}{parsed.path}"
                kind = "file" if (is_robots or is_sitemap) else "html"
                cand = Candidate(
                    http_url,
                    seed_mode,
                    None,
                    kind,
                    purpose,
                    explicit=request.meta.get("explicit", False),
                    discovery_source=discovery_source,
                )
                req = self._candidate_request(cand)
                if req is not None:
                    req.meta["http_fallback"] = True
                    fallback_requests.append(req)

        # 6. File size / generic error
        if "MaxSizeExceeded" in error_name or "file_size_limit" in error_str:
            reason = "file_size_limit"
        else:
            reason = "request_failed"
        self.writer.write_error({"url": input_url, "error": reason, "message": error_str})

        if fallback_requests:
            return iter(fallback_requests)
        return None
