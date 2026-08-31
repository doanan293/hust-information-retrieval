import json

from scrapy import Request
from scrapy.http import Response

import hust_crawler.coverage as coverage


def test_tracker_records_only_requests_that_reach_downloader_and_closes_drops(tmp_path) -> None:
    tracker_class = getattr(coverage, "LifecycleTracker", None)
    assert tracker_class is not None
    tracker = tracker_class(tmp_path / "lifecycle.jsonl")
    accepted = Request(
        "https://example.com/page",
        meta={"discovery_source": "html", "parent_url": "https://example.com/"},
    )
    duplicate = Request(
        "https://example.com/duplicate",
        meta={"discovery_source": "html", "parent_url": "https://example.com/"},
    )

    tracker.request_reached_downloader(accepted)
    tracker.request_dropped(duplicate)

    events = [json.loads(line) for line in (tmp_path / "lifecycle.jsonl").read_text().splitlines()]
    assert [(item["url"], item["phase"]) for item in events] == [
        ("https://example.com/page", "scheduled"),
        ("https://example.com/duplicate", "deduplicated"),
    ]


def test_tracker_closes_redirect_source_before_target_request(tmp_path) -> None:
    tracker_class = getattr(coverage, "LifecycleTracker", None)
    assert tracker_class is not None
    tracker = tracker_class(tmp_path / "lifecycle.jsonl")
    request = Request("https://example.com/old", meta={"discovery_source": "root"})
    response = Response(
        request.url,
        status=302,
        headers={b"Location": b"https://example.com/new"},
        request=request,
    )

    tracker.response_downloaded(response, request)

    event = json.loads((tmp_path / "lifecycle.jsonl").read_text())
    assert event["phase"] == "redirected"
    assert event["reason"] == "https://example.com/new"
