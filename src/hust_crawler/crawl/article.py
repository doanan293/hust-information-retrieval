from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from .content import (
    _NEGATIVE_HINT,
    _NONCONTENT_TAGS,
    _discoveries_from_root,
    _document_base,
    _extract_links,
    _normalized_text,
    _select_content,
    HtmlDiscovery,
    normalize_url,
)

_ALLOWED_TAGS = {
    "article",
    "section",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "mark",
    "sub",
    "sup",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "figure",
    "figcaption",
    "picture",
    "source",
    "img",
    "hr",
}

_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height", "srcset"},
    "source": {"src", "srcset", "type", "media"},
    "ol": {"start", "type", "reversed"},
    "table": {"summary"},
    "th": {"colspan", "rowspan", "scope", "headers"},
    "td": {"colspan", "rowspan", "headers"},
    "blockquote": {"cite"},
}

_DROP_TAGS = {
    "aside",
    "audio",
    "button",
    "canvas",
    "dialog",
    "embed",
    "footer",
    "form",
    "header",
    "iframe",
    "input",
    "nav",
    "noscript",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
    "video",
}

_XML_INCOMPATIBLE_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


AssetRole = Literal["inline_image", "media_reference", "document_attachment"]


@dataclass(frozen=True, slots=True)
class ArticleAsset:
    url: str
    role: AssetRole
    alt: str = ""
    caption: str = ""
    external: bool = False


@dataclass(frozen=True, slots=True)
class ArticleAnalysis:
    article: dict[str, object]
    discoveries: list[HtmlDiscovery]
    assets: tuple[ArticleAsset, ...]


def _parse_html(source: bytes | str, encoding: str | None = None) -> etree._Element:
    if isinstance(source, str):
        source = source.encode("utf-8")
        encoding = "utf-8"
    if not source.strip():
        source = b"<html></html>"
    parser = lxml_html.HTMLParser(encoding=encoding, recover=True)
    root = lxml_html.document_fromstring(source, parser=parser)
    for element in root.iter():
        if element.text:
            element.text = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", element.text)
        if element.tail:
            element.tail = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", element.tail)
        for key, value in element.attrib.items():
            element.attrib[key] = _XML_INCOMPATIBLE_CHARACTERS.sub(" ", value)
    return root


def _extract_title(root: etree._Element, content_el: etree._Element) -> str:
    # 1. Visible h1 inside selected content container
    for h1 in content_el.xpath(".//h1"):
        text = _normalized_text(list(h1.itertext()))
        if text:
            return text

    # 2. og:title
    for og_title in root.xpath(
        "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='og:title']/@content"
        " | //meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='og:title']/@content"
    ):
        text = " ".join(str(og_title).split())
        if text:
            return text

    # 3. Document <title>
    for title_el in root.xpath("//title"):
        text = _normalized_text(list(title_el.itertext()))
        if text:
            return text

    # 4. Any h1 anywhere in document
    for h1 in root.xpath("//h1"):
        text = _normalized_text(list(h1.itertext()))
        if text:
            return text

    return ""


def _extract_meta_content(root: etree._Element, attribute_name: str, values: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for val in values:
        xpath = f"//meta[translate(@{attribute_name}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='{val.lower()}']/@content"
        for raw in root.xpath(xpath):
            cleaned = " ".join(str(raw).split())
            if cleaned and cleaned not in seen:
                results.append(cleaned)
                seen.add(cleaned)
    return results


def _extract_language(root: etree._Element) -> str | None:
    for lang in root.xpath("//html/@lang | //html/@xml:lang"):
        cleaned = " ".join(str(lang).split())
        if cleaned:
            return cleaned
    meta_langs = _extract_meta_content(root, "http-equiv", ("content-language",))
    if meta_langs:
        return meta_langs[0]
    meta_langs = _extract_meta_content(root, "name", ("content-language", "language"))
    if meta_langs:
        return meta_langs[0]
    return None


def _extract_canonical_url(root: etree._Element, base_url: str, warnings: list[str]) -> str | None:
    raw_canonicals: list[str] = []
    for href in root.xpath("//link[translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='canonical']/@href"):
        resolved = normalize_url(str(href), base_url)
        if resolved and resolved not in raw_canonicals:
            raw_canonicals.append(resolved)
    if len(raw_canonicals) > 1:
        warnings.append("ambiguous_canonical_url")
    return raw_canonicals[0] if raw_canonicals else None


def _extract_author(root: etree._Element) -> str | None:
    authors = _extract_meta_content(root, "name", ("author", "article:author"))
    if not authors:
        authors = _extract_meta_content(root, "property", ("author", "article:author"))
    return authors[0] if authors else None


def _extract_published_at(root: etree._Element, content_el: etree._Element, warnings: list[str]) -> str | None:
    dates: list[str] = []
    seen: set[str] = set()

    def add_date(raw: str) -> None:
        cleaned = " ".join(raw.split())
        if cleaned and cleaned not in seen:
            dates.append(cleaned)
            seen.add(cleaned)

    for val in _extract_meta_content(root, "property", ("article:published_time", "og:published_time")):
        add_date(val)
    for val in _extract_meta_content(root, "name", ("article:published_time", "pubdate", "publishdate")):
        add_date(val)

    for time_el in content_el.xpath(".//time[@datetime]"):
        val = time_el.get("datetime")
        if val:
            add_date(val)

    if not dates:
        for time_el in root.xpath("//time[@datetime]"):
            val = time_el.get("datetime")
            if val:
                add_date(val)

    if len(dates) > 1:
        warnings.append("ambiguous_published_at")
    return dates[0] if dates else None


def _extract_modified_at(root: etree._Element) -> str | None:
    dates = _extract_meta_content(root, "property", ("article:modified_time", "og:updated_time"))
    if not dates:
        dates = _extract_meta_content(root, "name", ("article:modified_time", "last-modified"))
    return dates[0] if dates else None


def _extract_summary(root: etree._Element) -> str | None:
    summaries = _extract_meta_content(root, "name", ("description", "og:description", "twitter:description"))
    if not summaries:
        summaries = _extract_meta_content(root, "property", ("description", "og:description"))
    return summaries[0] if summaries else None


def sanitize_article(element: etree._Element, base_url: str) -> etree._Element:
    clean = deepcopy(element)
    clean.attrib.clear()

    for comment in list(clean.xpath(".//comment()")):
        comment.drop_tree()

    for child in list(clean.iterdescendants()):
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        hints = f"{child.get('id', '')} {child.get('class', '')}"
        is_hidden = child.get("hidden") is not None or (child.get("aria-hidden") or "").lower() == "true"
        if (
            tag in _DROP_TAGS
            or tag in _NONCONTENT_TAGS
            or _NEGATIVE_HINT.search(hints)
            or is_hidden
        ):
            child.drop_tree()

    for child in list(clean.iterdescendants()):
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag not in _ALLOWED_TAGS:
            child.drop_tag()
            continue

        raw_attrs = dict(child.attrib)
        child.attrib.clear()
        allowed = _ALLOWED_ATTRS.get(tag, set())

        for key, val in raw_attrs.items():
            k = key.lower()
            if k.startswith("on") or k in {"style", "class", "id"} or k.startswith("data-") or k not in allowed:
                continue

            if tag == "a" and k == "href":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            elif tag in {"img", "source"} and k == "src":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            elif tag in {"img", "source"} and k == "srcset":
                candidates: list[str] = []
                for item in val.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    parts = item.split(maxsplit=1)
                    res = normalize_url(parts[0], base_url)
                    if res:
                        candidates.append(f"{res} {parts[1]}" if len(parts) > 1 else res)
                if candidates:
                    child.attrib[k] = ", ".join(candidates)
            elif tag == "blockquote" and k == "cite":
                resolved = normalize_url(val, base_url)
                if resolved:
                    child.attrib[k] = resolved
            else:
                child.attrib[k] = val

    return clean


def serialize_content_html(element: etree._Element) -> str:
    parts: list[str] = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    for child in element:
        parts.append(lxml_html.tostring(child, encoding="unicode").strip())
    return "\n".join(p for p in parts if p)


def blocks_from_article(element: etree._Element) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading_stack: dict[int, str] = {}

    def current_heading_path() -> list[str]:
        return [heading_stack[lvl] for lvl in sorted(heading_stack.keys())]

    def walk(node: etree._Element) -> None:
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            text = _normalized_text(list(node.itertext()))
            if text:
                for lvl in list(heading_stack.keys()):
                    if lvl >= level:
                        del heading_stack[lvl]
                heading_stack[level] = text
                blocks.append({
                    "type": "heading",
                    "level": level,
                    "text": text,
                    "heading_path": current_heading_path(),
                })
        elif tag == "p":
            text = _normalized_text(list(node.itertext()))
            if text:
                blocks.append({
                    "type": "paragraph",
                    "text": text,
                    "heading_path": current_heading_path(),
                })
        elif tag in {"ul", "ol"}:
            items: list[str] = []
            for li in node.xpath(".//li"):
                li_copy = deepcopy(li)
                for sublist in li_copy.xpath(".//ul | .//ol"):
                    sublist.drop_tree()
                item_text = _normalized_text(list(li_copy.itertext()))
                if item_text:
                    items.append(item_text)
            if items:
                blocks.append({
                    "type": "list",
                    "ordered": tag == "ol",
                    "items": items,
                    "heading_path": current_heading_path(),
                })
        elif tag == "blockquote":
            text = _normalized_text(list(node.itertext()))
            if text:
                blocks.append({
                    "type": "quote",
                    "text": text,
                    "heading_path": current_heading_path(),
                })
        elif tag == "pre":
            code_el = node.find(".//code")
            target = code_el if code_el is not None else node
            code_text = "".join(target.itertext()).strip("\r\n")
            if code_text:
                blocks.append({
                    "type": "code",
                    "text": code_text,
                    "heading_path": current_heading_path(),
                })
        elif tag == "table":
            headers: list[list[str]] = []
            rows: list[list[str]] = []
            thead_trs = node.xpath(".//thead/tr")
            if thead_trs:
                for tr in thead_trs:
                    row = [_normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if any(row):
                        headers.append(row)
                for tr in node.xpath(".//tbody/tr | ./tr"):
                    row = [_normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if any(row):
                        rows.append(row)
            else:
                for tr in node.xpath(".//tbody/tr | ./tr"):
                    ths = tr.xpath("./th")
                    row = [_normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if not any(row):
                        continue
                    if ths and not headers and not rows:
                        headers.append(row)
                    else:
                        rows.append(row)
            if headers or rows:
                blocks.append({
                    "type": "table",
                    "headers": headers,
                    "rows": rows,
                    "heading_path": current_heading_path(),
                })
        elif tag == "figure":
            img = node.find(".//img")
            caption_el = node.find(".//figcaption")
            asset_url = img.get("src", "") if img is not None else ""
            alt = img.get("alt", "") if img is not None else ""
            caption = _normalized_text(list(caption_el.itertext())) if caption_el is not None else ""
            blocks.append({
                "type": "figure",
                "asset_url": asset_url,
                "alt": alt,
                "caption": caption,
                "heading_path": current_heading_path(),
            })
        elif tag == "img":
            blocks.append({
                "type": "figure",
                "asset_url": node.get("src", ""),
                "alt": node.get("alt", ""),
                "caption": "",
                "heading_path": current_heading_path(),
            })
        elif tag == "hr":
            blocks.append({
                "type": "separator",
                "heading_path": current_heading_path(),
            })
        else:
            if node.text and node.text.strip():
                t = _normalized_text([node.text])
                if t:
                    blocks.append({
                        "type": "paragraph",
                        "text": t,
                        "heading_path": current_heading_path(),
                    })
            for child in node:
                walk(child)
                if child.tail and child.tail.strip():
                    t = _normalized_text([child.tail])
                    if t:
                        blocks.append({
                            "type": "paragraph",
                            "text": t,
                            "heading_path": current_heading_path(),
                        })

    walk(element)
    return blocks


def text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype in {"heading", "paragraph", "quote"}:
            t = block.get("text")
            if t and isinstance(t, str):
                lines.append(t)
        elif btype == "list":
            for item in block.get("items", []):
                if item and isinstance(item, str):
                    lines.append(item)
        elif btype == "code":
            t = block.get("text")
            if t and isinstance(t, str):
                lines.append(t)
        elif btype == "table":
            for row in block.get("headers", []):
                line = " ".join(c for c in row if c)
                if line:
                    lines.append(line)
            for row in block.get("rows", []):
                line = " ".join(c for c in row if c)
                if line:
                    lines.append(line)
        elif btype == "figure":
            cap = block.get("caption") or block.get("alt")
            if cap and isinstance(cap, str):
                lines.append(cap)
    return "\n".join(lines).strip()


def _srcset_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip().split()[0] for part in value.split(",") if part.strip())


def _is_supported_document(value: str, declared_type: str = "") -> bool:
    from .assets import DOCUMENT_EXTENSIONS, DOCUMENT_MIME_TYPES

    mime = declared_type.partition(";")[0].strip().lower()
    if mime in DOCUMENT_MIME_TYPES:
        return True
    try:
        path = urlsplit(value).path
    except ValueError:
        return False
    return PurePosixPath(path).suffix.lower() in DOCUMENT_EXTENSIONS


def _append_asset(
    assets: list[ArticleAsset],
    seen: set[tuple[str, AssetRole]],
    *,
    value: str,
    base_url: str,
    role: AssetRole,
    alt: str = "",
    caption: str = "",
) -> None:
    normalized = normalize_url(value, base_url)
    if normalized is None or (normalized, role) in seen:
        return
    seen.add((normalized, role))
    assets.append(
        ArticleAsset(
            url=normalized,
            role=role,
            alt=alt,
            caption=caption,
            external=(urlsplit(normalized).hostname != urlsplit(base_url).hostname),
        )
    )


def extract_assets(
    element: etree._Element,
    base_url: str,
    warnings: list[str] | None = None,
) -> list[ArticleAsset]:
    assets: list[ArticleAsset] = []
    seen: set[tuple[str, AssetRole]] = set()

    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        tag = node.tag.lower()
        if tag == "img":
            caption = ""
            parent = node.getparent()
            while parent is not None and parent != element:
                if isinstance(parent.tag, str) and parent.tag.lower() == "figure":
                    cap_el = parent.find(".//figcaption")
                    if cap_el is not None:
                        caption = _normalized_text(list(cap_el.itertext()))
                    break
                parent = parent.getparent()
            alt = node.get("alt", "")
            src = node.get("src")
            if src:
                _append_asset(assets, seen, value=src, base_url=base_url, role="inline_image", alt=alt, caption=caption)
            srcset = node.get("srcset")
            if srcset:
                for v in _srcset_values(srcset):
                    _append_asset(assets, seen, value=v, base_url=base_url, role="inline_image", alt=alt, caption=caption)
        elif tag == "source":
            parent = node.getparent()
            parent_tag = parent.tag.lower() if parent is not None and isinstance(parent.tag, str) else ""
            if parent_tag == "picture":
                srcset = node.get("srcset")
                if srcset:
                    for v in _srcset_values(srcset):
                        _append_asset(assets, seen, value=v, base_url=base_url, role="inline_image")
                src = node.get("src")
                if src:
                    _append_asset(assets, seen, value=src, base_url=base_url, role="inline_image")
            elif parent_tag in {"video", "audio"}:
                src = node.get("src")
                if src:
                    _append_asset(assets, seen, value=src, base_url=base_url, role="media_reference")
        elif tag == "video":
            src = node.get("src")
            if src:
                _append_asset(assets, seen, value=src, base_url=base_url, role="media_reference", caption=node.get("title", ""))
            poster = node.get("poster")
            if poster:
                _append_asset(assets, seen, value=poster, base_url=base_url, role="inline_image")
        elif tag == "audio":
            src = node.get("src")
            if src:
                _append_asset(assets, seen, value=src, base_url=base_url, role="media_reference", caption=node.get("title", ""))
        elif tag == "a":
            href = node.get("href")
            if href and _is_supported_document(href, node.get("type", "")):
                text = _normalized_text(list(node.itertext()))
                _append_asset(assets, seen, value=href, base_url=base_url, role="document_attachment", caption=text)
        elif tag == "object":
            data = node.get("data")
            if data and _is_supported_document(data, node.get("type", "")):
                text = _normalized_text(list(node.itertext()))
                _append_asset(assets, seen, value=data, base_url=base_url, role="document_attachment", caption=text)
        elif tag == "embed":
            src = node.get("src")
            if src and _is_supported_document(src, node.get("type", "")):
                _append_asset(assets, seen, value=src, base_url=base_url, role="document_attachment", caption=node.get("title", ""))

    if warnings is not None:
        for asset in assets:
            if asset.external:
                warnings.append(f"external_asset:{asset.url}")

    return assets



def analyze_article(source: bytes | str, url: str, encoding: str | None = None) -> ArticleAnalysis:
    root = _parse_html(source, encoding)
    base_url = _document_base(root, url)
    content_el = _select_content(root)

    title = _extract_title(root, content_el)
    warnings: list[str] = []

    if not title:
        warnings.append("missing_title")

    language = _extract_language(root)
    canonical_url = _extract_canonical_url(root, base_url, warnings)
    author = _extract_author(root)
    published_at = _extract_published_at(root, content_el, warnings)
    modified_at = _extract_modified_at(root)
    summary = _extract_summary(root)

    assets_list = extract_assets(content_el, base_url, warnings)
    assets: tuple[ArticleAsset, ...] = tuple(assets_list)

    sanitized_el = sanitize_article(content_el, base_url)
    content_html = serialize_content_html(sanitized_el)
    content_blocks = blocks_from_article(sanitized_el)
    text = text_from_blocks(content_blocks)

    if not text:
        warnings.append("missing_text")

    headings = [
        {
            "level": b["level"],
            "text": b["text"],
            "heading_path": list(b["heading_path"]),
        }
        for b in content_blocks
        if b["type"] == "heading"
    ]

    article: dict[str, Any] = {
        "title": title,
        "text": text,
        "language": language,
        "canonical_url": canonical_url,
        "author": author,
        "published_at": published_at,
        "modified_at": modified_at,
        "summary": summary,
        "content_html": content_html,
        "headings": headings,
        "content_blocks": content_blocks,
        "assets": [
            {
                "url": a.url,
                "role": a.role,
                "alt": a.alt,
                "caption": a.caption,
                "external": a.external,
            }
            for a in assets
        ],
        "links": _extract_links(root, base_url),
        "warnings": warnings,
    }
    discoveries = _discoveries_from_root(root, base_url)

    return ArticleAnalysis(article=article, discoveries=discoveries, assets=assets)
