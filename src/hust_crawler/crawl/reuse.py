from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Iterator

from hust_crawler.policies.network import validate_public_web_url

from .assets import AssetGroup, AssetRole, classify_asset
from .state import CrawlState


_REUSABLE_STATUSES = frozenset({"complete", "complete_with_failures", "truncated"})
_ASSET_ROLES = frozenset({"inline_image", "media_reference", "document_attachment"})


@dataclass(frozen=True, slots=True)
class ReusedAssetTarget:
    url: str
    role: AssetRole
    group: AssetGroup
    referring_pages: tuple[str, ...]


def _lock_is_live(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def validate_reuse_source(source_output: Path, input_path: Path) -> tuple[Path, dict[str, object]]:
    state_dir = source_output / "state"
    manifest_path = state_dir / "manifest.json"
    database_path = state_dir / "index.sqlite3"
    if _lock_is_live(state_dir / "run.lock"):
        raise ValueError(f"source crawl is still running: {source_output}")
    if not manifest_path.is_file() or not database_path.is_file():
        raise ValueError(f"source is not a reusable crawl output: {source_output}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic = manifest.get("semantic_config", {})
    if (
        manifest.get("state_version") != 4
        or manifest.get("phase") != "crawl"
        or not isinstance(semantic, dict)
        or semantic.get("crawl_strategy") != "hybrid-unified"
        or semantic.get("assets") != "content-only"
        or semantic.get("asset_policy_version") != 2
    ):
        raise ValueError("source is not a compatible content-only crawl")
    if manifest.get("status") not in _REUSABLE_STATUSES:
        raise ValueError("source crawl has not completed with reusable output")

    input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    if manifest.get("input_sha256") != input_sha256:
        raise ValueError("source crawl input does not match --input")
    return database_path, manifest


def import_reused_content(
    source_output: Path,
    destination: CrawlState,
    input_path: Path,
) -> None:
    database_path, _ = validate_reuse_source(source_output, input_path)
    state_dir = source_output / "state"
    manifest_path = state_dir / "manifest.json"
    manifest_before = hashlib.sha256(manifest_path.read_bytes()).digest()
    try:
        with tempfile.TemporaryDirectory(prefix="hust-crawl-reuse-") as temporary:
            staged_database = Path(temporary) / database_path.name
            shutil.copy2(database_path, staged_database)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{database_path}{suffix}")
                if sidecar.is_file():
                    shutil.copy2(sidecar, Path(f"{staged_database}{suffix}"))

            source = sqlite3.connect(staged_database, autocommit=True)
            try:
                if _lock_is_live(state_dir / "run.lock"):
                    raise ValueError(f"source crawl is still running: {source_output}")
                source.backup(destination.connection)
            finally:
                source.close()
    except sqlite3.Error as exc:
        raise ValueError(f"could not import reusable crawl: {exc}") from exc

    manifest_after = hashlib.sha256(manifest_path.read_bytes()).digest()
    if _lock_is_live(state_dir / "run.lock") or manifest_after != manifest_before:
        raise ValueError("source crawl changed while reusable content was imported")


def prepare_reused_asset_targets(state: CrawlState) -> None:
    state.connection.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS reused_asset_targets (
            url TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            asset_group TEXT NOT NULL
        )
        """
    )
    state.connection.execute("DELETE FROM reused_asset_targets")
    try:
        rows = state.connection.execute("SELECT payload FROM articles ORDER BY url")
        for (payload_json,) in rows:
            article = json.loads(payload_json)
            referring_page = str(article.get("final_url") or article.get("url") or "")
            for raw_asset in article.get("assets", []):
                if not isinstance(raw_asset, dict):
                    continue
                role_value = raw_asset.get("role")
                if role_value not in _ASSET_ROLES:
                    continue
                decision = validate_public_web_url(str(raw_asset.get("url") or ""))
                if not decision.accepted or decision.canonical_url is None:
                    continue
                url = decision.canonical_url
                role: AssetRole = role_value
                group = classify_asset(url=url, asset_role=role)
                state.connection.execute(
                    """
                    INSERT INTO reused_asset_targets(url, role, asset_group)
                    VALUES (?, ?, ?)
                    ON CONFLICT(url) DO NOTHING
                    """,
                    (url, role, group),
                )
                if referring_page:
                    state.add_asset_referrer(url, referring_page, role)
    except (AttributeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"could not read reused article metadata: {exc}") from exc


def reused_asset_targets(state: CrawlState) -> Iterator[ReusedAssetTarget]:

    targets = state.connection.execute(
        "SELECT url, role, asset_group FROM reused_asset_targets ORDER BY url"
    )
    for url, role, group in targets:
        yield ReusedAssetTarget(
            url=url,
            role=role,
            group=group,
            referring_pages=state.asset_referrers(url),
        )
