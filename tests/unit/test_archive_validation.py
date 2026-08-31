from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module

import pytest

import hust_crawler.cli as cli
from hust_crawler.cdxj import CdxjEntry
from hust_crawler.config import CrawlerConfig
from hust_crawler.models import ArchiveRef, Capture
from hust_crawler.warc_store import WarcStore


def archived_run(tmp_path):
    captured_at = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    archive_dir = tmp_path / "archives" / "2026" / "08" / "22"
    store = WarcStore(archive_dir, "example.com")
    capture = Capture(
        url="https://example.com/",
        captured_at=captured_at,
        status=200,
        reason="OK",
        headers=(("Content-Type", "text/html"),),
        payload=b"<h1>Hello</h1>",
        mime="text/html",
    )
    ref = store.write(capture)
    store.close()
    entry = CdxjEntry.from_capture(capture, ref)
    index = tmp_path / "indexes" / "runs" / "run-1.cdxj"
    index.parent.mkdir(parents=True)
    index.write_text(entry.to_line() + "\n", encoding="utf-8")
    return entry, index


def test_validate_run_accepts_matching_cdxj_and_warc(tmp_path) -> None:
    archived_run(tmp_path)
    validate_run = import_module("hust_crawler.archive_validation").validate_run

    result = validate_run(tmp_path, "run-1")

    assert result == {"run_id": "run-1", "status": "valid", "records": 1, "errors": []}


def test_validate_run_rejects_wrong_record_id(tmp_path) -> None:
    entry, index = archived_run(tmp_path)
    index.write_text(replace(entry, record_id="<urn:uuid:wrong>").to_line() + "\n")
    validate_run = import_module("hust_crawler.archive_validation").validate_run

    result = validate_run(tmp_path, "run-1")

    assert result["status"] == "invalid"
    assert result["records"] == 1
    assert any("record_id" in error for error in result["errors"])


def test_validate_archive_command_reports_real_validation(tmp_path, monkeypatch, capsys) -> None:
    archived_run(tmp_path)
    config = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset(),
        contact="test@example.invalid",
    )
    monkeypatch.setattr(cli, "_load", lambda args: config)

    result = cli.main(["validate-archive", "run-1"])

    assert result == 0
    assert '"status": "valid"' in capsys.readouterr().out


@pytest.mark.parametrize("length_delta", [-1, 1])
def test_validate_run_rejects_inexact_compressed_range(tmp_path, length_delta) -> None:
    entry, index = archived_run(tmp_path)
    archive = tmp_path / "archives" / "2026" / "08" / "22" / entry.filename
    if length_delta > 0:
        with archive.open("ab") as stream:
            stream.write(b"x")
    index.write_text(replace(entry, length=entry.length + length_delta).to_line() + "\n")
    validate_run = import_module("hust_crawler.archive_validation").validate_run

    result = validate_run(tmp_path, "run-1")

    assert result["status"] == "invalid"
    assert any("compressed range" in error for error in result["errors"])


def test_validate_run_rejects_revisit_with_unknown_reference(tmp_path) -> None:
    captured_at = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    archive_dir = tmp_path / "archives" / "2026" / "08" / "22"
    store = WarcStore(archive_dir, "example.com")
    capture = Capture(
        "https://example.com/", captured_at, 200, "OK",
        (("Content-Type", "text/html"),), b"same", "text/html",
    )
    store.write(capture, run_id="run-1")
    revisit = store.write(
        capture,
        previous=ArchiveRef(
            "example.com-00001.warc.gz", 0, 1, "urn:uuid:missing", "sha256:same", "response"
        ),
        run_id="run-1",
    )
    store.close()
    index = tmp_path / "indexes" / "runs" / "run-1.cdxj"
    index.parent.mkdir(parents=True)
    index.write_text(CdxjEntry.from_capture(capture, revisit).to_line() + "\n", encoding="utf-8")

    result = import_module("hust_crawler.archive_validation").validate_run(tmp_path, "run-1")

    assert result["status"] == "invalid"
    assert any("reference" in error for error in result["errors"])
