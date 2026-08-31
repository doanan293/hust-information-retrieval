from __future__ import annotations

import hashlib
import io
import zlib
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator

from .cdxj import CdxjEntry


def _archive_path(data_dir: Path, entry: CdxjEntry) -> Path:
    if Path(entry.filename).name != entry.filename:
        raise ValueError("unsafe archive filename")
    date = entry.timestamp[:8]
    if len(date) != 8 or not date.isdigit():
        raise ValueError("invalid capture timestamp")
    return (
        data_dir
        / "archives"
        / date[:4]
        / date[4:6]
        / date[6:8]
        / entry.filename
    )


def _validate_entry(
    data_dir: Path,
    entry: CdxjEntry,
    known_record_ids: frozenset[str] = frozenset(),
) -> list[str]:
    errors: list[str] = []
    archive = _archive_path(data_dir, entry)
    with archive.open("rb") as stream:
        stream.seek(entry.offset)
        block = stream.read(entry.length)
    if len(block) != entry.length:
        return [f"short record: expected {entry.length} bytes, read {len(block)}"]
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decompressor.decompress(block)
        decompressor.flush()
    except zlib.error as exc:
        return [f"inexact compressed range: {exc}"]
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        return ["inexact compressed range"]

    iterator = ArchiveIterator(io.BytesIO(block))
    record = next(iterator)
    payload = record.content_stream().read()
    if record.rec_headers.get_header("WARC-Record-ID") != entry.record_id:
        errors.append("record_id mismatch")
    if record.rec_headers.get_header("WARC-Target-URI") != entry.url:
        errors.append("url mismatch")
    if record.rec_type != entry.record_type:
        errors.append("record_type mismatch")
    if record.rec_type == "response":
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    else:
        digest = record.rec_headers.get_header("WARC-Payload-Digest")
        reference = record.rec_headers.get_header("WARC-Refers-To")
        if reference not in known_record_ids:
            errors.append("revisit reference not found")
    if digest != entry.digest:
        errors.append("digest mismatch")
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        errors.append("CDXJ range contains multiple WARC records")
    return errors


def validate_run(data_dir: Path, run_id: str) -> dict[str, object]:
    index = data_dir / "indexes" / "runs" / f"{run_id}.cdxj"
    if not index.is_file():
        return {
            "run_id": run_id,
            "status": "invalid",
            "records": 0,
            "errors": [f"run index not found: {index}"],
        }

    lines = [line for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors: list[str] = []
    records = 0
    known_ids = {
        CdxjEntry.from_line(line).record_id
        for line in lines
        if line.strip()
    }
    cumulative = data_dir / "indexes" / "latest.cdxj"
    if cumulative.exists():
        known_ids.update(
            CdxjEntry.from_line(line).record_id
            for line in cumulative.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        records += 1
        try:
            entry = CdxjEntry.from_line(line)
            entry_errors = _validate_entry(data_dir, entry, frozenset(known_ids))
        except Exception as exc:
            entry_errors = [f"{type(exc).__name__}: {exc}"]
        errors.extend(f"line {line_number}: {error}" for error in entry_errors)
    return {
        "run_id": run_id,
        "status": "valid" if not errors else "invalid",
        "records": records,
        "errors": errors,
    }
