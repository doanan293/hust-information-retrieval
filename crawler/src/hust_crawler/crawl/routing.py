from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .content import resource_kind

ResponsePurpose = Literal["page", "robots", "sitemap", "asset"]
RouteAction = Literal[
    "html",
    "robots",
    "sitemap",
    "file",
    "transient",
    "unknown",
    "optional_absent",
    "http_error",
]


@dataclass(frozen=True, slots=True)
class ResponseRoute:
    action: RouteAction
    kind: str
    status: int
    content_type: str
    error: str | None = None


def route_response(
    *, url: str, status: int, content_type: str, purpose: ResponsePurpose
) -> ResponseRoute:
    normalized_type = content_type.partition(";")[0].strip().lower()
    if status >= 400:
        if purpose in {"robots", "sitemap"} and status in {404, 410}:
            return ResponseRoute("optional_absent", purpose, status, normalized_type)
        return ResponseRoute(
            "http_error", purpose, status, normalized_type, f"http_{status}"
        )
    if purpose == "robots":
        return ResponseRoute("robots", "robots", status, normalized_type)
    if purpose == "sitemap":
        return ResponseRoute("sitemap", "sitemap", status, normalized_type)
    if purpose == "asset":
        kind = resource_kind(url, normalized_type)
        return ResponseRoute("file", kind, status, normalized_type)
    kind = resource_kind(url, normalized_type)
    return ResponseRoute(cast(RouteAction, kind), kind, status, normalized_type)
