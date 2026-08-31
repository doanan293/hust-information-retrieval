import json
from pathlib import Path
from types import SimpleNamespace

import hust_crawler.cli as cli
from hust_crawler.cdxj import CdxjEntry
from hust_crawler.cli import main
from hust_crawler.config import CrawlerConfig
from hust_crawler.run_store import RunPaths, RunStore


class LifecycleCrawlerProcess:
    finish_reason = "finished"
    settings = None

    def __init__(self, settings) -> None:
        type(self).settings = settings

    def create_crawler(self, spider_class):
        stats = SimpleNamespace(get_value=lambda key: self.finish_reason if key == "finish_reason" else None)
        return SimpleNamespace(stats=stats)

    def crawl(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def crawler_config(data_dir: Path) -> CrawlerConfig:
    return CrawlerConfig(
        data_dir=data_dir,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset(),
        contact="test@example.invalid",
    )


def test_effective_settings_match_config(tmp_path) -> None:
    config = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset({"example.com"}),
        contact="test@example.invalid",
        concurrent_requests=7,
        concurrent_per_host=2,
        throttle_start_seconds=0.5,
        throttle_max_seconds=15,
        max_response_bytes=123456,
    )

    settings = cli._effective_settings(config, "run-1", RunPaths.for_run(tmp_path, "run-1"))

    assert settings.getint("CONCURRENT_REQUESTS") == 7
    assert settings.getint("CONCURRENT_REQUESTS_PER_DOMAIN") == 2
    assert settings.getfloat("AUTOTHROTTLE_START_DELAY") == 0.5
    assert settings.getint("DOWNLOAD_MAXSIZE") == 123456
    assert settings.getbool("ROBOTSTXT_OBEY") is False
    assert settings.get("DOWNLOAD_HANDLERS")["https"].endswith("ScrapyPlaywrightDownloadHandler")
    assert settings.getlist("CRAWL_PLAYWRIGHT_HOSTS") == ["example.com"]


def test_validate_config_reports_domain_count(capsys) -> None:
    assert main(["validate-config"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hostname_count"] == 159


def test_pilot_rejects_zero_page_limit() -> None:
    assert main(["pilot", "--max-pages-per-host", "0"]) == 2


def test_pilot_defaults_to_ten_requests_per_host(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    config = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset(),
        contact="ops@example.org",
    )

    def fake_run(config, mode, run_id, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_load", lambda args: config)
    monkeypatch.setattr(cli, "_run_crawl", fake_run)

    assert cli.main([
        "pilot",
        "run-1",
        "--max-pages-per-host", "1",
        "--time-limit-seconds", "10",
    ]) == 0
    assert captured["pilot"] is True
    assert captured["max_requests_per_host"] == 10


def test_pilot_rejects_zero_request_budget(tmp_path, monkeypatch, capsys) -> None:
    config = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset(),
        contact="ops@example.org",
    )
    monkeypatch.setattr(cli, "_load", lambda args: config)

    assert cli.main([
        "pilot",
        "run-1",
        "--max-pages-per-host", "1",
        "--max-requests-per-host", "0",
        "--time-limit-seconds", "10",
    ]) == 2
    assert "max-requests-per-host must be positive" in capsys.readouterr().err


def test_pilot_rejects_placeholder_contact(monkeypatch, capsys) -> None:
    config = crawler_config(Path("data"))
    monkeypatch.setattr(cli, "_load", lambda args: config)

    assert cli.main([
        "pilot",
        "--max-pages-per-host", "1",
        "--time-limit-seconds", "10",
    ]) == 2
    assert "operator contact" in capsys.readouterr().err


def test_validate_config_allows_placeholder_contact(monkeypatch, capsys) -> None:
    config = crawler_config(Path("data"))
    monkeypatch.setattr(cli, "_load", lambda args: config)

    assert cli.main(["validate-config"]) == 0
    assert json.loads(capsys.readouterr().out)["hostname_count"] == 1


def test_completed_crawl_publishes_pipeline_urls(tmp_path, monkeypatch) -> None:
    class FixtureCrawlerProcess:
        def __init__(self, settings) -> None:
            self.settings = settings

        def create_crawler(self, spider_class):
            stats = SimpleNamespace(
                get_value=lambda key: "finished" if key == "finish_reason" else None
            )
            return SimpleNamespace(stats=stats)

        def crawl(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            entry = CdxjEntry(
                url="https://example.com/page",
                timestamp="20260101000000",
                status=200,
                mime="text/html",
                digest="sha256:example",
                filename="example.warc.gz",
                offset=0,
                length=10,
                record_id="urn:uuid:example",
                record_type="response",
            )
            run_index = (
                Path(self.settings.get("CRAWL_DATA_DIR"))
                / "indexes"
                / "runs"
                / f"{self.settings.get('CRAWL_RUN_ID')}.cdxj"
            )
            run_index.parent.mkdir(parents=True)
            run_index.write_text(entry.to_line() + "\n", encoding="utf-8")

    monkeypatch.setattr(cli, "CrawlerProcess", FixtureCrawlerProcess)
    monkeypatch.setattr(cli, "validate_run", lambda *args: {"status": "valid"})
    class CompleteCoverage:
        status = "complete"
        def to_dict(self): return {"status": self.status}
    monkeypatch.setattr(cli.CoverageLedger, "load", lambda *args: type("L", (), {"summarize": lambda *a, **k: CompleteCoverage()})())
    config = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"example.com"}),
        playwright_hosts=frozenset(),
        contact="test@example.invalid",
    )

    assert cli._run_crawl(config, "crawl", "run-1") == 0
    assert (tmp_path / "crawled_urls.txt").read_text(encoding="utf-8") == (
        "https://example.com/page\n"
    )


def test_pilot_applies_time_limit_and_records_timeout(tmp_path, monkeypatch) -> None:
    LifecycleCrawlerProcess.finish_reason = "closespider_timeout"
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)

    result = cli._run_crawl(
        crawler_config(tmp_path),
        "crawl",
        "run-timeout",
        max_pages_per_host=2,
        time_limit_seconds=15,
    )

    manifest = json.loads((tmp_path / "runs" / "run-timeout" / "manifest.json").read_text())
    assert result == 130
    assert LifecycleCrawlerProcess.settings.getint("CLOSESPIDER_TIMEOUT") == 15
    assert manifest["status"] == "interrupted"


def test_shutdown_finish_reason_marks_run_interrupted(tmp_path, monkeypatch) -> None:
    LifecycleCrawlerProcess.finish_reason = "shutdown"
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)

    result = cli._run_crawl(crawler_config(tmp_path), "crawl", "run-interrupted")

    manifest = json.loads((tmp_path / "runs" / "run-interrupted" / "manifest.json").read_text())
    assert result == 130
    assert manifest["status"] == "interrupted"


def test_resume_reuses_existing_run_and_job_state(tmp_path, monkeypatch) -> None:
    config = crawler_config(tmp_path)
    LifecycleCrawlerProcess.finish_reason = "shutdown"
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)
    assert cli._run_crawl(config, "crawl", "run-resume") == 130

    LifecycleCrawlerProcess.finish_reason = "finished"
    monkeypatch.setattr(cli, "validate_run", lambda *args: {"status": "valid"})
    class CompleteCoverage:
        status = "complete"
        def to_dict(self): return {"status": self.status}
    monkeypatch.setattr(cli.CoverageLedger, "load", lambda *args: type("L", (), {"summarize": lambda *a, **k: CompleteCoverage()})())
    result = cli._run_crawl(config, "crawl", "run-resume", resume=True)

    manifest = json.loads((tmp_path / "runs" / "run-resume" / "manifest.json").read_text())
    assert result == 0
    assert manifest["status"] == "complete"
    assert manifest["resumed_at"]
    assert LifecycleCrawlerProcess.settings.get("JOBDIR") == str(
        tmp_path / "state" / "run-resume"
    )


def test_resume_rejects_missing_run_without_uncaught_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)

    result = cli._run_crawl(
        crawler_config(tmp_path), "crawl", "missing-run", resume=True
    )

    assert result == 2


def test_resume_rejects_config_drift(tmp_path, monkeypatch) -> None:
    original = crawler_config(tmp_path)
    LifecycleCrawlerProcess.finish_reason = "shutdown"
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)
    assert cli._run_crawl(original, "crawl", "run-drift") == 130
    changed = CrawlerConfig(
        data_dir=tmp_path,
        hostnames=frozenset({"changed.example"}),
        playwright_hosts=frozenset(),
        contact="test@example.invalid",
    )

    result = cli._run_crawl(changed, "crawl", "run-drift", resume=True)

    manifest = json.loads((tmp_path / "runs" / "run-drift" / "manifest.json").read_text())
    assert result == 2
    assert manifest["status"] == "interrupted"


def test_crawl_refuses_lock_contention_without_orphan_run(tmp_path, monkeypatch) -> None:
    owner = RunStore(RunPaths.create(tmp_path, "owner"))
    owner.acquire_lock()
    monkeypatch.setattr(cli, "CrawlerProcess", LifecycleCrawlerProcess)
    try:
        result = cli._run_crawl(crawler_config(tmp_path), "crawl", "contender")
    finally:
        owner.release_lock()

    assert result == 1
    assert not (tmp_path / "runs" / "contender").exists()
