from __future__ import annotations

import json
import io
import hashlib
import os
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from warcio.archiveiterator import ArchiveIterator

from .models import ArchiveRef, Capture


def _surt(url: str) -> str:
    parts = urlsplit(url)
    host = ",".join(reversed(parts.hostname.split("."))) if parts.hostname else ""
    return f"{host}){parts.path or '/'}"


@dataclass(frozen=True, slots=True)
class CdxjEntry:
    url: str
    timestamp: str
    status: int
    mime: str
    digest: str
    filename: str
    offset: int
    length: int
    record_id: str
    record_type: str

    @classmethod
    def from_capture(cls, capture: Capture, ref: ArchiveRef) -> "CdxjEntry":
        timestamp = capture.captured_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        return cls(capture.url, timestamp, capture.status, capture.mime, ref.payload_digest, ref.filename, ref.offset, ref.length, ref.record_id, ref.record_type)

    def to_line(self) -> str:
        body = {"url": self.url, "status": self.status, "mime": self.mime, "digest": self.digest, "filename": self.filename, "offset": self.offset, "length": self.length, "record_id": self.record_id, "record_type": self.record_type}
        return f"{_surt(self.url)} {self.timestamp} {json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"

    @classmethod
    def from_line(cls, line: str) -> "CdxjEntry":
        _, timestamp, raw = line.rstrip("\n").split(" ", 2)
        body = json.loads(raw)
        return cls(timestamp=timestamp, **body)

    @classmethod
    def from_warc_record(cls, record, filename: str, offset: int, length: int) -> "CdxjEntry":
        date_value = record.rec_headers.get_header("WARC-Date")
        captured_at = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        if record.rec_type == "response" and record.http_headers is not None:
            status = int(record.http_headers.get_statuscode() or 0)
            mime = (record.http_headers.get_header("Content-Type") or "").split(";", 1)[0].strip()
            payload = record.content_stream().read()
            digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        else:
            status = 200
            mime = "application/octet-stream"
            digest = record.rec_headers.get_header("WARC-Payload-Digest") or ""
        return cls(
            url=record.rec_headers.get_header("WARC-Target-URI"),
            timestamp=captured_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S"),
            status=status,
            mime=mime,
            digest=digest,
            filename=filename,
            offset=offset,
            length=length,
            record_id=record.rec_headers.get_header("WARC-Record-ID"),
            record_type=record.rec_type,
        )


def load_latest(path: Path) -> dict[str, CdxjEntry]:
    latest: dict[str, CdxjEntry] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = CdxjEntry.from_line(line)
            if entry.url not in latest or entry.timestamp > latest[entry.url].timestamp:
                latest[entry.url] = entry
    return latest


def merge_indexes(previous: Path | None, current: Path, destination: Path) -> None:
    lines: set[str] = set()
    for source in (previous, current):
        if source and source.exists():
            lines.update(line for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write("\n".join(sorted(lines)) + ("\n" if lines else ""))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)


def _iter_gzip_records(path: Path):
    payload = path.read_bytes()
    offset = 0
    while offset < len(payload):
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decompressor.decompress(payload[offset:])
        if not decompressor.eof:
            break
        length = len(payload[offset:]) - len(decompressor.unused_data)
        if length <= 0:
            break
        record = next(ArchiveIterator(io.BytesIO(payload[offset : offset + length])))
        yield record, path.name, offset, length
        offset += length


def iter_run_warc_records(data_dir: Path, run_id: str):
    for path in sorted((data_dir / "archives").rglob("*.warc.gz")):
        for record, filename, offset, length in _iter_gzip_records(path):
            if record.rec_headers.get_header("WARC-Run-ID") == run_id:
                yield record, filename, offset, length


def _run_index_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "indexes" / "runs" / f"{run_id}.cdxj"


def recover_run_index(data_dir: Path, run_id: str) -> int:
    index_path = _run_index_path(data_dir, run_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    known = set()
    if index_path.exists():
        known = {
            CdxjEntry.from_line(line).record_id
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    recovered = [
        CdxjEntry.from_warc_record(record, filename, offset, length)
        for record, filename, offset, length in iter_run_warc_records(data_dir, run_id)
        if record.rec_headers.get_header("WARC-Record-ID") not in known
    ]
    if recovered:
        with index_path.open("a", encoding="utf-8") as stream:
            for entry in recovered:
                stream.write(entry.to_line() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return len(recovered)


def finalize_indexes(data_dir: Path, run_id: str) -> None:
    indexes = data_dir / "indexes"
    latest = indexes / "latest.cdxj"
    run_index = indexes / "runs" / f"{run_id}.cdxj"
    if latest.exists() and latest.stat().st_size > 0:
        sources = [run_index]
    else:
        sources = sorted((indexes / "runs").glob("*.cdxj"))
        if run_index not in sources:
            sources.append(run_index)
    for source in sources:
        merge_indexes(latest if latest.exists() else None, source, latest)

    urls = sorted(load_latest(latest))
    destination = data_dir / "crawled_urls.txt"
    temporary = destination.with_name(f".{destination.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write("\n".join(urls) + ("\n" if urls else ""))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
