from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from scrapy import Request, signals


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    url: str
    phase: str
    hostname: str
    source: str
    parent_url: str | None = None
    target_kind: str = "page"
    mechanism: str = "http"
    status: int | None = None
    mime: str | None = None
    reason: str | None = None
    bytes: int = 0
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    status: str
    totals: dict[str, int]
    by_host: dict[str, dict[str, int]]
    by_source: dict[str, dict[str, int]]
    by_mechanism: dict[str, dict[str, int]]
    gaps: list[dict[str, object]]
    gap_reasons: dict[str, int]
    response_statuses: dict[str, int]
    mime_counts: dict[str, int]
    bytes_archived: int
    retries: int

    @classmethod
    def from_events(
        cls,
        status: str,
        events: list[LifecycleEvent],
        gaps: list[dict[str, object]],
        pending_nonterminal: bool = False,
    ) -> "CoverageSummary":
        latest = CoverageLedger.resolve(events)
        if pending_nonterminal:
            latest = {
                url: replace(event, phase="pending")
                if event.phase not in CoverageLedger.TERMINAL and event.phase != "discovered"
                else event
                for url, event in latest.items()
            }
        totals = Counter(event.phase for event in latest.values())
        by_host: dict[str, Counter[str]] = defaultdict(Counter)
        by_source: dict[str, Counter[str]] = defaultdict(Counter)
        by_mechanism: dict[str, Counter[str]] = defaultdict(Counter)
        response_statuses: Counter[str] = Counter()
        mime_counts: Counter[str] = Counter()
        bytes_archived = 0
        retries = sum(max(event.attempts - 1, 0) for event in events)
        for event in latest.values():
            by_host[event.hostname][event.phase] += 1
            by_source[event.source][event.phase] += 1
            by_mechanism[event.mechanism][event.phase] += 1
            if event.status is not None:
                response_statuses[str(event.status)] += 1
            if event.mime:
                mime_counts[event.mime] += 1
            if event.phase == "archived":
                bytes_archived += event.bytes
        gap_reasons = Counter(
            str(gap.get("reason", "unknown")) for gap in gaps
        )
        return cls(
            status=status,
            totals=dict(totals),
            by_host={host: dict(values) for host, values in by_host.items()},
            by_source={source: dict(values) for source, values in by_source.items()},
            by_mechanism={mechanism: dict(values) for mechanism, values in by_mechanism.items()},
            gaps=gaps,
            gap_reasons=dict(gap_reasons),
            response_statuses=dict(response_statuses),
            mime_counts=dict(mime_counts),
            bytes_archived=bytes_archived,
            retries=retries,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "totals": self.totals,
            "by_host": self.by_host,
            "by_source": self.by_source,
            "by_mechanism": self.by_mechanism,
            "gaps": self.gaps,
            "gap_reasons": self.gap_reasons,
            "response_statuses": self.response_statuses,
            "mime_counts": self.mime_counts,
            "bytes_archived": self.bytes_archived,
            "retries": self.retries,
        }


class CoverageLedger:
    TERMINAL = frozenset(
        {
            "archived", "rejected", "login_required", "captcha_blocked", "oversize",
            "failed", "deduplicated", "redirected", "fallback", "budget_rejected",
        }
    )
    GAP_TERMINALS = frozenset({"login_required", "captcha_blocked", "oversize", "failed"})
    EXPECTED_REJECTIONS = frozenset(
        {
            "host_out_of_scope",
            "media_parent_out_of_scope",
            "unsupported_scheme",
            "credentials_or_missing_host",
            "invalid_port",
            "non_default_port",
            "logout_path",
            "repeated_path_trap",
            "too_many_query_pairs",
            "search_trap",
            "calendar_trap",
            "page_limit",
            "optional_seed_absent",
            "unsupported_mime",
            "request_budget",
        }
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[LifecycleEvent] = []

    @classmethod
    def load(cls, path: Path) -> "CoverageLedger":
        ledger = cls(path)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ledger.events.append(LifecycleEvent(**json.loads(line)))
        return ledger

    def append(self, event: LifecycleEvent) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            json.dump(asdict(event), stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(event)

    @classmethod
    def resolve(cls, events: list[LifecycleEvent]) -> dict[str, LifecycleEvent]:
        rank = {
            "archived": 4,
            "deduplicated": 3,
            "redirected": 3,
            "fallback": 3,
            "budget_rejected": 3,
            "rejected": 3,
            "login_required": 2,
            "captcha_blocked": 2,
            "oversize": 2,
            "failed": 2,
        }
        resolved: dict[str, LifecycleEvent] = {}
        for event in events:
            current = resolved.get(event.url)
            if current is None or rank.get(event.phase, 1) >= rank.get(current.phase, 1):
                resolved[event.url] = event
        return resolved

    def summarize(
        self,
        configured_hosts: frozenset[str],
        archive_valid: bool,
        *,
        interrupted: bool = False,
        internal_error: bool = False,
    ) -> CoverageSummary:
        latest = self.resolve(self.events)
        gaps: list[dict[str, object]] = []
        for event in latest.values():
            if (
                event.phase not in self.TERMINAL
                and event.phase != "discovered"
                and not interrupted
            ):
                gaps.append({"url": event.url, "reason": "missing_terminal_outcome"})
            elif event.phase in self.GAP_TERMINALS:
                gaps.append({"url": event.url, "reason": event.reason or event.phase})
            elif event.phase == "rejected" and event.reason not in self.EXPECTED_REJECTIONS:
                gaps.append({"url": event.url, "reason": event.reason or "rejected"})
        attempted_roots = {
            event.hostname
            for event in self.events
            if event.source == "root" and event.phase in self.TERMINAL
        }
        if not interrupted:
            gaps.extend(
            {"hostname": host, "reason": "root_missing_terminal_outcome"}
            for host in sorted(configured_hosts - attempted_roots)
            )
        if not archive_valid:
            gaps.append({"reason": "archive_invalid"})
        if internal_error or not archive_valid:
            status = "failed"
        elif interrupted:
            status = "interrupted"
        elif gaps:
            status = "complete_with_gaps"
        else:
            status = "complete"
        return CoverageSummary.from_events(status, self.events, gaps, pending_nonterminal=interrupted)


class LifecycleTracker:
    def __init__(self, path: Path) -> None:
        self.coverage = CoverageLedger(path)

    @classmethod
    def from_crawler(cls, crawler):
        from .run_store import RunPaths

        paths = RunPaths.for_run(
            Path(crawler.settings.get("CRAWL_DATA_DIR", "data")),
            crawler.settings.get("CRAWL_RUN_ID", "manual"),
        )
        tracker = cls(paths.lifecycle)
        crawler.signals.connect(
            tracker.request_reached_downloader,
            signal=signals.request_reached_downloader,
        )
        crawler.signals.connect(tracker.request_dropped, signal=signals.request_dropped)
        crawler.signals.connect(
            tracker.response_downloaded,
            signal=signals.response_downloaded,
        )
        return tracker

    @staticmethod
    def _event(request: Request, phase: str, *, reason: str | None = None) -> LifecycleEvent:
        return LifecycleEvent(
            url=request.url,
            phase=phase,
            hostname=(urlsplit(request.url).hostname or "unknown").lower().rstrip("."),
            source=request.meta.get("discovery_source", "unknown"),
            parent_url=request.meta.get("parent_url"),
            target_kind=request.meta.get("target_kind", "page"),
            mechanism="playwright" if request.meta.get("playwright") else "http",
            reason=reason,
            attempts=int(request.meta.get("network_attempt", 0)),
        )

    def request_reached_downloader(self, request: Request, spider=None) -> None:
        self.coverage.append(self._event(request, "scheduled"))

    def request_dropped(self, request: Request, spider=None) -> None:
        self.coverage.append(self._event(request, "deduplicated"))

    def response_downloaded(self, response, request: Request, spider=None) -> None:
        if 300 <= response.status < 400 and response.headers.get(b"Location"):
            location = response.headers[b"Location"].decode("latin-1")
            self.coverage.append(
                self._event(request, "redirected", reason=urljoin(request.url, location))
            )
