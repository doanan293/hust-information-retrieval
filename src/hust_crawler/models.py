from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UrlDecision:
    accepted: bool
    canonical_url: str | None
    reason: str | None
    target_kind: str = "page"
    leaf: bool = False


@dataclass(frozen=True, slots=True)
class AccessDecision:
    outcome: str
    escalate_playwright: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RejectedUrl:
    source_url: str
    discovered_url: str
    reason: str


@dataclass(frozen=True, slots=True)
class FetchError:
    url: str
    error_type: str
    message: str
    parent_url: str | None = None
    discovery_source: str = "unknown"
    attempts: int = 1
    http_fallback_attempted: bool = False
    playwright_attempted: bool = False


@dataclass(frozen=True, slots=True)
class Capture:
    url: str
    captured_at: datetime
    status: int
    reason: str
    headers: tuple[tuple[str, str], ...]
    payload: bytes
    mime: str
    discovery_source: str = "unknown"
    parent_url: str | None = None
    target_kind: str = "page"
    fetch_mechanism: str = "http"


@dataclass(frozen=True, slots=True)
class ArchiveRef:
    filename: str
    offset: int
    length: int
    record_id: str
    payload_digest: str
    record_type: str
