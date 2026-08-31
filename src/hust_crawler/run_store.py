from __future__ import annotations

import fcntl
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StorageCapacityError(RuntimeError):
    pass


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path
    manifest: Path
    stats: Path
    fetches: Path
    errors: Path
    rejected_urls: Path
    lifecycle: Path
    cdxj: Path
    state: Path

    @classmethod
    def for_run(cls, data_dir: Path, run_id: str) -> "RunPaths":
        root = data_dir / "runs" / run_id
        state = data_dir / "state" / run_id
        return cls(
            root=root,
            manifest=root / "manifest.json",
            stats=root / "stats.json",
            fetches=root / "fetches.jsonl",
            errors=root / "errors.jsonl",
            rejected_urls=root / "rejected-urls.jsonl",
            lifecycle=root / "lifecycle.jsonl",
            cdxj=root / "index.cdxj",
            state=state,
        )

    @classmethod
    def create(cls, data_dir: Path, run_id: str) -> "RunPaths":
        paths = cls.for_run(data_dir, run_id)
        paths.root.mkdir(parents=True, exist_ok=False)
        paths.state.mkdir(parents=True, exist_ok=True)
        return paths


class RunStore:
    def __init__(self, paths: RunPaths) -> None:
        self.paths = paths
        self._lock_stream: TextIO | None = None

    def acquire_lock(self) -> None:
        lock_path = self.paths.root.parent.parent / ".crawl.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            stream.close()
            raise RuntimeError("crawl already active") from exc
        self._lock_stream = stream

    def release_lock(self) -> None:
        if self._lock_stream is not None:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
            self._lock_stream.close()
            self._lock_stream = None

    def start(self, config_snapshot: dict[str, object]) -> None:
        _atomic_json(
            self.paths.manifest,
            {"run_id": self.paths.root.name, "started_at": _utc_now(), "status": "running", "config": config_snapshot},
        )

    def resume(self) -> None:
        manifest = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        manifest.pop("finished_at", None)
        manifest.update(
            {
                "resumed_at": _utc_now(),
                "resume_count": int(manifest.get("resume_count", 0)) + 1,
                "status": "running",
            }
        )
        _atomic_json(self.paths.manifest, manifest)

    def append_fetch(self, event: dict[str, object]) -> None:
        self._append_jsonl(self.paths.fetches, event)

    def append_error(self, event: dict[str, object]) -> None:
        self._append_jsonl(self.paths.errors, event)

    def append_rejection(self, event: dict[str, object]) -> None:
        self._append_jsonl(self.paths.rejected_urls, event)

    @staticmethod
    def _append_jsonl(path: Path, event: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            json.dump(event, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, status: str, stats: dict[str, object]) -> None:
        _atomic_json(self.paths.stats, stats)
        manifest = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        manifest.update({"finished_at": _utc_now(), "status": status})
        _atomic_json(self.paths.manifest, manifest)

    def __enter__(self) -> "RunStore":
        self.acquire_lock()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release_lock()


def has_capacity(path: Path, minimum_bytes: int, minimum_percent: float) -> bool:
    usage = shutil.disk_usage(path)
    free_percent = usage.free / usage.total * 100 if usage.total else 0.0
    return usage.free >= minimum_bytes and free_percent >= minimum_percent
