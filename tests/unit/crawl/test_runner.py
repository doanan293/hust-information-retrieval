import json
from pathlib import Path

import pytest

from hust_crawler.config import CrawlerConfig, semantic_config_snapshot
from hust_crawler.crawl import runner
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.frontier import FrontierPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.profiles import resolve_runtime_tuning
from hust_crawler.crawl.runner import (
    TRUNCATION_REASONS,
    finish_reason_to_exit,
    run_crawl,
)
from hust_crawler.crawl.reuse_spider import ReusedAssetSpider
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.sitemaps import SitemapDiscoveryCoordinator
from hust_crawler.crawl.spider import UnifiedSpider
from hust_crawler.crawl.state import CrawlState


def test_frontier_limits_are_truncation_reasons() -> None:
    assert {"query_variant_limit", "host_url_limit", "total_url_limit"} <= TRUNCATION_REASONS


def test_finished_frontier_limit_returns_truncated() -> None:
    assert finish_reason_to_exit(
        "finished", queued=0, failed=0, truncated=1
    ) == ("truncated", 4)


def test_queued_nonfinished_run_is_interrupted() -> None:
    assert finish_reason_to_exit(
        "shutdown", queued=2, failed=0, truncated=0
    ) == ("interrupted", 130)


def test_shutdown_with_queue_is_interrupted_not_complete() -> None:
    assert finish_reason_to_exit("shutdown", queued=12, failed=0, truncated=0) == (
        "interrupted",
        130,
    )


def test_natural_finish_with_truncation_has_exit_four() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=0, truncated=1) == (
        "truncated",
        4,
    )


def test_natural_finish_with_failures_has_exit_three() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=2, truncated=0) == (
        "complete_with_failures",
        3,
    )


def test_clean_finish_has_exit_zero() -> None:
    assert finish_reason_to_exit("finished", queued=0, failed=0, truncated=0) == (
        "complete",
        0,
    )


def capture_runner_dependencies(monkeypatch) -> dict[str, object]:
    observed: dict[str, object] = {}
    original_open = CrawlState.open

    def fake_open(cls, root: Path, **kwargs) -> CrawlState:
        observed["root"] = root
        observed.update(kwargs)
        return original_open(root, **kwargs)

    monkeypatch.setattr(CrawlState, "open", classmethod(fake_open))

    original_create = runner.CrawlerProcess.create_crawler

    def fake_create(self, spider_cls, *args, **kwargs):
        if isinstance(spider_cls, type):
            observed["spider"] = spider_cls
        observed["jobdir"] = self.settings.get("JOBDIR")
        return original_create(self, spider_cls, *args, **kwargs)

    monkeypatch.setattr(runner.CrawlerProcess, "create_crawler", fake_create)

    original_crawl = runner.CrawlerProcess.crawl

    def fake_crawl(self, crawler_or_spidercls, *args, **kwargs):
        if "targets" in kwargs:
            kwargs["targets"] = tuple(kwargs["targets"])
        observed["crawl_kwargs"] = kwargs
        return original_crawl(self, crawler_or_spidercls, *args, **kwargs)

    monkeypatch.setattr(runner.CrawlerProcess, "crawl", fake_crawl)
    monkeypatch.setattr(runner.CrawlerProcess, "start", lambda self: None)
    return observed


def run_unified_for_test(
    tmp_path: Path,
    *,
    resume: bool = False,
    retry_failed: bool = False,
    retry_truncated: bool = False,
    retry_access_gates: bool = False,
    retry_policy_skips: bool = False,
) -> int:
    urls_file = tmp_path / "seeds.txt"
    urls_file.write_text("https://a.test/article\n", encoding="utf-8")
    seeds = SeedSet(
        recursive_hostnames=frozenset({"a.test"}),
        exact_urls=("https://a.test/article",),
        invalid=(),
    )
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    options = CrawlOptions()
    tuning = resolve_runtime_tuning(profile="safe-fast")
    output = tmp_path / "crawl-output"
    return run_crawl(
        input_path=urls_file,
        output=output,
        config=config,
        seeds=seeds,
        options=options,
        tuning=tuning,
        resume=resume,
        retry_failed=retry_failed,
        retry_truncated=retry_truncated,
        retry_access_gates=retry_access_gates,
        retry_policy_skips=retry_policy_skips,
    )


def test_crawl_runner_policy_recovery_is_explicit_and_resumable(monkeypatch, tmp_path: Path) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    assert run_unified_for_test(tmp_path) == 0

    observed.clear()
    assert run_unified_for_test(tmp_path, resume=True, retry_policy_skips=True) == 0
    assert observed["allow_policy_migration"] is True
    assert {
        row["url"] for row in observed["crawl_kwargs"]["resume_records"]
    } == {"https://a.test/"}


def test_crawl_runner_uses_crawl_phase_and_unified_spider(
    monkeypatch, tmp_path: Path
) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    exit_code = run_unified_for_test(tmp_path)
    assert exit_code == 0
    assert observed["phase"] == "crawl"
    assert observed["spider"] is UnifiedSpider
    crawl_kwargs = observed["crawl_kwargs"]
    assert isinstance(crawl_kwargs["seeds"], SeedSet)
    assert isinstance(crawl_kwargs["frontier"], FrontierPolicy)
    assert isinstance(crawl_kwargs["policy"], AssetPolicy)
    assert isinstance(crawl_kwargs["coordinator"], SitemapDiscoveryCoordinator)
    assert crawl_kwargs["coordinator"].strategy == "hybrid-unified"

    manifest_path = tmp_path / "crawl-output" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["discovery"]["strategy"] == "hybrid-unified"
    assert "frontier" in manifest
    assert "frontier" in manifest["metrics"]


def test_crawl_runner_reuses_articles_and_selects_asset_only_spider(
    monkeypatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    lightweight_options = CrawlOptions()
    source = tmp_path / "crawl"
    source_state = CrawlState.open(
        source / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={
            **semantic_config_snapshot(config),
            **lightweight_options.semantic_snapshot(seeds),
        },
        runtime_config={},
    )
    source_state.complete_url(
        {"url": "https://a.test/article", "status": "extracted"},
        article={
            "url": "https://a.test/article",
            "assets": [
                {"url": "https://cdn.test/photo", "role": "inline_image"},
            ],
        },
    )
    source_state.finish("complete", 0, source_state.counts(), {})
    source_state.close()

    observed = capture_runner_dependencies(monkeypatch)
    exit_code = run_crawl(
        input_path=input_path,
        output=tmp_path / "crawl-all",
        config=config,
        seeds=seeds,
        options=CrawlOptions(assets="all"),
        tuning=resolve_runtime_tuning(profile="safe-fast"),
        reuse_content_from=source,
    )

    assert exit_code == 0
    assert observed["spider"] is ReusedAssetSpider
    targets = observed["crawl_kwargs"]["targets"]
    assert [target.url for target in targets] == ["https://cdn.test/photo"]
    article_lines = (tmp_path / "crawl-all" / "articles.jsonl").read_text().splitlines()
    assert len(article_lines) == 1


def test_crawl_runner_rejects_missing_reuse_source_before_creating_output(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    output = tmp_path / "crawl-all"

    with pytest.raises(ValueError, match="not a reusable crawl output"):
        run_crawl(
            input_path=input_path,
            output=output,
            config=CrawlerConfig(
                hostnames=seeds.allowed_hostnames, contact="ops@example.com"
            ),
            seeds=seeds,
            options=CrawlOptions(assets="all"),
            tuning=resolve_runtime_tuning(profile="safe-fast"),
            reuse_content_from=tmp_path / "missing-crawl",
        )

    assert not output.exists()


def test_crawl_runner_rejects_existing_reuse_output_without_touching_it(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    output = tmp_path / "crawl-all"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("user data", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())

    with pytest.raises(FileExistsError, match="reuse output already exists"):
        run_crawl(
            input_path=input_path,
            output=output,
            config=CrawlerConfig(
                hostnames=seeds.allowed_hostnames, contact="ops@example.com"
            ),
            seeds=seeds,
            options=CrawlOptions(assets="all"),
            tuning=resolve_runtime_tuning(profile="safe-fast"),
            reuse_content_from=tmp_path / "crawl",
        )

    assert marker.read_text(encoding="utf-8") == "user data"


def test_crawl_runner_cleans_destination_after_corrupt_source_database(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    source = tmp_path / "crawl"
    source_state = CrawlState.open(
        source / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={
            **semantic_config_snapshot(
                CrawlerConfig(
                    hostnames=seeds.allowed_hostnames, contact="ops@example.com"
                )
            ),
            **CrawlOptions().semantic_snapshot(seeds),
        },
        runtime_config={},
    )
    source_state.finish("complete", 0, source_state.counts(), {})
    source_state.close()
    (source / "state" / "index.sqlite3").write_bytes(b"not a sqlite database")
    output = tmp_path / "crawl-all"

    with pytest.raises(ValueError, match="could not import reusable crawl"):
        run_crawl(
            input_path=input_path,
            output=output,
            config=CrawlerConfig(
                hostnames=seeds.allowed_hostnames, contact="ops@example.com"
            ),
            seeds=seeds,
            options=CrawlOptions(assets="all"),
            tuning=resolve_runtime_tuning(profile="safe-fast"),
            reuse_content_from=source,
        )

    assert not output.exists()


def test_crawl_runner_cleans_reuse_destination_when_scrapy_setup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    source = tmp_path / "crawl"
    source_state = CrawlState.open(
        source / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={
            **semantic_config_snapshot(config),
            **CrawlOptions().semantic_snapshot(seeds),
        },
        runtime_config={},
    )
    source_state.finish("complete", 0, source_state.counts(), {})
    source_state.close()
    output = tmp_path / "crawl-all"

    def fail_process(*args, **kwargs):
        raise RuntimeError("scrapy setup failed")

    monkeypatch.setattr(runner, "CrawlerProcess", fail_process)
    with pytest.raises(RuntimeError, match="scrapy setup failed"):
        run_crawl(
            input_path=input_path,
            output=output,
            config=config,
            seeds=seeds,
            options=CrawlOptions(assets="all"),
            tuning=resolve_runtime_tuning(profile="safe-fast"),
            reuse_content_from=source,
        )

    assert not output.exists()


def test_crawl_runner_rejects_malformed_reused_article_before_scrapy_starts(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    seeds = SeedSet(frozenset({"a.test"}), (), ())
    config = CrawlerConfig(hostnames=seeds.allowed_hostnames, contact="ops@example.com")
    source = tmp_path / "crawl"
    source_state = CrawlState.open(
        source / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={
            **semantic_config_snapshot(config),
            **CrawlOptions().semantic_snapshot(seeds),
        },
        runtime_config={},
    )
    source_state.connection.execute(
        "INSERT INTO articles(url, payload) VALUES (?, ?)",
        ("https://a.test/article", "{malformed-json"),
    )
    source_state.finish("complete", 0, source_state.counts(), {})
    source_state.close()
    output = tmp_path / "crawl-all"

    with pytest.raises(ValueError, match="could not read reused article metadata"):
        run_crawl(
            input_path=input_path,
            output=output,
            config=config,
            seeds=seeds,
            options=CrawlOptions(assets="all"),
            tuning=resolve_runtime_tuning(profile="safe-fast"),
            reuse_content_from=source,
        )

    assert not output.exists()


def test_crawl_runner_keyboard_interrupt_publishes_snapshots(
    monkeypatch, tmp_path: Path
) -> None:
    capture_runner_dependencies(monkeypatch)
    monkeypatch.setattr(
        runner.CrawlerProcess, "start", lambda self: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    exit_code = run_unified_for_test(tmp_path)
    assert exit_code == 130
    manifest_path = tmp_path / "crawl-output" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted"
    assert manifest["exit_code"] == 130
    assert manifest["discovery"]["strategy"] == "hybrid-unified"
    assert "frontier" in manifest
    assert "frontier" in manifest["metrics"]


def test_crawl_runner_failure_publishes_snapshots(
    monkeypatch, tmp_path: Path
) -> None:
    capture_runner_dependencies(monkeypatch)
    monkeypatch.setattr(
        runner.CrawlerProcess, "start", lambda self: (_ for _ in ()).throw(RuntimeError("crawl crashed"))
    )
    exit_code = run_unified_for_test(tmp_path)
    assert exit_code == 1
    manifest_path = tmp_path / "crawl-output" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["exit_code"] == 1
    assert manifest["metrics"]["error"] == "crawl crashed"
    assert manifest["discovery"]["strategy"] == "hybrid-unified"
    assert "frontier" in manifest
    assert "frontier" in manifest["metrics"]


def test_crawl_runner_resume_restores_frontier_and_coordinator(
    monkeypatch, tmp_path: Path
) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    exit_code = run_unified_for_test(tmp_path)
    assert exit_code == 0

    # Resumed run: populate some state records first
    state_dir = tmp_path / "crawl-output" / "state"
    state = CrawlState.open(
        state_dir,
        phase="crawl",
        input_path=tmp_path / "seeds.txt",
        semantic_config=observed["semantic_config"],
        runtime_config=observed["runtime_config"],
        resume=True,
    )
    state.merge_url({
        "url": "https://a.test/page1",
        "status": "scheduled",
        "frontier_action": "scheduled",
    })
    state.upsert_host_discovery({
        "hostname": "a.test",
        "mode": "sitemap",
        "robots_complete": True,
        "pending_sitemaps": [],
        "registered_sitemaps": {"https://a.test/sitemap.xml": True},
        "sitemaps_succeeded": 1,
        "sitemaps_failed": 0,
        "sitemaps_absent": 0,
        "sitemap_targets": ["https://a.test/page1"],
        "html_targets": [],
    })
    state.close()

    # Now resume via run_crawl
    observed.clear()
    exit_code2 = run_unified_for_test(tmp_path, resume=True)
    assert exit_code2 == 0
    resumed_frontier = observed["crawl_kwargs"]["frontier"]
    resumed_coordinator = observed["crawl_kwargs"]["coordinator"]
    resume_records = observed["crawl_kwargs"]["resume_records"]
    assert resumed_frontier.snapshot()["total_scheduled"] == 1
    assert resumed_coordinator.hosts["a.test"].mode == "sitemap"
    assert [record["url"] for record in resume_records] == ["https://a.test/page1"]
    assert observed["jobdir"] is None


def test_crawl_runner_retry_failed_requeues_transport_errors(
    monkeypatch, tmp_path: Path
) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    assert run_unified_for_test(tmp_path) == 0

    state = CrawlState.open(
        tmp_path / "crawl-output" / "state",
        phase="crawl",
        input_path=tmp_path / "seeds.txt",
        semantic_config=observed["semantic_config"],
        runtime_config=observed["runtime_config"],
        resume=True,
    )
    state.merge_url({
        "url": "https://a.test/retry",
        "status": "scheduled",
        "frontier_action": "scheduled",
    })
    state.complete_url(
        {"url": "https://a.test/retry", "status": "failed"},
        error={
            "url": "https://a.test/retry",
            "error": "request_failed",
            "message": "User timeout caused connection failure.",
        },
    )
    state.close()

    observed.clear()
    assert run_unified_for_test(tmp_path, resume=True, retry_failed=True) == 0
    assert [
        record["url"] for record in observed["crawl_kwargs"]["resume_records"]
    ] == ["https://a.test/retry"]


def test_crawl_runner_retry_truncated_requeues_query_variant_limits(
    monkeypatch, tmp_path: Path
) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    assert run_unified_for_test(tmp_path) == 0

    state = CrawlState.open(
        tmp_path / "crawl-output" / "state",
        phase="crawl",
        input_path=tmp_path / "seeds.txt",
        semantic_config=observed["semantic_config"],
        runtime_config=observed["runtime_config"],
        resume=True,
    )
    state.complete_url({
        "url": "https://a.test/news?page=22&tag=undergraduate",
        "status": "skipped",
        "reason": "query_variant_limit",
        "discovery_source": "html_link",
        "response_purpose": "page",
    })
    state.close()

    observed.clear()
    assert run_unified_for_test(tmp_path, resume=True, retry_truncated=True) == 0
    resume_records = observed["crawl_kwargs"]["resume_records"]
    assert [record["url"] for record in resume_records] == [
        "https://a.test/news?page=22&tag=undergraduate"
    ]
    assert resume_records[0]["link_kind"] == "pagination"
    assert resume_records[0]["family_key"] == (
        "a.test/news?page={page}&tag=undergraduate"
    )
    assert resume_records[0]["ordinal"] == 22


def test_crawl_runner_retry_access_gates_requeues_captcha_blocked_pages(
    monkeypatch, tmp_path: Path
) -> None:
    observed = capture_runner_dependencies(monkeypatch)
    assert run_unified_for_test(tmp_path) == 0

    state = CrawlState.open(
        tmp_path / "crawl-output" / "state",
        phase="crawl",
        input_path=tmp_path / "seeds.txt",
        semantic_config=observed["semantic_config"],
        runtime_config=observed["runtime_config"],
        resume=True,
    )
    state.merge_url({
        "url": "https://a.test/false-captcha",
        "status": "scheduled",
        "seed_type": "recursive",
        "frontier_action": "scheduled",
        "discovery_source": "html_link",
        "response_purpose": "page",
        "link_kind": "content",
    })
    state.complete_url({
        "url": "https://a.test/false-captcha",
        "status": "skipped",
        "reason": "captcha_blocked",
    })
    state.close()

    observed.clear()
    assert run_unified_for_test(
        tmp_path,
        resume=True,
        retry_access_gates=True,
    ) == 0
    assert [
        record["url"] for record in observed["crawl_kwargs"]["resume_records"]
    ] == ["https://a.test/false-captcha"]


def test_crawl_runner_resume_rejects_incompatible_workflow(tmp_path: Path) -> None:
    state_dir = tmp_path / "crawl-output" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "manifest.json").write_text(
        json.dumps({
            "state_version": 4,
            "phase": "crawl",
            "semantic_config": {"crawl_strategy": "sitemap-only"},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hybrid-unified"):
        run_unified_for_test(tmp_path, resume=True)
