from __future__ import annotations

import hashlib
import os
from datetime import timezone
from pathlib import Path
from urllib.parse import urlsplit

from .cdxj import CdxjEntry, load_latest
from .coverage import CoverageLedger, LifecycleEvent
from .models import ArchiveRef, Capture, FetchError, RejectedUrl
from .run_store import RunPaths, RunStore, StorageCapacityError, has_capacity
from .warc_store import WarcStore


_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf"}
_DOCUMENT_MIMES = {"application/pdf", "application/msword", "application/rtf", "text/rtf", "application/vnd.ms-word", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet", "application/vnd.oasis.opendocument.presentation"}


_DEFAULT_MIME_TYPES = frozenset(
    {
        "text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml",
        "application/pdf", "application/msword", "application/rtf", "text/rtf",
        "application/vnd.ms-word", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
    }
)
_DEFAULT_MIME_PREFIXES = ("image/", "video/")
_DEFAULT_EXTENSIONS = frozenset(_DOCUMENT_EXTENSIONS | {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".webm", ".mov"})


def is_supported_content(
    content_type: str,
    url: str,
    accepted_mime_types: frozenset[str] = _DEFAULT_MIME_TYPES,
    accepted_mime_prefixes: tuple[str, ...] = _DEFAULT_MIME_PREFIXES,
    extensions: frozenset[str] = _DEFAULT_EXTENSIONS,
) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in accepted_mime_types or mime.startswith(accepted_mime_prefixes):
        return True
    return mime == "application/octet-stream" and Path(urlsplit(url).path.lower()).suffix in extensions


class CapturePipeline:
    def __init__(
        self,
        data_dir: Path,
        run_id: str,
        *,
        allowed_hosts: frozenset[str],
        warc_rotate_bytes: int = 1024 * 1024 * 1024,
        min_free_bytes: int = 50 * 1024**3,
        min_free_percent: float = 10.0,
        accepted_mime_types: frozenset[str] = _DEFAULT_MIME_TYPES,
        accepted_mime_prefixes: tuple[str, ...] = _DEFAULT_MIME_PREFIXES,
        media_extensions: frozenset[str] = _DEFAULT_EXTENSIONS,
    ) -> None:
        self.data_dir = data_dir
        self.run_id = run_id
        self.allowed_hosts = allowed_hosts
        self.warc_rotate_bytes = warc_rotate_bytes
        self.min_free_bytes = min_free_bytes
        self.min_free_percent = min_free_percent
        self.accepted_mime_types = accepted_mime_types
        self.accepted_mime_prefixes = accepted_mime_prefixes
        self.media_extensions = media_extensions
        self.latest = load_latest(data_dir / "indexes" / "latest.cdxj")
        self.run_index = data_dir / "indexes" / "runs" / f"{run_id}.cdxj"
        self.run_index.parent.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, WarcStore] = {}
        self.events = RunStore(RunPaths.for_run(data_dir, run_id))
        self.coverage = CoverageLedger(RunPaths.for_run(data_dir, run_id).lifecycle)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            Path(crawler.settings.get("CRAWL_DATA_DIR", "data")),
            crawler.settings.get("CRAWL_RUN_ID", "manual"),
            allowed_hosts=frozenset(crawler.settings.getlist("CRAWL_ALLOWED_HOSTS")),
            warc_rotate_bytes=crawler.settings.getint("CRAWL_WARC_ROTATE_BYTES", 1024 * 1024 * 1024),
            min_free_bytes=crawler.settings.getint("CRAWL_MIN_FREE_BYTES", 50 * 1024**3),
            min_free_percent=crawler.settings.getfloat("CRAWL_MIN_FREE_PERCENT", 10.0),
            accepted_mime_types=frozenset(crawler.settings.getlist("CRAWL_ACCEPTED_MIME_TYPES")) or _DEFAULT_MIME_TYPES,
            accepted_mime_prefixes=tuple(crawler.settings.getlist("CRAWL_ACCEPTED_MIME_PREFIXES")) or _DEFAULT_MIME_PREFIXES,
            media_extensions=frozenset(crawler.settings.getlist("CRAWL_MEDIA_EXTENSIONS")) or _DEFAULT_EXTENSIONS,
        )

    def process_item(self, item: Capture | FetchError | RejectedUrl):
        if isinstance(item, FetchError):
            self.events.append_error(
                {"url": item.url, "type": item.error_type, "error": item.message}
            )
            self.events.append_fetch(
                {"url": item.url, "status": "error", "error": item.error_type}
            )
            self.coverage.append(
                LifecycleEvent(
                    url=item.url,
                    phase="failed",
                    hostname=urlsplit(item.parent_url or item.url).hostname or "unknown",
                    source=item.discovery_source,
                    parent_url=item.parent_url,
                    mechanism="playwright" if item.playwright_attempted else "http",
                    reason=item.error_type,
                    attempts=item.attempts,
                )
            )
            return item
        if isinstance(item, RejectedUrl):
            self.events.append_rejection(
                {
                    "source_url": item.source_url,
                    "discovered_url": item.discovered_url,
                    "reason": item.reason,
                }
            )
            self.coverage.append(
                LifecycleEvent(
                    url=item.discovered_url,
                    phase="budget_rejected" if item.reason == "request_budget" else "rejected",
                    hostname=urlsplit(item.source_url).hostname or "unknown",
                    source="policy",
                    parent_url=item.source_url,
                    reason=item.reason,
                )
            )
            return item
        if not isinstance(item, Capture):
            return item
        host = (urlsplit(item.url).hostname or "").lower().rstrip(".")
        if host not in self.allowed_hosts:
            rejection = RejectedUrl(
                source_url=item.parent_url or item.url,
                discovered_url=item.url,
                reason="host_out_of_scope",
            )
            self.process_item(rejection)
            return rejection
        if not is_supported_content(
            dict(item.headers).get("Content-Type", ""),
            item.url,
            self.accepted_mime_types,
            self.accepted_mime_prefixes,
            self.media_extensions,
        ):
            self.coverage.append(
                LifecycleEvent(
                    url=item.url,
                    phase="rejected",
                    hostname=urlsplit(item.parent_url or item.url).hostname or "unknown",
                    source=item.discovery_source,
                    parent_url=item.parent_url,
                    target_kind=item.target_kind,
                    mechanism=item.fetch_mechanism,
                    mime=item.mime,
                    reason="unsupported_mime",
                )
            )
            return item
        host = host or "unknown"
        today = item.captured_at.astimezone(timezone.utc).strftime("%Y/%m/%d")
        store_key = f"{today}/{host}"
        if store_key not in self._stores:
            if not has_capacity(self.data_dir, self.min_free_bytes, self.min_free_percent):
                self.coverage.append(
                    LifecycleEvent(
                        url=item.url,
                        phase="failed",
                        hostname=host,
                        source=item.discovery_source,
                        parent_url=item.parent_url,
                        target_kind=item.target_kind,
                        mechanism=item.fetch_mechanism,
                        status=item.status,
                        mime=item.mime,
                        reason="storage_watermark",
                    )
                )
                raise StorageCapacityError("storage watermark reached")
            self._stores[store_key] = WarcStore(
                self.data_dir / "archives" / today,
                host,
                rotate_bytes=self.warc_rotate_bytes,
            )
        store = self._stores[store_key]
        previous = self.latest.get(item.url)
        digest = f"sha256:{hashlib.sha256(item.payload).hexdigest()}"
        previous_ref = None
        if previous and previous.digest == digest:
            previous_ref = ArchiveRef(
                previous.filename,
                previous.offset,
                previous.length,
                previous.record_id,
                previous.digest,
                previous.record_type,
            )
        ref = store.write(item, previous_ref, run_id=self.run_id)
        self.coverage.append(
            LifecycleEvent(
                url=item.url,
                phase="fetched",
                hostname=urlsplit(item.parent_url or item.url).hostname or host,
                source=item.discovery_source,
                parent_url=item.parent_url,
                target_kind=item.target_kind,
                mechanism=item.fetch_mechanism,
                status=item.status,
                mime=item.mime,
                bytes=len(item.payload),
                attempts=1,
            )
        )
        entry = CdxjEntry.from_capture(item, ref)
        with self.run_index.open("a", encoding="utf-8") as stream:
            stream.write(entry.to_line() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append_fetch(
            {
                "url": item.url,
                "captured_at": item.captured_at.isoformat().replace("+00:00", "Z"),
                "status": item.status,
                "mime": item.mime,
                "digest": ref.payload_digest,
                "record_type": ref.record_type,
                "filename": ref.filename,
                "offset": ref.offset,
                "length": ref.length,
            }
        )
        self.coverage.append(
            LifecycleEvent(
                url=item.url,
                phase="archived",
                hostname=urlsplit(item.parent_url or item.url).hostname or host,
                source=item.discovery_source,
                parent_url=item.parent_url,
                target_kind=item.target_kind,
                mechanism=item.fetch_mechanism,
                status=item.status,
                mime=item.mime,
                bytes=len(item.payload),
                attempts=1,
            )
        )
        self.latest[item.url] = entry
        return item

    def close_spider(self, spider=None) -> None:
        for store in self._stores.values():
            store.close()
