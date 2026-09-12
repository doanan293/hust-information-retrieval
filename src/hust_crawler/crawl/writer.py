from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from .assets import AssetGroup, AssetRole
from .options import CrawlOptions
from .state import CrawlState

_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".weba",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


class StorageLimit(Exception):
    def __init__(self, reason: Literal["file_size_limit", "storage_budget"]) -> None:
        super().__init__(reason)
        self.reason = reason


class CrawlWriter:
    def __init__(self, root: Path, state: CrawlState, options: CrawlOptions) -> None:
        self.root = root
        self.state = state
        self.options = options
        self.files_dir = root / "files"
        self._stored_bytes = 0
        if self.files_dir.exists():
            for entry in self.files_dir.iterdir():
                if entry.is_file() and not entry.name.startswith("."):
                    self._stored_bytes += entry.stat().st_size

    @property
    def stored_bytes(self) -> int:
        return self._stored_bytes

    def record_scheduled(self, record: dict[str, object]) -> None:
        self.state.merge_url(record, completed=False)

    def record_discovered(self, record: dict[str, object], *, completed: bool = True) -> None:
        self.state.merge_url(record, completed=completed)

    def record_skipped(self, record: dict[str, object]) -> None:
        self.state.merge_url(record, completed=True)

    def record_absent(self, record: dict[str, object]) -> None:
        self.state.complete_url(record)

    def write_article(self, record: dict[str, object]) -> dict[str, object]:
        text = str(record.get("text") or "").strip()
        content_id = hashlib.sha256(text.encode("utf-8")).hexdigest()
        url = str(record["url"])
        record_copy = dict(record)
        record_copy["content_id"] = content_id

        owner_url = self.state.claim_content(content_id, url)
        if owner_url != url:
            record_copy["duplicate_of"] = owner_url
            record_copy.pop("text", None)

        disposition = {
            "url": url,
            "final_url": record_copy.get("final_url", url),
            "status": "extracted",
            "content_id": content_id,
            "duplicate_of": record_copy.get("duplicate_of"),
            "http_status": record_copy.get("http_status", 200),
            "content_type": record_copy.get("content_type", "text/html"),
        }
        self.state.complete_url(disposition, article=record_copy)
        return record_copy

    def write_file(
        self,
        url: str,
        final_url: str,
        content_type: str,
        body: bytes,
        *,
        asset_group: AssetGroup = "document",
        asset_role: AssetRole = "document_attachment",
        referring_page: str | None = None,
    ) -> dict[str, object]:
        body_len = len(body)
        if body_len > self.options.max_file_bytes:
            raise StorageLimit("file_size_limit")

        digest = hashlib.sha256(body).hexdigest()
        ext = self._resolve_extension(final_url, url, content_type)
        filename = f"{digest}{ext}"

        dest = self.files_dir / filename
        if not dest.exists():
            if self._stored_bytes + body_len > self.options.max_total_file_bytes:
                raise StorageLimit("storage_budget")
            self.files_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.files_dir / f".{digest}.tmp"
            with tmp_path.open("wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, dest)
            self._stored_bytes += body_len

        if referring_page is not None:
            self.state.add_asset_referrer(url, referring_page, asset_role)

        referring_pages = list(self.state.asset_referrers(url))

        cursor = self.state.connection.execute(
            "SELECT payload FROM files WHERE url = ?;", (url,)
        )
        existing_row = cursor.fetchone()
        if existing_row:
            existing_payload = json.loads(existing_row[0])
            first_role = existing_payload.get("asset_role", asset_role)
            first_group = existing_payload.get("asset_group", asset_group)
        else:
            first_role = asset_role
            first_group = asset_group

        file_record = {
            "url": url,
            "final_url": final_url,
            "path": f"files/{filename}",
            "content_type": content_type,
            "sha256": digest,
            "size": body_len,
            "asset_group": first_group,
            "asset_role": first_role,
            "referring_pages": referring_pages,
        }
        disposition = {
            "url": url,
            "final_url": final_url,
            "status": "file_saved",
            "content_type": content_type,
            "sha256": digest,
            "asset_group": first_group,
            "asset_role": first_role,
        }
        self.state.complete_url(disposition, file=file_record)
        return file_record

    def _resolve_extension(self, final_url: str, url: str, content_type: str) -> str:
        mime = content_type.partition(";")[0].strip().lower()
        path = urlsplit(final_url or url).path
        suffix = PurePosixPath(path).suffix.lower()
        if suffix and len(suffix) <= 5:
            return suffix
        if mime in _MIME_TO_EXT:
            return _MIME_TO_EXT[mime]
        guessed = mimetypes.guess_extension(mime)
        return guessed or ""

    def write_error(self, record: dict[str, object]) -> None:
        url = str(record["url"])
        disposition = {
            "url": url,
            "final_url": record.get("final_url", url),
            "status": "failed",
            "reason": record.get("reason", record.get("error", "error")),
        }
        self.state.complete_url(disposition, error=record)

    def checkpoint(self, url: str) -> None:
        self.state.checkpoint(url)

    def publish(self) -> None:
        self.state.export_crawl(self.root)

    def close(self) -> None:
        self.state.close()
