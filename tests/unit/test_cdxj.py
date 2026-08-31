from datetime import datetime, timezone

import hust_crawler.cdxj as cdxj
from hust_crawler.cdxj import CdxjEntry, load_latest, merge_indexes, recover_run_index
from hust_crawler.models import ArchiveRef, Capture
from hust_crawler.warc_store import WarcStore


def test_cdxj_round_trip_and_latest_selection(tmp_path):
    capture = Capture("https://example.com/", datetime(2026, 1, 1, tzinfo=timezone.utc), 200, "OK", (), b"x", "text/html")
    entry = CdxjEntry.from_capture(capture, ArchiveRef("a.warc.gz", 1, 2, "urn:uuid:x", "sha256:abc", "response"))
    first = tmp_path / "first.cdxj"
    second = tmp_path / "second.cdxj"
    first.write_text(entry.to_line() + "\n", encoding="utf-8")
    newer_capture = Capture("https://example.com/", datetime(2026, 1, 2, tzinfo=timezone.utc), 200, "OK", (), b"y", "text/html")
    newer = CdxjEntry.from_capture(newer_capture, ArchiveRef("b.warc.gz", 1, 2, "urn:uuid:y", "sha256:def", "response"))
    second.write_text(newer.to_line() + "\n", encoding="utf-8")
    destination = tmp_path / "latest.cdxj"
    merge_indexes(first, second, destination)
    assert load_latest(destination)[capture.url].digest == "sha256:def"


def test_finalize_indexes_reads_pipeline_index_and_writes_plain_url_list(tmp_path):
    indexes = tmp_path / "indexes"
    run_indexes = indexes / "runs"
    run_indexes.mkdir(parents=True)
    previous = CdxjEntry(
        url="https://b.example/",
        timestamp="20260101000000",
        status=200,
        mime="text/html",
        digest="sha256:b",
        filename="b.warc.gz",
        offset=0,
        length=10,
        record_id="urn:uuid:b",
        record_type="response",
    )
    current = CdxjEntry(
        url="https://a.example/document.pdf",
        timestamp="20260102000000",
        status=200,
        mime="application/pdf",
        digest="sha256:a",
        filename="a.warc.gz",
        offset=0,
        length=20,
        record_id="urn:uuid:a",
        record_type="response",
    )
    (indexes / "latest.cdxj").write_text(previous.to_line() + "\n", encoding="utf-8")
    (run_indexes / "run-1.cdxj").write_text(current.to_line() + "\n", encoding="utf-8")

    cdxj.finalize_indexes(tmp_path, "run-1")

    assert set(load_latest(indexes / "latest.cdxj")) == {
        "https://a.example/document.pdf",
        "https://b.example/",
    }
    assert (tmp_path / "crawled_urls.txt").read_text(encoding="utf-8") == (
        "https://a.example/document.pdf\nhttps://b.example/\n"
    )


def test_finalize_indexes_recovers_all_runs_when_latest_is_empty(tmp_path):
    run_indexes = tmp_path / "indexes" / "runs"
    run_indexes.mkdir(parents=True)
    older = CdxjEntry(
        url="https://example.com/shared",
        timestamp="20260101000000",
        status=200,
        mime="text/html",
        digest="sha256:old",
        filename="old.warc.gz",
        offset=0,
        length=10,
        record_id="urn:uuid:old",
        record_type="response",
    )
    newer = CdxjEntry(
        url="https://example.com/shared",
        timestamp="20260102000000",
        status=200,
        mime="text/html",
        digest="sha256:new",
        filename="new.warc.gz",
        offset=0,
        length=10,
        record_id="urn:uuid:new",
        record_type="response",
    )
    historical = CdxjEntry(
        url="https://example.com/historical",
        timestamp="20260101000000",
        status=200,
        mime="application/pdf",
        digest="sha256:historical",
        filename="historical.warc.gz",
        offset=10,
        length=20,
        record_id="urn:uuid:historical",
        record_type="response",
    )
    (run_indexes / "run-1.cdxj").write_text(
        older.to_line() + "\n" + historical.to_line() + "\n", encoding="utf-8"
    )
    (run_indexes / "run-2.cdxj").write_text(newer.to_line() + "\n", encoding="utf-8")
    (tmp_path / "indexes" / "latest.cdxj").write_text("", encoding="utf-8")

    cdxj.finalize_indexes(tmp_path, "run-2")

    assert load_latest(tmp_path / "indexes" / "latest.cdxj")[older.url].digest == "sha256:new"
    assert (tmp_path / "crawled_urls.txt").read_text(encoding="utf-8") == (
        "https://example.com/historical\nhttps://example.com/shared\n"
    )


def test_recover_run_index_restores_missing_completed_record(tmp_path):
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    capture = Capture(
        "https://example.com/", captured_at, 200, "OK",
        (("Content-Type", "text/html"),), b"payload", "text/html",
    )
    store = WarcStore(tmp_path / "archives" / "2026" / "01" / "01", "example.com")
    ref = store.write(capture, run_id="run-1")
    store.close()

    assert recover_run_index(tmp_path, "run-1") == 1
    recovered = CdxjEntry.from_line(
        (tmp_path / "indexes" / "runs" / "run-1.cdxj").read_text().strip()
    )
    assert (recovered.url, recovered.record_id) == (capture.url, ref.record_id)
