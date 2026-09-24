from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final, Iterable, Literal
from urllib.parse import parse_qsl, urlsplit

from .assets import recognizable_asset
from .content import normalize_url

LinkKind = Literal[
    "content",
    "pagination",
    "search_filter",
    "calendar_archive",
    "action_auth",
    "asset",
]

PAGINATION_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"page", "p", "paged", "pg", "offset", "start", "skip"}
)
PRESENTATION_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sort",
        "order",
        "dir",
        "orderby",
        "sortby",
        "filter",
        "category",
        "tag",
        "view",
        "display",
        "lang",
        "tab",
    }
)
SEARCH_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"q", "query", "keyword", "search"}
)
FACET_QUERY_NAMESPACES: Final[frozenset[str]] = frozenset(
    {"f", "filter", "filters", "facet", "facets", "refine", "refinement", "taxonomy"}
)
FACET_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"filter", "filters", "facet", "facets", "refine", "refinement", "taxonomy"}
)
VOLATILE_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"session", "sid", "nonce", "token", "cache", "cb", "timestamp", "fbclid", "gclid"}
)
PAGINATION_LABELS: Final[frozenset[str]] = frozenset(
    {
        "next",
        "next page",
        "newer",
        "newer posts",
        "older",
        "older posts",
        "prev",
        "previous",
        "previous page",
        "sau",
        "tiếp",
        "tiếp theo",
        "trang sau",
        "trang trước",
        "trước",
    }
)

ACTION_AUTH_PATH_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "login",
        "logout",
        "signin",
        "signout",
        "register",
        "signup",
        "auth",
        "cart",
        "checkout",
        "print",
        "share",
    }
)
ACTION_AUTH_TEXT_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "đăng nhập",
        "đăng xuất",
        "đăng ký",
        "login",
        "logout",
        "sign in",
        "sign out",
        "register",
        "sign up",
        "thoát",
    }
)

ARCHIVE_PATH_CONTEXTS: Final[frozenset[str]] = frozenset(
    {"archive", "archives", "calendar", "date", "year", "month"}
)
NAVIGATION_PATH_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "all",
        "archive",
        "archives",
        "browse",
        "categories",
        "category",
        "find",
        "list",
        "listing",
        "search",
        "tag",
        "tags",
    }
)

_PATH_PAGINATION_RE: Final[re.Pattern[str]] = re.compile(
    r"/(?:page|p)/(\d+)(?:/)?$", re.IGNORECASE
)
_CALENDAR_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"/(\d{4})/(\d{1,2})(?:/(\d{1,2}))?(?:/)?$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class ClassifiedLink:
    url: str
    kind: LinkKind
    family_key: str | None
    ordinal: int | None
    rel_next: bool
    reason: str | None


def query_key_kind(key: str) -> Literal["pagination", "filter"] | None:
    """Classify common exact and namespaced navigation query keys."""
    lowered = key.casefold()
    if lowered in PAGINATION_QUERY_KEYS:
        return "pagination"
    if (
        lowered in SEARCH_QUERY_KEYS
        or lowered in PRESENTATION_QUERY_KEYS
        or lowered in FACET_QUERY_KEYS
    ):
        return "filter"

    tokens = re.findall(r"[a-z0-9]+", lowered)
    if len(tokens) > 1 and tokens[0] in FACET_QUERY_NAMESPACES:
        return "filter"
    if len(tokens) > 1 and tokens[-1] in PAGINATION_QUERY_KEYS:
        return "pagination"
    return None


def is_strong_filter_key(key: str) -> bool:
    """Return whether a selector should outrank pagination classification."""
    lowered = key.casefold()
    if lowered in SEARCH_QUERY_KEYS or lowered in FACET_QUERY_KEYS:
        return True
    tokens = re.findall(r"[a-z0-9]+", lowered)
    return len(tokens) > 1 and tokens[0] in FACET_QUERY_NAMESPACES


def is_navigation_path(path: str) -> bool:
    segments = {segment.casefold() for segment in path.split("/") if segment}
    return bool(segments & NAVIGATION_PATH_SEGMENTS)


def _classify_navigation(
    normalized: str, text: str, rel: tuple[str, ...]
) -> tuple[LinkKind, int | None]:
    parts = urlsplit(normalized)
    path = parts.path or "/"
    segments = [s.lower() for s in path.split("/") if s]
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query_dict = {k.lower(): v for k, v in query_pairs}
    rel_tokens = {r.lower() for r in rel}

    # 1. Action / Auth check (path and text tokens)
    if any(token in ACTION_AUTH_PATH_TOKENS for token in segments):
        return "action_auth", None
    if any(token in text for token in ACTION_AUTH_TEXT_TOKENS):
        return "action_auth", None

    # 2. Calendar / Archive check (date pattern under archive context)
    has_archive_context = any(ctx in segments for ctx in ARCHIVE_PATH_CONTEXTS) or any(
        ctx in query_dict for ctx in ARCHIVE_PATH_CONTEXTS
    )
    if has_archive_context and _CALENDAR_DATE_RE.search(path):
        return "calendar_archive", None

    # Namespaced/high-cardinality selectors outrank pagination so a facet
    # page cannot evade the query-variant budget by appending a page key.
    if any(is_strong_filter_key(k) for k in query_dict):
        return "search_filter", None

    # 3. Pagination check
    # Check query keys
    for raw_key, val in query_pairs:
        key = raw_key.casefold()
        if query_key_kind(key) == "pagination":
            if key == "p":
                if "shortlink" in rel_tokens:
                    return "content", None
                label = " ".join(text.split())
                if not ({"next", "prev"} & rel_tokens or label == val or label in PAGINATION_LABELS):
                    continue
            ordinal = int(val) if val.isdigit() else None
            return "pagination", ordinal

    # Check path pagination (/page/123, /p/123)
    path_match = _PATH_PAGINATION_RE.search(path)
    if path_match:
        return "pagination", int(path_match.group(1))

    # Check rel="next"
    if "next" in rel_tokens:
        return "pagination", None

    # 4. Search / Filter check
    if any(query_key_kind(k) == "filter" for k in query_dict):
        return "search_filter", None

    # Generic collection/listing routes are navigation even when their
    # site-specific selector uses an otherwise opaque key such as `value`.
    if is_navigation_path(path):
        return "search_filter", None

    # Default to content
    return "content", None


def route_family_key(url: str, kind: LinkKind) -> str | None:
    if kind in {"content", "asset"}:
        return None

    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower().rstrip(".")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    port = parts.port
    default_port = 443 if parts.scheme == "https" else 80
    if port not in {None, default_port}:
        rendered_host = f"{rendered_host}:{port}"

    path = parts.path or "/"

    if kind == "pagination":
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        has_page_query = False
        new_query_pairs = []
        for k, v in query_pairs:
            k_lower = k.lower()
            if query_key_kind(k_lower) == "pagination":
                has_page_query = True
                new_query_pairs.append((k, "{page}"))
            elif k_lower not in VOLATILE_QUERY_KEYS and not k_lower.startswith("utm_"):
                new_query_pairs.append((k, v))

        if has_page_query:
            query_str = "&".join(f"{k}={v}" for k, v in new_query_pairs)
            return f"{rendered_host}{path}?{query_str}"

        if _PATH_PAGINATION_RE.search(path):
            norm_path = _PATH_PAGINATION_RE.sub(r"/page/{page}", path)
            return f"{rendered_host}{norm_path}"

        return f"{rendered_host}{path}"

    if kind == "calendar_archive":
        norm_path = _CALENDAR_DATE_RE.sub(
            lambda m: "/{year}/{month}" + ("/{day}" if m.group(3) else ""), path
        )
        return f"{rendered_host}{norm_path}"

    return f"{rendered_host}{path}"


def classify_link(
    url: str,
    *,
    source_url: str,
    text: str = "",
    rel: tuple[str, ...] = (),
    resource_kind_hint: str | None = None,
) -> ClassifiedLink:
    normalized = normalize_url(url, source_url)
    if normalized is None:
        raise ValueError("link URL is not normalizable")
    group = recognizable_asset(normalized)
    if resource_kind_hint == "file" or group is not None:
        return ClassifiedLink(normalized, "asset", None, None, False, None)
    kind, ordinal = _classify_navigation(normalized, text.casefold(), rel)
    return ClassifiedLink(
        normalized,
        kind,
        route_family_key(normalized, kind),
        ordinal,
        "next" in {token.casefold() for token in rel},
        None,
    )


def select_semantic_next(
    links: Iterable[ClassifiedLink], *, current_ordinal: int | None
) -> ClassifiedLink | None:
    candidates = [link for link in links if link.kind == "pagination"]
    rel_next = sorted((link for link in candidates if link.rel_next), key=lambda link: link.url)
    if rel_next:
        return rel_next[0]
    higher = sorted(
        (
            link
            for link in candidates
            if link.ordinal is not None
            and (current_ordinal is None or link.ordinal > current_ordinal)
        ),
        key=lambda link: (link.ordinal, link.url),
    )
    return higher[0] if higher else None
