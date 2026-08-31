import json
import warnings
from datetime import datetime, timezone

from scrapy.crawler import Crawler
from scrapy.exceptions import ScrapyDeprecationWarning
from scrapy.pipelines import ItemPipelineManager
from scrapy.settings import Settings
import pytest

import hust_crawler.models as models
import hust_crawler.pipelines as pipelines
from hust_crawler.cdxj import CdxjEntry
from hust_crawler.models import Capture, RejectedUrl
from hust_crawler.pipelines import CapturePipeline, is_supported_content
from hust_crawler.spiders.public_sites import PublicSitesSpider
from hust_crawler.warc_store import WarcStore


def test_supported_content_types() -> None:
    assert is_supported_content("text/html", "https://example.com/")
    assert is_supported_content("application/pdf", "https://example.com/a.pdf")
    assert is_supported_content("application/octet-stream", "https://example.com/a.docx")


def test_supported_content_includes_media_and_xml() -> None:
    assert is_supported_content("image/png", "https://example.com/a.png")
    assert is_supported_content("video/mp4", "https://example.com/a.mp4")
    assert is_supported_content("application/xml", "https://example.com/a.xml")


def test_render_only_assets_are_not_archived() -> None:
    assert not is_supported_content("text/css", "https://example.com/site.css")
    assert not is_supported_content("application/javascript", "https://example.com/app.js")
    assert not is_supported_content("font/woff2", "https://example.com/font.woff2")


def test_capture_pipeline_logs_archived_fetch(tmp_path) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-1", allowed_hosts=frozenset({"example.com"}))
    capture = Capture(
        url="https://example.com/",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status=200,
        reason="OK",
        headers=(("Content-Type", "text/html"),),
        payload=b"<h1>Hello</h1>",
        mime="text/html",
    )

    pipeline.process_item(capture)
    pipeline.close_spider()

    event = json.loads((tmp_path / "runs" / "run-1" / "fetches.jsonl").read_text())
    assert event["url"] == "https://example.com/"
    assert event["status"] == 200
    assert event["record_type"] == "response"
    assert (tmp_path / "archives" / "2026" / "01" / "01" / event["filename"]).is_file()
    lifecycle = [
        json.loads(line)
        for line in (tmp_path / "runs" / "run-1" / "lifecycle.jsonl").read_text().splitlines()
    ]
    assert [event["phase"] for event in lifecycle] == ["fetched", "archived"]


def test_capture_pipeline_logs_rejected_url(tmp_path) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-1", allowed_hosts=frozenset({"example.com"}))

    pipeline.process_item(
        RejectedUrl(
            source_url="https://example.com/",
            discovered_url="https://outside.test/",
            reason="host_out_of_scope",
        )
    )

    event = json.loads((tmp_path / "runs" / "run-1" / "rejected-urls.jsonl").read_text())
    assert event == {
        "discovered_url": "https://outside.test/",
        "reason": "host_out_of_scope",
        "source_url": "https://example.com/",
    }


def test_request_budget_rejection_has_distinct_terminal_phase(tmp_path) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-1", allowed_hosts=frozenset({"example.com"}))

    pipeline.process_item(RejectedUrl("https://example.com/", "https://example.com/later", "request_budget"))

    lifecycle = json.loads((tmp_path / "runs" / "run-1" / "lifecycle.jsonl").read_text())
    assert lifecycle["phase"] == "budget_rejected"


def test_pipeline_rejects_external_capture_without_archive(tmp_path) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    pipeline = CapturePipeline(
        tmp_path,
        "run-1",
        allowed_hosts=frozenset({"example.com"}),
    )
    capture = Capture(
        "https://outside.test/image.png",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        200,
        "OK",
        (("Content-Type", "image/png"),),
        b"png",
        "image/png",
        parent_url="https://example.com/",
        target_kind="media",
    )

    result = pipeline.process_item(capture)
    pipeline.close_spider()

    assert result == RejectedUrl(
        "https://example.com/",
        "https://outside.test/image.png",
        "host_out_of_scope",
    )
    assert not list((tmp_path / "archives").rglob("*.warc.gz"))
    assert not (tmp_path / "indexes" / "runs" / "run-1.cdxj").exists()


def test_capture_pipeline_uses_current_scrapy_method_signatures(tmp_path) -> None:
    settings = Settings(
        {
            "ITEM_PIPELINES": {"hust_crawler.pipelines.CapturePipeline": 300},
            "CRAWL_DATA_DIR": str(tmp_path),
            "CRAWL_RUN_ID": "run-1",
        }
    )
    crawler = Crawler(PublicSitesSpider, settings)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ItemPipelineManager.from_crawler(crawler)

    assert not [warning for warning in caught if warning.category is ScrapyDeprecationWarning]


def test_capture_pipeline_logs_download_error(tmp_path) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-1", allowed_hosts=frozenset({"broken.example"}))

    pipeline.process_item(
        models.FetchError(
            url="https://broken.example/",
            error_type="DownloadTimeoutError",
            message="request timed out",
        )
    )

    error = json.loads((tmp_path / "runs" / "run-1" / "errors.jsonl").read_text())
    fetch = json.loads((tmp_path / "runs" / "run-1" / "fetches.jsonl").read_text())
    assert error == {
        "error": "request timed out",
        "type": "DownloadTimeoutError",
        "url": "https://broken.example/",
    }
    assert fetch == {
        "error": "DownloadTimeoutError",
        "status": "error",
        "url": "https://broken.example/",
    }


def test_changed_payload_creates_response_not_revisit(tmp_path) -> None:
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    initial = Capture(
        url="https://example.com/",
        captured_at=captured_at,
        status=200,
        reason="OK",
        headers=(("Content-Type", "text/html"),),
        payload=b"old",
        mime="text/html",
    )
    store = WarcStore(tmp_path / "archives" / "2026" / "01" / "01", "example.com")
    ref = store.write(initial, run_id="run-old")
    store.close()
    latest = tmp_path / "indexes" / "latest.cdxj"
    latest.parent.mkdir(parents=True)
    latest.write_text(CdxjEntry.from_capture(initial, ref).to_line() + "\n", encoding="utf-8")

    (tmp_path / "runs" / "run-new").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-new", allowed_hosts=frozenset({"example.com"}))
    pipeline.process_item(initial.__class__(
        initial.url, initial.captured_at, initial.status, initial.reason,
        initial.headers, b"new", initial.mime,
    ))
    pipeline.close_spider()

    entry = CdxjEntry.from_line((tmp_path / "indexes" / "runs" / "run-new.cdxj").read_text().strip())
    assert entry.record_type == "response"


def test_identical_payload_creates_revisit(tmp_path) -> None:
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    initial = Capture(
        "https://example.com/", captured_at, 200, "OK",
        (("Content-Type", "text/html"),), b"same", "text/html",
    )
    store = WarcStore(tmp_path / "archives" / "2026" / "01" / "01", "example.com")
    ref = store.write(initial, run_id="run-old")
    store.close()
    latest = tmp_path / "indexes" / "latest.cdxj"
    latest.parent.mkdir(parents=True)
    latest.write_text(CdxjEntry.from_capture(initial, ref).to_line() + "\n", encoding="utf-8")

    (tmp_path / "runs" / "run-new").mkdir(parents=True)
    pipeline = CapturePipeline(tmp_path, "run-new", allowed_hosts=frozenset({"example.com"}))
    pipeline.process_item(initial)
    pipeline.close_spider()

    entry = CdxjEntry.from_line((tmp_path / "indexes" / "runs" / "run-new.cdxj").read_text().strip())
    assert entry.record_type == "revisit"


def test_pipeline_checks_capacity_before_opening_archive(tmp_path, monkeypatch) -> None:
    (tmp_path / "runs" / "run-1").mkdir(parents=True)
    monkeypatch.setattr("hust_crawler.pipelines.has_capacity", lambda *args: False)
    pipeline = CapturePipeline(
        tmp_path,
        "run-1",
        allowed_hosts=frozenset({"example.com"}),
        min_free_bytes=10,
        min_free_percent=10,
    )
    capture = Capture(
        "https://example.com/", datetime(2026, 1, 1, tzinfo=timezone.utc), 200, "OK",
        (("Content-Type", "text/html"),), b"x", "text/html",
    )

    with pytest.raises(pipelines.StorageCapacityError):
        pipeline.process_item(capture)
    assert not list((tmp_path / "archives").rglob("*.warc.gz"))
