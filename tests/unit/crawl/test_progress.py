import io
from unittest.mock import MagicMock

from hust_crawler.crawl.progress import CrawlProgressReporter, EtaEstimator


def test_eta_is_unknown_while_frontier_is_expanding() -> None:
    eta = EtaEstimator(stable_samples=3)
    eta.observe(at=10, completed=10, queued=20, discovered=30)
    eta.observe(at=20, completed=20, queued=25, discovered=45)
    assert eta.render() == "unknown (discovering)"


def test_eta_uses_recent_rate_after_stable_frontier() -> None:
    eta = EtaEstimator(stable_samples=3)
    eta.observe(at=10, completed=10, queued=30, discovered=40)
    eta.observe(at=20, completed=20, queued=20, discovered=40)
    eta.observe(at=30, completed=30, queued=10, discovered=40)
    eta.observe(at=40, completed=40, queued=0, discovered=40)
    assert eta.render() == "0s"


def test_progress_reporter_line_contains_all_metrics() -> None:
    crawler = MagicMock()
    crawler.settings.get.return_value = None
    stats_dict = {
        "response_received_count": 50,
        "scheduler/enqueued": 70,
        "scheduler/dequeued": 50,
        "hust/adaptive/http_active": 4,
        "hust/adaptive/browser_active": 1,
        "hust/resources/cpu_percent": 25.0,
        "hust/resources/ram_percent": 45.0,
    }
    crawler.stats.get_value.side_effect = lambda k, d=0: stats_dict.get(k, d)
    crawler.stats.get.side_effect = lambda k, d=0: stats_dict.get(k, d)

    output = io.StringIO()
    reporter = CrawlProgressReporter(
        crawler,
        output=output,
        clock=lambda: 100.0,
    )
    reporter.started = 90.0

    line = reporter.report()
    assert "done=50" in line
    assert "queued=20" in line
    assert "rate=" in line
    assert "cpu=25%" in line
    assert "ram=45%" in line
    assert "eta=" in line


def test_progress_reporter_metrics_track_peak_queue() -> None:
    crawler = MagicMock()
    crawler.settings.get.return_value = None
    stats_dict = {
        "response_received_count": 10,
        "scheduler/enqueued": 50,
        "scheduler/dequeued": 10,
    }
    crawler.stats.get_value.side_effect = lambda k, d=0: stats_dict.get(k, d)
    crawler.stats.get.side_effect = lambda k, d=0: stats_dict.get(k, d)
    reporter = CrawlProgressReporter(crawler, clock=lambda: 10.0)
    reporter.started = 0.0
    reporter.report()
    # Queue is 40
    metrics = reporter.metrics()
    assert metrics["peak_queue"] == 40
    assert metrics["recent_rate"] == 1.0

    # Queue drops to 10
    stats_dict["scheduler/dequeued"] = 40
    stats_dict["response_received_count"] = 40
    reporter.report()
    metrics2 = reporter.metrics()
    assert metrics2["peak_queue"] == 40
    assert metrics2["recent_rate"] == 4.0


def test_progress_reports_downloader_queue_and_retry_waits() -> None:
    from types import SimpleNamespace

    output = io.StringIO()
    slot = SimpleNamespace(
        active={1, 2, 3}, transferring={1}, queue=[2, 3], concurrency=2, delay=17.0,
    )
    stats = {"hust/retry/count": 4, "hust/retry/waiting": 2}
    crawler = SimpleNamespace(
        stats=SimpleNamespace(get_value=lambda key, default=0: stats.get(key, default)),
        engine=SimpleNamespace(downloader=SimpleNamespace(
            active={1, 2, 3, 4, 5}, slots={"hust.edu.vn": slot},
        )),
    )
    CrawlProgressReporter(crawler, output=output).report()
    log = output.getvalue()
    assert "active=5 | transferring=1 | slot_waiting=2 | outside_slots=2" in log
    assert "retries=4 | retry_waiting=2" in log
    assert "host=hust.edu.vn" in log
    assert "concurrency=2 | delay=17.00s" in log
