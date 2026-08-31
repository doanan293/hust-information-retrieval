from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from .models import ArchiveRef, Capture


class WarcStore:
    def __init__(self, directory: Path, hostname: str, rotate_bytes: int = 1_073_741_824) -> None:
        self.directory = directory
        self.hostname = hostname
        self.rotate_bytes = rotate_bytes
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sequence = 1
        self._stream = None
        self._writer = None
        self._filename = ""
        self._next_sequence()
        self._open_next()

    def _next_sequence(self) -> None:
        pattern = re.compile(rf"{re.escape(self.hostname)}-(\d{{5}})\.warc\.gz$")
        numbers = [
            int(match.group(1))
            for path in self.directory.iterdir()
            if (match := pattern.fullmatch(path.name))
        ]
        self.sequence = max(numbers, default=0) + 1

    def _open_next(self) -> None:
        if self._stream is not None:
            self.close()
        self._filename = f"{self.hostname}-{self.sequence:05d}.warc.gz"
        self.sequence += 1
        self._stream = (self.directory / self._filename).open("xb+")
        self._writer = WARCWriter(self._stream, gzip=True)

    @staticmethod
    def _digest(payload: bytes) -> str:
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    def write(
        self,
        capture: Capture,
        previous: ArchiveRef | None = None,
        *,
        run_id: str = "manual",
    ) -> ArchiveRef:
        assert self._stream is not None and self._writer is not None
        digest = self._digest(capture.payload)
        if previous is None:
            http_headers = StatusAndHeaders(
                f"{capture.status} {capture.reason}".strip(),
                list(capture.headers),
                protocol="HTTP/1.1",
            )
            record = self._writer.create_warc_record(
                capture.url,
                "response",
                payload=io.BytesIO(capture.payload),
                warc_headers_dict={
                    "WARC-Date": capture.captured_at.isoformat().replace("+00:00", "Z"),
                    "WARC-Run-ID": run_id,
                },
                http_headers=http_headers,
            )
            record_type = "response"
        else:
            record = self._writer.create_warc_record(
                capture.url,
                "revisit",
                warc_headers_dict={
                    "WARC-Date": capture.captured_at.isoformat().replace("+00:00", "Z"),
                    "WARC-Run-ID": run_id,
                    "WARC-Refers-To": previous.record_id,
                    "WARC-Payload-Digest": digest,
                    "WARC-Profile": "http://netpreserve.org/warc/1.1/revisit/identical-payload-digest",
                },
            )
            record_type = "revisit"
        offset = self._stream.tell()
        self._writer.write_record(record)
        self._stream.flush()
        length = self._stream.tell() - offset
        record_id = record.rec_headers["WARC-Record-ID"]
        filename = self._filename
        if self._stream.tell() >= self.rotate_bytes:
            self._open_next()
        return ArchiveRef(filename, offset, length, record_id, digest, record_type)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
            self._stream = None
            self._writer = None
