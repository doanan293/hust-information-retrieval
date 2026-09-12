from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from hust_crawler.crawl.reuse import (
    import_reused_content,
    prepare_reused_asset_targets,
    reused_asset_targets,
)
from hust_crawler.crawl.state import CrawlState


def open_source(tmp_path: Path) -> tuple[Path, Path]:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    output = tmp_path / "crawl"
    state = CrawlState.open(
        output / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={
            "crawl_strategy": "hybrid-unified",
            "assets": "content-only",
            "asset_policy_version": 2,
        },
        runtime_config={},
    )
    state.complete_url(
        {"url": "https://a.test/one", "status": "extracted"},
        article={
            "url": "https://a.test/one",
            "final_url": "https://a.test/one",
            "assets": [
                {"url": "https://cdn.test/photo", "role": "inline_image"},
                {"url": "https://a.test/report.pdf", "role": "document_attachment"},
            ],
        },
    )
    state.complete_url(
        {"url": "https://a.test/two", "status": "extracted"},
        article={
            "url": "https://a.test/two",
            "final_url": "https://a.test/two",
            "assets": [
                {"url": "https://cdn.test/photo", "role": "inline_image"},
            ],
        },
    )
    state.finish("complete", 0, state.counts(), {})
    state.close()
    return output, input_path


def test_import_reused_content_copies_articles_without_mutating_source(tmp_path: Path) -> None:
    source, input_path = open_source(tmp_path)
    source_state_dir = source / "state"
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_state_dir.iterdir()
        if path.is_file()
    }

    destination = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    import_reused_content(source, destination, input_path)

    assert destination.counts()["articles"] == 2
    assert destination.is_complete("https://a.test/one")
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_state_dir.iterdir()
        if path.is_file()
    }
    assert after == before
    destination.close()


def test_reused_asset_targets_deduplicate_urls_and_keep_referrers(tmp_path: Path) -> None:
    source, input_path = open_source(tmp_path)
    destination = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    import_reused_content(source, destination, input_path)
    prepare_reused_asset_targets(destination)

    targets = {target.url: target for target in reused_asset_targets(destination)}

    assert set(targets) == {"https://cdn.test/photo", "https://a.test/report.pdf"}
    assert targets["https://cdn.test/photo"].group == "image"
    assert targets["https://cdn.test/photo"].role == "inline_image"
    assert targets["https://cdn.test/photo"].referring_pages == (
        "https://a.test/one",
        "https://a.test/two",
    )
    assert targets["https://a.test/report.pdf"].group == "document"
    destination.close()


def test_reused_asset_targets_are_read_from_materialized_sqlite_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    state = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )

    state.put_article(
        {
            "url": "https://a.test/article",
            "assets": [
                {"url": "https://cdn.test/photo.jpg", "role": "inline_image"},
            ],
        }
    )
    prepare_reused_asset_targets(state)
    targets = reused_asset_targets(state)

    assert [target.url for target in targets] == ["https://cdn.test/photo.jpg"]
    state.close()


def test_import_reused_content_rejects_a_running_source(tmp_path: Path) -> None:
    source, input_path = open_source(tmp_path)
    (source / "state" / "run.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    destination = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    with pytest.raises(ValueError, match="still running"):
        import_reused_content(source, destination, input_path)
    destination.close()


def test_import_reused_content_ignores_a_stale_source_lock(tmp_path: Path) -> None:
    source, input_path = open_source(tmp_path)
    (source / "state" / "run.lock").write_text("999999999\n", encoding="utf-8")
    destination = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )

    import_reused_content(source, destination, input_path)

    assert destination.counts()["articles"] == 2
    destination.close()


def test_import_reused_content_rejects_different_input(tmp_path: Path) -> None:
    source, _ = open_source(tmp_path)
    other_input = tmp_path / "other-seeds.txt"
    other_input.write_text("b.test\n", encoding="utf-8")
    destination = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=other_input,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    with pytest.raises(ValueError, match="input does not match"):
        import_reused_content(source, destination, other_input)
    destination.close()
