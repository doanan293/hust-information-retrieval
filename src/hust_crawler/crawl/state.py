from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator, Literal, TYPE_CHECKING
from collections.abc import Mapping

from .link_policy import classify_link
from .recovery import validate_policy_migration

if TYPE_CHECKING:
    from .assets import AssetRole

FamilyKind = Literal["pagination", "search_filter", "calendar_archive", "action_auth"]

_RETRYABLE_REQUEST_FAILURE_MARKERS = (
    "an error occurred while connecting",
    "connection failure",
    "connection refused",
    "connection reset",
    "connection timed out",
    "connection to the other side was lost",
    "connection was closed cleanly",
    "dns lookup failed",
    "name or service not known",
    "network is unreachable",
    "no route to host",
    "temporary failure in name resolution",
    "timeout",
    "timed out",
    "took longer than",
)

LEGACY_V4_SEMANTIC_DEFAULTS = {
    "robots_txt_obey": True,
    "cookies_enabled": False,
    "redirect_max_times": 20,
    "user_agent_name": "HUSTPublicCrawler",
    "user_agent_version": "1.0",
}

LEGACY_V4_RECLASSIFIED_RUNTIME_KEYS = frozenset(
    {
        "download_timeout_seconds",
        "retry_times",
        "retry_backoff_base_seconds",
        "retry_max_delay_seconds",
    }
)


def normalize_legacy_semantic_config(snapshot: Mapping[str, object]) -> dict[str, object]:
    normalized = {
        name: value
        for name, value in snapshot.items()
        if name not in LEGACY_V4_RECLASSIFIED_RUNTIME_KEYS
    }
    for name, value in LEGACY_V4_SEMANTIC_DEFAULTS.items():
        normalized.setdefault(name, value)
    return normalized


@dataclass(frozen=True, slots=True)
class RouteFamilyState:
    family_key: str
    kind: FamilyKind
    scheduled_next_ordinal: int | None
    content_fingerprints: tuple[str, ...]
    target_fingerprints: tuple[str, ...]
    accepted_targets: tuple[str, ...]
    consecutive_zero_novelty: int
    closed: bool
    closure_reason: str | None


@dataclass(frozen=True, slots=True)
class FamilyObservation:
    family_key: str
    kind: FamilyKind
    ordinal: int | None
    content_fingerprint: str
    target_fingerprint: str
    content_targets: tuple[str, ...]


class CrawlState:
    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        connection: sqlite3.Connection,
    ) -> None:
        self.root = root
        self.manifest = manifest
        self.manifest_path = root / "manifest.json"
        self.lock_path = root / "run.lock"
        self.connection = connection
        self._lock_acquired = True

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        phase: Literal["discover", "crawl"],
        input_path: Path,
        semantic_config: dict[str, object],
        runtime_config: dict[str, object],
        resume: bool = False,
        allow_policy_migration: bool = False,
    ) -> "CrawlState":
        root.mkdir(parents=True, exist_ok=True)
        input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        manifest_path = root / "manifest.json"
        lock_path = root / "run.lock"

        # Lock acquisition
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
            except (OSError, ValueError):
                lock_path.unlink(missing_ok=True)
            else:
                raise RuntimeError(f"crawl is already running with PID {pid}")

        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"crawl state is locked: {root}") from exc
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)

        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not resume:
                    raise FileExistsError(f"crawl state already exists: {root}")
                if manifest.get("state_version") != 4:
                    raise ValueError("resume state version does not match saved crawl")
                if manifest.get("phase") != phase:
                    raise ValueError("resume phase does not match saved run")
                if manifest.get("input_sha256") != input_sha256:
                    raise ValueError("resume input does not match saved crawl")
                saved_semantic = normalize_legacy_semantic_config(manifest.get("semantic_config", {}))
                effective_semantic = normalize_legacy_semantic_config(semantic_config)
                if saved_semantic != effective_semantic:
                    if not allow_policy_migration:
                        raise ValueError("resume semantic configuration does not match saved crawl")
                    validate_policy_migration(saved_semantic, effective_semantic)
                    migrations = list(manifest.get("policy_migrations", []))
                    if not any(
                        entry.get("to_revision") == effective_semantic.get("access_policy_revision")
                        for entry in migrations
                        if isinstance(entry, dict)
                    ):
                        migrations.append(
                            {
                                "from_revision": saved_semantic.get("access_policy_revision", 1),
                                "to_revision": effective_semantic["access_policy_revision"],
                                "timestamp": time.time(),
                            }
                        )
                    manifest["policy_migrations"] = migrations
                    manifest["semantic_config"] = semantic_config
                history = manifest.get("runtime_history", [])
                if not history or history[-1] != runtime_config:
                    history.append(runtime_config)
                    manifest["runtime_history"] = history
                manifest["status"] = "running"

            else:
                if resume:
                    raise ValueError(f"cannot resume non-existent crawl state: {root}")
                manifest = {
                    "state_version": 4,
                    "phase": phase,
                    "input_sha256": input_sha256,
                    "semantic_config": semantic_config,
                    "runtime_history": [runtime_config],
                    "status": "running",
                    "started_at": time.time(),
                }

            # Initialize SQLite
            db_path = root / "index.sqlite3"
            connection = sqlite3.connect(str(db_path), autocommit=True)
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA synchronous=NORMAL;")
            cls._init_db(connection)

            instance = cls(root, manifest, connection)
            instance._write_manifest(manifest)
            return instance
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _init_db(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS url_records (
                url TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        connection.execute("CREATE TABLE IF NOT EXISTS targets (url TEXT PRIMARY KEY, payload TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS articles (url TEXT PRIMARY KEY, payload TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS files (url TEXT PRIMARY KEY, payload TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS errors (url TEXT PRIMARY KEY, payload TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS content_owner (content_id TEXT PRIMARY KEY, url TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS metrics (name TEXT PRIMARY KEY, value INTEGER NOT NULL);")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS host_discovery (
                hostname TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS route_families (
                family_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_referrers (
                url TEXT NOT NULL,
                page_url TEXT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY(url, page_url, role)
            );
            """
        )

    def _write_manifest(self, value: dict[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.manifest_path)

    def _merged_record(self, record: dict[str, object]) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT payload FROM url_records WHERE url = ?", (str(record["url"]),)
        ).fetchone()
        merged = json.loads(row[0]) if row else {}
        merged.update({key: value for key, value in record.items() if value is not None})
        return merged

    def merge_url(self, record: dict[str, object], *, completed: bool = False) -> dict[str, object]:
        merged = self._merged_record(record)
        url = str(merged["url"])
        payload = json.dumps(merged, ensure_ascii=False)
        status = str(merged.get("status", "discovered"))
        comp_val = 1 if (completed or merged.get("completed")) else 0
        self.connection.execute(
            """
            INSERT INTO url_records(url, payload, status, completed)
            VALUES (:url, :payload, :status, :completed)
            ON CONFLICT(url) DO UPDATE SET
              payload = excluded.payload,
              status = excluded.status,
              completed = MAX(url_records.completed, excluded.completed);
            """,
            {"url": url, "payload": payload, "status": status, "completed": comp_val},
        )
        return merged

    def _put_payload(self, table: str, record: dict[str, object]) -> None:
        url = str(record["url"])
        payload = json.dumps(record, ensure_ascii=False)
        self.connection.execute(
            f"""
            INSERT INTO {table} (url, payload) VALUES (?, ?)
            ON CONFLICT(url) DO UPDATE SET payload = excluded.payload;
            """,
            (url, payload),
        )

    def complete_url(
        self,
        disposition: dict[str, object],
        *,
        article: dict[str, object] | None = None,
        file: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            if article is not None:
                self._put_payload("articles", article)
            if file is not None:
                self._put_payload("files", file)
            if error is not None:
                self._put_payload("errors", error)
            self.merge_url(disposition, completed=True)
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise

    def upsert_url(self, record: dict[str, object]) -> None:
        self.merge_url(record, completed=bool(record.get("completed")))

    def put_article(self, record: dict[str, object]) -> None:
        self._put_payload("articles", record)

    def put_file(self, record: dict[str, object]) -> None:
        self._put_payload("files", record)

    def put_error(self, record: dict[str, object]) -> None:
        self._put_payload("errors", record)

    def is_complete(self, url: str) -> bool:
        cursor = self.connection.execute("SELECT completed FROM url_records WHERE url = ?;", (url,))
        row = cursor.fetchone()
        return bool(row and row[0] == 1)

    def checkpoint(self, url: str) -> None:
        self.connection.execute("UPDATE url_records SET completed = 1 WHERE url = ?;", (url,))
        self.connection.commit()

    def claim_content(self, content_id: str, url: str) -> str:
        self.connection.execute(
            """
            INSERT INTO content_owner (content_id, url)
            VALUES (?, ?)
            ON CONFLICT(content_id) DO NOTHING;
            """,
            (content_id, url),
        )
        self.connection.commit()
        cursor = self.connection.execute("SELECT url FROM content_owner WHERE content_id = ?;", (content_id,))
        row = cursor.fetchone()
        assert row is not None
        return str(row[0])

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in self.connection.execute("SELECT status, count(*) FROM url_records GROUP BY status;"):
            result[f"url_status_{row[0]}"] = int(row[1])

        cursor = self.connection.execute("SELECT count(*) FROM url_records;")
        result["total_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM url_records WHERE completed = 1;")
        result["completed_urls"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM articles;")
        result["articles"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM files;")
        result["files"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM errors;")
        result["errors"] = int(cursor.fetchone()[0])

        cursor = self.connection.execute("SELECT count(*) FROM targets;")
        result["targets"] = int(cursor.fetchone()[0])

        return result

    def put_target(self, record: dict[str, object]) -> None:
        url = str(record["url"])
        self.connection.execute(
            "INSERT OR REPLACE INTO targets (url, payload) VALUES (?, ?);",
            (url, json.dumps(record, sort_keys=True, ensure_ascii=False)),
        )

    def iter_targets(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute("SELECT payload FROM targets ORDER BY url;")
        for row in cursor:
            yield json.loads(row[0])

    def put_route_family(self, state: RouteFamilyState) -> None:
        payload = json.dumps(
            {
                "family_key": state.family_key,
                "kind": state.kind,
                "scheduled_next_ordinal": state.scheduled_next_ordinal,
                "content_fingerprints": list(state.content_fingerprints),
                "target_fingerprints": list(state.target_fingerprints),
                "accepted_targets": list(state.accepted_targets),
                "consecutive_zero_novelty": state.consecutive_zero_novelty,
                "closed": state.closed,
                "closure_reason": state.closure_reason,
            },
            ensure_ascii=False,
        )
        self.connection.execute(
            """
            INSERT INTO route_families (family_key, payload) VALUES (?, ?)
            ON CONFLICT(family_key) DO UPDATE SET payload = excluded.payload;
            """,
            (state.family_key, payload),
        )

    def iter_route_families(self) -> Iterator[RouteFamilyState]:
        cursor = self.connection.execute(
            "SELECT payload FROM route_families ORDER BY family_key ASC;"
        )
        for (payload_str,) in cursor.fetchall():
            d = json.loads(payload_str)
            yield RouteFamilyState(
                family_key=d["family_key"],
                kind=d["kind"],
                scheduled_next_ordinal=d["scheduled_next_ordinal"],
                content_fingerprints=tuple(d["content_fingerprints"]),
                target_fingerprints=tuple(d["target_fingerprints"]),
                accepted_targets=tuple(d["accepted_targets"]),
                consecutive_zero_novelty=d["consecutive_zero_novelty"],
                closed=d["closed"],
                closure_reason=d["closure_reason"],
            )

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None:
        self.connection.execute(
            """
            INSERT INTO asset_referrers (url, page_url, role)
            VALUES (?, ?, ?)
            ON CONFLICT(url, page_url, role) DO NOTHING;
            """,
            (url, page_url, role),
        )
        cursor = self.connection.execute("SELECT payload FROM files WHERE url = ?;", (url,))
        row = cursor.fetchone()
        if row:
            payload = json.loads(row[0])
            payload["referring_pages"] = list(self.asset_referrers(url))
            new_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            self.connection.execute(
                "UPDATE files SET payload = ? WHERE url = ?;",
                (new_payload, url),
            )

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        cursor = self.connection.execute(
            "SELECT DISTINCT page_url FROM asset_referrers WHERE url = ? ORDER BY page_url ASC;",
            (url,),
        )
        return tuple(row[0] for row in cursor.fetchall())



    def iter_url_records(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute("SELECT payload FROM url_records ORDER BY url;")
        for row in cursor:
            yield json.loads(row[0])

    def iter_pending_scheduled_records(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute(
            """
            SELECT payload
            FROM url_records
            WHERE completed = 0 AND status = 'scheduled'
            ORDER BY url;
            """
        )
        for row in cursor:
            record = json.loads(row[0])
            if record.get("frontier_action") == "scheduled":
                yield record

    def requeue_retryable_failures(self) -> int:
        rows = self.connection.execute(
            """
            SELECT url_records.url, url_records.payload, errors.payload
            FROM url_records
            JOIN errors ON errors.url = url_records.url
            WHERE url_records.completed = 1 AND url_records.status = 'failed';
            """
        ).fetchall()
        retryable: list[tuple[str, dict[str, object]]] = []
        for url, record_payload, error_payload in rows:
            error = json.loads(error_payload)
            message = str(error.get("message", "")).lower()
            if error.get("error") != "request_failed" or not any(
                marker in message for marker in _RETRYABLE_REQUEST_FAILURE_MARKERS
            ):
                continue
            record = json.loads(record_payload)
            record["status"] = "scheduled"
            for key in ("completed", "final_url", "http_status", "reason"):
                record.pop(key, None)
            retryable.append((str(url), record))

        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            for url, record in retryable:
                self.connection.execute(
                    """
                    UPDATE url_records
                    SET payload = ?, status = 'scheduled', completed = 0
                    WHERE url = ?;
                    """,
                    (json.dumps(record, ensure_ascii=False), url),
                )
                self.connection.execute("DELETE FROM errors WHERE url = ?;", (url,))
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise
        return len(retryable)

    def requeue_query_variant_truncations(self) -> int:
        rows = self.connection.execute(
            """
            SELECT url, payload
            FROM url_records
            WHERE completed = 1 AND status = 'skipped';
            """
        ).fetchall()
        recoverable: list[tuple[str, dict[str, object]]] = []
        for url, payload in rows:
            record = json.loads(payload)
            if record.get("reason") != "query_variant_limit":
                continue
            classified = classify_link(str(url), source_url=str(url))
            if classified.kind not in {"content", "pagination"}:
                continue
            record.update(
                {
                    "status": "scheduled",
                    "seed_type": "recursive",
                    "frontier_action": "scheduled",
                    "discovery_source": "html_link",
                    "response_purpose": "page",
                    "explicit": False,
                    "link_kind": classified.kind,
                    "family_key": classified.family_key,
                    "ordinal": classified.ordinal,
                    "priority": 500 if classified.kind == "content" else 100,
                }
            )
            for key in ("completed", "final_url", "http_status", "reason"):
                record.pop(key, None)
            recoverable.append((str(url), record))

        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            for url, record in recoverable:
                self.connection.execute(
                    """
                    UPDATE url_records
                    SET payload = ?, status = 'scheduled', completed = 0
                    WHERE url = ?;
                    """,
                    (json.dumps(record, ensure_ascii=False), url),
                )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise
        return len(recoverable)

    def requeue_access_gates(self) -> int:
        rows = self.connection.execute(
            """
            SELECT url, payload
            FROM url_records
            WHERE completed = 1 AND status = 'skipped';
            """
        ).fetchall()
        recoverable: list[tuple[str, dict[str, object]]] = []
        for url, payload in rows:
            record = json.loads(payload)
            if record.get("reason") != "captcha_blocked":
                continue
            record["status"] = "scheduled"
            for key in ("completed", "final_url", "http_status", "reason"):
                record.pop(key, None)
            recoverable.append((str(url), record))

        self.connection.execute("BEGIN IMMEDIATE;")
        try:
            for url, record in recoverable:
                self.connection.execute(
                    """
                    UPDATE url_records
                    SET payload = ?, status = 'scheduled', completed = 0
                    WHERE url = ?;
                    """,
                    (json.dumps(record, ensure_ascii=False), url),
                )
            self.connection.execute("COMMIT;")
        except Exception:
            self.connection.execute("ROLLBACK;")
            raise
        return len(recoverable)

    def reason_counts(self, reasons: frozenset[str] | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        cursor = self.connection.execute("SELECT payload FROM url_records;")
        for row in cursor:
            rec = json.loads(row[0])
            r = rec.get("reason")
            if not r:
                continue
            if reasons is None or r in reasons:
                counts[str(r)] = counts.get(str(r), 0) + 1
        return counts

    def skipped_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        cursor = self.connection.execute("SELECT payload FROM url_records;")
        for row in cursor:
            rec = json.loads(row[0])
            if rec.get("status") == "skipped":
                r = rec.get("reason")
                if r:
                    counts[str(r)] = counts.get(str(r), 0) + 1
        return counts

    def upsert_host_discovery(self, record: dict[str, object]) -> None:
        hostname = str(record["hostname"])
        payload = json.dumps(record, ensure_ascii=False)
        self.connection.execute(
            """
            INSERT INTO host_discovery (hostname, payload)
            VALUES (?, ?)
            ON CONFLICT(hostname) DO UPDATE SET payload = excluded.payload;
            """,
            (hostname, payload),
        )

    def iter_host_discovery(self) -> Iterator[dict[str, object]]:
        cursor = self.connection.execute("SELECT payload FROM host_discovery ORDER BY hostname;")
        for row in cursor:
            yield json.loads(row[0])

    def export_discovery(self, output: Path) -> None:
        output.mkdir(parents=True, exist_ok=True)

        urls_txt = output / "urls.txt"
        tmp_urls = urls_txt.with_suffix(".tmp")
        cursor = self.connection.execute("SELECT url FROM targets ORDER BY url;")
        with tmp_urls.open("w", encoding="utf-8") as file:
            for (url,) in cursor:
                file.write(f"{url}\n")
        os.replace(tmp_urls, urls_txt)

        errors_jsonl = output / "errors.jsonl"
        tmp_errors = errors_jsonl.with_suffix(".tmp")
        cursor = self.connection.execute("SELECT payload FROM errors ORDER BY url;")
        with tmp_errors.open("w", encoding="utf-8") as file:
            for (payload,) in cursor:
                file.write(f"{payload}\n")
        os.replace(tmp_errors, errors_jsonl)

    def export_crawl(self, output: Path) -> None:
        output.mkdir(parents=True, exist_ok=True)

        urls_txt = output / "urls.txt"
        tmp_urls = urls_txt.with_suffix(".tmp")
        cursor = self.connection.execute(
            """
            SELECT url
            FROM url_records
            WHERE status IN ('extracted', 'file_saved')
            ORDER BY url;
            """
        )
        with tmp_urls.open("w", encoding="utf-8") as file:
            for (url,) in cursor:
                file.write(f"{url}\n")
        os.replace(tmp_urls, urls_txt)

        def _export_table(table: str, target: Path) -> None:
            tmp = target.with_suffix(".tmp")
            cursor = self.connection.execute(f"SELECT payload FROM {table} ORDER BY url;")
            with tmp.open("w", encoding="utf-8") as file:
                for row in cursor:
                    file.write(f"{row[0]}\n")
            os.replace(tmp, target)

        _export_table("url_records", output / "url_records.jsonl")
        _export_table("articles", output / "articles.jsonl")
        _export_table("files", output / "files.jsonl")
        _export_table("errors", output / "errors.jsonl")

    def export(self, output: Path) -> None:
        self.export_crawl(output)

    def asset_metrics(self) -> dict[str, dict[str, int]]:
        from .assets import classify_asset

        groups: dict[str, dict[str, int]] = {
            "image": {"file_count": 0, "unique_bytes": 0},
            "document": {"file_count": 0, "unique_bytes": 0},
            "media": {"file_count": 0, "unique_bytes": 0},
        }
        seen_blobs: set[tuple[str, str]] = set()
        rows = self.connection.execute("SELECT payload FROM files").fetchall()
        for (payload_json,) in rows:
            payload = json.loads(payload_json)
            url = payload.get("final_url") or payload.get("url", "")
            content_type = payload.get("content_type", "")
            asset_role = payload.get("asset_role")
            group = payload.get("asset_group") or classify_asset(content_type, url, asset_role)
            if group not in groups:
                group = "document"
            groups[group]["file_count"] += 1
            sha256 = payload.get("sha256")
            size = int(payload.get("size", 0))
            blob_id = (group, sha256) if sha256 else (group, url)
            if blob_id not in seen_blobs:
                seen_blobs.add(blob_id)
                groups[group]["unique_bytes"] += size
        return groups

    def finish(
        self,
        status: str,
        exit_code: int,
        counts: dict[str, int],
        metrics: dict[str, object],
        truncation: dict[str, object] | None = None,
        *,
        discovery: dict[str, object] | None = None,
        frontier: dict[str, object] | None = None,
        public_output: Path | None = None,
    ) -> None:
        finish_data: dict[str, object] = {
            "status": status,
            "exit_code": exit_code,
            "counts": counts,
            "metrics": metrics,
            "truncation": truncation,
            "truncation_reasons": sorted(list(truncation.keys())) if isinstance(truncation, dict) else [],
            "finished_at": time.time(),
        }
        if discovery is not None or self.manifest.get("phase") == "discover":
            finish_data["discovery"] = discovery
        if frontier is not None:
            finish_data["frontier"] = frontier
        elif "frontier" in metrics and isinstance(metrics["frontier"], dict):
            finish_data["frontier"] = metrics["frontier"]
        if self.manifest.get("phase") != "discover":
            finish_data["assets"] = self.asset_metrics()
            finish_data["skipped"] = self.skipped_reasons()

        self.manifest.update(finish_data)
        self._write_manifest(self.manifest)
        if public_output is not None:
            public_output.mkdir(parents=True, exist_ok=True)
            pub_manifest = public_output / "manifest.json"
            tmp = pub_manifest.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, pub_manifest)

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self._lock_acquired:
                self.lock_path.unlink(missing_ok=True)
                self._lock_acquired = False


def validate_resume_workflow(state_root: Path, expected: str) -> None:
    manifest_path = state_root / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = manifest.get("semantic_config", {}).get("crawl_strategy")
    if actual != expected:
        raise ValueError(
            f"cannot resume {actual or 'legacy'} state as {expected}; use a new output directory"
        )
