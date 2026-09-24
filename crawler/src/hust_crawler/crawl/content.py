from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, TypedDict
from urllib.parse import quote, unquote_plus, urljoin, urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html


_TRACKING_KEYS = {
    "_ga",
    "_gl",
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}
_TRACKING_PREFIXES = ("utm_",)

_HTML_EXTENSIONS = {".asp", ".aspx", ".cfm", ".htm", ".html", ".jsp", ".php"}
_FILE_EXTENSIONS = {
    ".7z",
    ".aac",
    ".avi",
    ".avif",
    ".bmp",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".flac",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ods",
    ".odt",
    ".ogg",
    ".ogv",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".svg",
    ".tar",
    ".tif",
    ".tiff",
    ".tsv",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}
_TRANSIENT_EXTENSIONS = {
    ".css",
    ".eot",
    ".js",
    ".map",
    ".mjs",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
}

_NONCONTENT_TAGS = {
    "aside",
    "button",
    "canvas",
    "dialog",
    "footer",
    "form",
    "header",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}
_BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
_POSITIVE_HINT = re.compile(r"(?:^|[-_\s])(article|content|detail|entry|main|news|post|story)(?:$|[-_\s])", re.I)
_NEGATIVE_HINT = re.compile(r"(?:^|[-_\s])(breadcrumb|comment|footer|header|menu|nav|related|share|sidebar|social)(?:$|[-_\s])", re.I)


def _clean_query(query: str) -> str:
    kept: list[str] = []
    for field in query.split("&"):
        raw_key = field.partition("=")[0]
        key = unquote_plus(raw_key).lower()
        if key in _TRACKING_KEYS or any(key.startswith(prefix) for prefix in _TRACKING_PREFIXES):
            continue
        kept.append(field)
    return "&".join(kept)


def normalize_url(value: str, base_url: str | None = None) -> str | None:
    """Resolve and normalize a web URL without rewriting its meaningful query data."""
    value = value.strip()
    if not value or any(character in value for character in "\x00\r\n"):
        return None
    try:
        resolved = urljoin(base_url, value) if base_url else value
        parts = urlsplit(resolved)
        hostname = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or hostname is None:
        return None
    if parts.username is not None or parts.password is not None:
        return None

    hostname = hostname.lower().rstrip(".")
    if not hostname or any(character.isspace() for character in hostname):
        return None
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        rendered_host = f"{rendered_host}:{port}"

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(_clean_query(parts.query), safe="!$&'()*+,-./:;=?@_%~")
    return urlunsplit((scheme, rendered_host, path, query, ""))


def _parse_html(source: bytes | str, encoding: str | None = None) -> etree._Element:
    if isinstance(source, str):
        source = source.encode("utf-8")
        encoding = "utf-8"
    if not source.strip():
        source = b"<html></html>"
    parser = lxml_html.HTMLParser(encoding=encoding, recover=True)
    return lxml_html.document_fromstring(source, parser=parser)


def _normalized_text(values: list[str] | tuple[str, ...]) -> str:
    return " ".join(" ".join(values).split())


def _document_base(root: etree._Element, url: str) -> str:
    final_url = normalize_url(url) or url
    base_values = root.xpath("//base[@href][1]/@href")
    if base_values:
        return normalize_url(str(base_values[0]), final_url) or final_url
    return final_url


def _element_label(element: etree._Element) -> str:
    visible = _normalized_text(list(element.itertext()))
    if visible:
        return visible
    alt_values = [str(value) for value in element.xpath(".//*[@alt]/@alt")]
    own_alt = element.get("alt")
    if own_alt:
        alt_values.insert(0, own_alt)
    alt = _normalized_text(alt_values)
    if alt:
        return alt
    return _normalized_text([element.get("aria-label", "")]) or _normalized_text(
        [element.get("title", "")]
    )


def _extract_links(root: etree._Element, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in root.xpath("//a[@href]"):
        normalized = normalize_url(anchor.get("href", ""), base_url)
        if normalized is None:
            continue
        text = _element_label(anchor)
        identity = (normalized, text)
        if identity not in seen:
            links.append({"url": normalized, "text": text})
            seen.add(identity)
    return links


def _candidate_score(element: etree._Element) -> int:
    text_length = len(_normalized_text(list(element.itertext())))
    paragraph_bonus = 80 * len(element.xpath(".//p"))
    link_penalty = sum(len(_normalized_text(list(link.itertext()))) for link in element.xpath(".//a"))
    return text_length + paragraph_bonus - link_penalty


def _select_content(root: etree._Element) -> etree._Element:
    semantic = root.xpath("//article | //main")
    if semantic:
        return max(semantic, key=_candidate_score)

    hinted: list[etree._Element] = []
    for element in root.xpath("//*[@id or @class]"):
        hint = f"{element.get('id', '')} {element.get('class', '')}"
        if _POSITIVE_HINT.search(hint) and not _NEGATIVE_HINT.search(hint):
            hinted.append(element)
    if hinted:
        return max(hinted, key=_candidate_score)

    body = root.xpath("//body")
    return body[0] if body else root


def _remove_noncontent(element: etree._Element) -> None:
    for child in list(element.iterdescendants()):
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        hints = f"{child.get('id', '')} {child.get('class', '')}"
        is_hidden = child.get("hidden") is not None or (child.get("aria-hidden") or "").lower() == "true"
        if not tag or tag in _NONCONTENT_TAGS or _NEGATIVE_HINT.search(hints) or is_hidden:
            child.drop_tree()


def _content_text(element: etree._Element) -> str:
    clean = deepcopy(element)
    _remove_noncontent(clean)
    pieces: list[str] = []

    def visit(node: etree._Element) -> None:
        if not isinstance(node.tag, str):
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag in _BLOCK_TAGS:
                pieces.append("\n")
            if child.tail:
                pieces.append(child.tail)

    visit(clean)
    lines = [" ".join(line.split()) for line in "".join(pieces).splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_title(root: etree._Element) -> str:
    for xpath in ("//title", "(//article | //main)//h1", "//h1"):
        for element in root.xpath(xpath):
            title = _normalized_text(list(element.itertext()))
            if title:
                return title
    return ""


class HtmlDiscovery(TypedDict):
    url: str
    text: str
    kind: str
    rel: list[str]


@dataclass(frozen=True, slots=True)
class HtmlAnalysis:
    article: dict[str, Any]
    discoveries: list[HtmlDiscovery]


def analyze_html(html: bytes | str, url: str, encoding: str | None = None) -> HtmlAnalysis:
    """Build DOM once and produce both article extraction and discoveries."""
    from .article import analyze_article

    res = analyze_article(html, url, encoding)
    return HtmlAnalysis(article=res.article, discoveries=res.discoveries)


def extract_html(html: bytes | str, url: str, encoding: str | None = None) -> dict:
    """Extract the primary readable text and every valid HTTP anchor from HTML."""
    return analyze_html(html, url, encoding).article


def resource_kind(url: str, content_type: str = "") -> str:
    """Classify a URL using response MIME when known, otherwise its extension."""
    mime = content_type.partition(";")[0].strip().lower()
    if mime:
        if mime in {"text/html", "application/xhtml+xml"}:
            return "html"
        if mime in {
            "text/css",
            "text/javascript",
            "application/font-woff",
            "application/javascript",
            "application/vnd.ms-fontobject",
            "application/x-javascript",
            "application/x-font-opentype",
            "application/x-font-ttf",
            "application/ecmascript",
            "text/ecmascript",
        } or mime.startswith("font/"):
            return "transient"
        if mime.startswith(("image/", "video/", "audio/")):
            return "file"
        if mime.startswith("application/") or mime.startswith("text/"):
            return "file"
        return "unknown"

    try:
        path = urlsplit(url).path
    except ValueError:
        return "unknown"
    suffix = PurePosixPath(path).suffix.lower()
    if not suffix or suffix in _HTML_EXTENSIONS:
        return "html"
    if suffix in _FILE_EXTENSIONS:
        return "file"
    if suffix in _TRANSIENT_EXTENSIONS:
        return "transient"
    return "unknown"


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate:
            urls.append(candidate.split()[0])
    return urls


def _discoveries_from_root(root: etree._Element, base_url: str) -> list[HtmlDiscovery]:
    discoveries: list[HtmlDiscovery] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()

    def add(
        value: str,
        text: str,
        rel: tuple[str, ...] = (),
        *,
        kind_hint: str | None = None,
    ) -> None:
        normalized = normalize_url(value, base_url)
        if normalized is None:
            return
        kind = kind_hint or resource_kind(normalized)
        if kind == "transient":
            return
        sorted_rel = tuple(sorted(rel))
        identity = (normalized, text, kind, sorted_rel)
        if identity not in seen:
            discoveries.append({
                "url": normalized,
                "text": text,
                "kind": kind,
                "rel": list(sorted_rel),
            })
            seen.add(identity)

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()
        relation = {token.lower() for token in element.get("rel", "").split()}
        as_kind = element.get("as", "").lower()
        if tag == "script" or "stylesheet" in relation or as_kind in {"font", "script", "style"}:
            continue
        text = _element_label(element)
        if tag in {"a", "area", "link"} and element.get("href"):
            add(element.get("href", ""), text, tuple(sorted(relation)))
        if tag in {
            "audio",
            "embed",
            "iframe",
            "img",
            "input",
            "script",
            "source",
            "track",
            "video",
        } and element.get("src"):
            add(element.get("src", ""), text, (), kind_hint="file")
        if tag == "object" and element.get("data"):
            add(element.get("data", ""), text, (), kind_hint="file")
        if tag == "video" and element.get("poster"):
            add(element.get("poster", ""), text, (), kind_hint="file")
        if tag in {"img", "source"} and element.get("srcset"):
            for candidate in _srcset_urls(element.get("srcset", "")):
                add(candidate, text, (), kind_hint="file")
    return discoveries


def discover_html(html: bytes | str, url: str, encoding: str | None = None) -> list[dict]:
    """Discover linked pages and media without applying hostname scope."""
    return analyze_html(html, url, encoding).discoveries
