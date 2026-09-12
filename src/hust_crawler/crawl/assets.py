from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, TYPE_CHECKING
from urllib.parse import urlsplit

from .options import CrawlOptions

if TYPE_CHECKING:
    from .article import ArticleAsset

AssetGroup = Literal["image", "media", "document"]
AssetRole = Literal["inline_image", "media_reference", "document_attachment"]
AssetAction = Literal["schedule", "record_only", "reject"]


@dataclass(frozen=True, slots=True)
class AssetDecision:
    action: AssetAction
    group: AssetGroup | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class AssetResponseDecision:
    persist: bool
    group: AssetGroup | None
    reason: str | None


DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
    }
)

DOCUMENT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
    }
)

EXTENSION_GROUPS: dict[str, AssetGroup] = {
    # Images
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "image",
    ".bmp": "image",
    ".avif": "image",
    ".ico": "image",
    ".tiff": "image",
    ".tif": "image",
    # Media
    ".mp4": "media",
    ".webm": "media",
    ".mov": "media",
    ".avi": "media",
    ".mkv": "media",
    ".mp3": "media",
    ".m4a": "media",
    ".flac": "media",
    ".ogg": "media",
    ".aac": "media",
    ".wav": "media",
    ".opus": "media",
    # Documents
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".xls": "document",
    ".xlsx": "document",
    ".ppt": "document",
    ".pptx": "document",
    ".odt": "document",
    ".ods": "document",
    ".odp": "document",
}


def recognizable_asset(url: str, declared_type: str = "") -> AssetGroup | None:
    mime = declared_type.partition(";")[0].strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith(("audio/", "video/")):
        return "media"
    if mime in DOCUMENT_MIME_TYPES:
        return "document"
    try:
        path = urlsplit(url).path if url else ""
    except ValueError:
        path = ""
    return EXTENSION_GROUPS.get(PurePosixPath(path).suffix.lower())


def is_recognizable_binary(url: str) -> bool:
    try:
        path = urlsplit(url).path if url else ""
    except ValueError:
        path = ""
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in EXTENSION_GROUPS or suffix in {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
        ".exe", ".msi", ".dmg", ".iso", ".apk",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
    }


def classify_asset(
    content_type: str = "",
    url: str = "",
    asset_role: str | None = None,
) -> AssetGroup:
    group = recognizable_asset(url, content_type)
    if group is not None:
        return group
    if asset_role == "inline_image":
        return "image"
    if asset_role == "media_reference":
        return "media"
    return "document"


class AssetPolicy:
    def __init__(self, options: CrawlOptions | None = None) -> None:
        self.options = options if options is not None else CrawlOptions()

    def consider(self, asset: ArticleAsset, source_page: str) -> AssetDecision:
        group = recognizable_asset(asset.url)
        if group is None:
            if asset.role == "inline_image":
                group = "image"
            elif asset.role == "media_reference":
                group = "media"
            elif asset.role == "document_attachment":
                group = "document"

        if group is None:
            return AssetDecision("reject", None, "unsupported_asset_type")

        if self.options.assets == "content-only":
            return AssetDecision("record_only", group, "asset_mode_content_only")

        if self.options.assets == "all":
            return AssetDecision("schedule", group, None)

        return AssetDecision("reject", None, "unsupported_asset_mode")

    def validate_response(self, content_type: str, final_url: str) -> AssetResponseDecision:
        if self.options.assets != "all":
            return AssetResponseDecision(False, None, "asset_mode_content_only")

        mime = content_type.partition(";")[0].strip().lower()
        if mime in {"text/html", "application/xhtml+xml"} or mime.startswith("text/html"):
            return AssetResponseDecision(False, None, "unexpected_asset_content")

        if mime.startswith("image/"):
            return AssetResponseDecision(True, "image", None)
        if mime.startswith(("audio/", "video/")):
            return AssetResponseDecision(True, "media", None)
        if mime in DOCUMENT_MIME_TYPES:
            return AssetResponseDecision(True, "document", None)

        if mime in {"application/octet-stream", "binary/octet-stream"}:
            try:
                path = urlsplit(final_url).path if final_url else ""
            except ValueError:
                path = ""
            suffix = PurePosixPath(path).suffix.lower()
            group = EXTENSION_GROUPS.get(suffix)
            if group is not None:
                return AssetResponseDecision(True, group, None)
            return AssetResponseDecision(False, None, "unsupported_asset_type")

        return AssetResponseDecision(False, None, "unsupported_asset_type")
