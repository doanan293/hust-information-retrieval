import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hust_crawler.run_store import RunPaths, RunStore, has_capacity


def test_run_store_creates_portable_artifacts(tmp_path: Path) -> None:
    paths = RunPaths.create(tmp_path, "20260822T020000Z")
    store = RunStore(paths)
    store.start({"concurrent_per_host": 1})
    store.append_fetch({"url": "https://example.com/", "status": 200})
    store.finish("completed", {"fetched": 1})

    assert json.loads(paths.manifest.read_text())["status"] == "completed"
    assert json.loads(paths.stats.read_text()) == {"fetched": 1}
    assert len(paths.fetches.read_text().splitlines()) == 1


def test_second_live_lock_is_rejected(tmp_path: Path) -> None:
    paths = RunPaths.create(tmp_path, "run")
    first = RunStore(paths)
    first.acquire_lock()
    with pytest.raises(RuntimeError, match="already active"):
        RunStore(paths).acquire_lock()
    first.release_lock()


def test_capacity_requires_both_absolute_and_percent_watermarks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "hust_crawler.run_store.shutil.disk_usage",
        lambda _: SimpleNamespace(total=1000, used=100, free=900),
    )
    assert has_capacity(tmp_path, minimum_bytes=500, minimum_percent=10.0)
    assert not has_capacity(tmp_path, minimum_bytes=950, minimum_percent=10.0)
    assert not has_capacity(tmp_path, minimum_bytes=500, minimum_percent=95.0)
