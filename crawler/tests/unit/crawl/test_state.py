import json
from pathlib import Path
from typing import Literal

import pytest

from hust_crawler.crawl.state import (
    CrawlState,
    RouteFamilyState,
    normalize_legacy_semantic_config,
    validate_resume_workflow,
)


def open_state(
    root: Path,
    phase: Literal["discover", "crawl"] = "crawl",
    *,
    resume: bool = False,
    semantic_config: dict[str, object] | None = None,
    runtime_config: dict[str, object] | None = None,
) -> CrawlState:
    state_dir = root if root.name == "state" or (root / "manifest.json").exists() else root / "output" / "state"
    input_path = state_dir.parent / "input.txt"
    if not input_path.exists():
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("a.test\n", encoding="utf-8")
    return CrawlState.open(
        state_dir,
        phase=phase,
        input_path=input_path,
        semantic_config={} if semantic_config is None else semantic_config,
        runtime_config={} if runtime_config is None else runtime_config,
        resume=resume,
    )


def test_resume_rejects_a_different_phase(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="discover")
    state.close()
    with pytest.raises(ValueError, match="phase"):
        open_state(tmp_path, phase="crawl", resume=True)


def test_url_upsert_keeps_one_authoritative_disposition(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/x", "status": "discovered_not_downloaded"})
    state.upsert_url({"url": "https://a.test/x", "status": "extracted"})
    state.checkpoint("https://a.test/x")
    state.export(tmp_path / "output")
    rows = [json.loads(line) for line in (tmp_path / "output/url_records.jsonl").read_text().splitlines()]
    assert rows == [{"status": "extracted", "url": "https://a.test/x"}]
    assert state.is_complete("https://a.test/x")
    state.close()


def test_late_discovery_cannot_downgrade_completed_success(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url(
        {"url": "https://a.test/article", "status": "extracted", "http_status": 200},
        article={"url": "https://a.test/article", "title": "Keep me"},
    )

    state.upsert_url(
        {
            "url": "https://a.test/article",
            "status": "skipped",
            "reason": "host_out_of_scope",
        }
    )

    record = next(state.iter_url_records())
    assert record["status"] == "extracted"
    assert record["http_status"] == 200
    assert state.is_complete("https://a.test/article")
    state.close()


def test_crawl_export_lists_only_successful_urls_in_text_file(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    records = (
        {"url": "https://a.test/z-file.pdf", "status": "file_saved"},
        {"url": "https://a.test/a-article", "status": "extracted"},
        {"url": "https://a.test/discovered", "status": "discovered_not_downloaded"},
        {"url": "https://a.test/skipped", "status": "skipped"},
        {"url": "https://a.test/failed", "status": "failed"},
        {"url": "https://a.test/pending", "status": "scheduled"},
    )
    for record in records:
        state.upsert_url(record)

    state.export_crawl(tmp_path / "output")

    assert (tmp_path / "output/urls.txt").read_text(encoding="utf-8") == (
        "https://a.test/a-article\n"
        "https://a.test/z-file.pdf\n"
    )
    state.close()


def test_finish_publishes_only_current_extracted_articles_to_final(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    output = tmp_path / "output"
    state.complete_url(
        {"url": "https://a.test/keep", "status": "extracted"},
        article={
            "url": "https://a.test/keep",
            "title": "Keep",
            "duplicate_of": "https://a.test/stale",
        },
    )
    state.put_article({"url": "https://a.test/stale", "title": "Stale", "text": "Full text"})
    state.upsert_url(
        {"url": "https://a.test/stale", "status": "skipped", "reason": "action_auth"}
    )

    state.finish("complete_with_failures", 3, state.counts(), {}, public_output=output)

    final = output / "final"
    articles = [json.loads(line) for line in (final / "articles.jsonl").read_text().splitlines()]
    assert articles == [{"url": "https://a.test/keep", "title": "Keep", "text": "Full text"}]
    assert (final / "urls.txt").read_text() == "https://a.test/keep\n"
    summary = json.loads((final / "README.json").read_text())
    assert summary["article_count"] == summary["url_count"] == 1
    assert sorted(path.name for path in final.iterdir()) == [
        "README.json", "articles.jsonl", "urls.txt"
    ]
    state.close()


def test_finish_refreshes_final_after_resume(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    output = tmp_path / "output"
    state.complete_url(
        {"url": "https://a.test/first", "status": "extracted"},
        article={"url": "https://a.test/first"},
    )
    state.finish("complete", 0, state.counts(), {}, public_output=output)
    assert (output / "final/urls.txt").read_text() == "https://a.test/first\n"

    state.complete_url(
        {"url": "https://a.test/second", "status": "extracted"},
        article={"url": "https://a.test/second"},
    )
    state.finish("complete", 0, state.counts(), {}, public_output=output)

    assert (output / "final/urls.txt").read_text() == (
        "https://a.test/first\nhttps://a.test/second\n"
    )
    assert [
        json.loads(line)["url"]
        for line in (output / "final/articles.jsonl").read_text().splitlines()
    ] == ["https://a.test/first", "https://a.test/second"]
    assert json.loads((output / "final/README.json").read_text())["article_count"] == 2
    state.close()


def test_resume_rejects_semantic_change_but_accepts_runtime_change(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.close()
    source = tmp_path / "output" / "input.txt"
    with pytest.raises(ValueError, match="semantic configuration"):
        CrawlState.open(
            tmp_path / "output/state",
            phase="crawl",
            input_path=source,
            semantic_config={"assets": "all"},
            runtime_config={"max_concurrency": 64},
            resume=True,
        )

    resumed = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config={},
        runtime_config={"max_concurrency": 32},
        resume=True,
    )
    assert resumed.manifest["runtime_history"][-1] == {"max_concurrency": 32}
    resumed.close()


def test_resume_policy_migration_requires_explicit_opt_in(tmp_path: Path) -> None:
    legacy = {
        "crawl_strategy": "hybrid-unified",
        "assets": "content-only",
        "robots_txt_obey": True,
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
    }
    current = {
        **legacy,
        "robots_txt_obey": False,
        "access_policy_revision": 2,
        "browser_user_agent": "Mozilla/5.0 Chrome/140.0.0.0",
    }
    state = open_state(tmp_path, semantic_config=legacy)
    state.close()
    source = tmp_path / "output" / "input.txt"

    with pytest.raises(ValueError, match="semantic configuration"):
        CrawlState.open(
            tmp_path / "output/state",
            phase="crawl",
            input_path=source,
            semantic_config=current,
            runtime_config={},
            resume=True,
        )

    resumed = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config=current,
        runtime_config={},
        resume=True,
        allow_policy_migration=True,
    )
    assert resumed.manifest["semantic_config"] == current
    assert resumed.manifest["policy_migrations"][0]["to_revision"] == 2
    resumed.close()

    resumed_again = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config=current,
        runtime_config={},
        resume=True,
        allow_policy_migration=True,
    )
    assert len(resumed_again.manifest["policy_migrations"]) == 1
    resumed_again.close()


def test_requeue_policy_skips_preserves_successful_records_and_is_idempotent(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    successful = {
        "url": "https://a.test/success",
        "status": "extracted",
        "title": "Keep me",
    }
    state.complete_url(successful, article={"url": successful["url"], "title": "Keep me"})
    state.complete_url(
        {"url": "https://a.test/file.pdf", "status": "file_saved"},
        file={"url": "https://a.test/file.pdf", "path": "files/file.pdf"},
    )
    for reason in ("robots_disallowed", "login_required", "captcha_blocked", "access_denied"):
        url = f"https://a.test/{reason}"
        state.complete_url(
            {"url": url, "status": "skipped", "reason": reason},
            error={"url": url, "error": reason},
        )
    state.complete_url(
        {"url": "https://a.test/not-found", "status": "failed", "reason": "http_404"},
        error={"url": "https://a.test/not-found", "error": "http_404"},
    )

    assert state.requeue_policy_skips(frozenset({"a.test"})) == 4
    pending = {row["url"] for row in state.iter_pending_scheduled_records()}
    assert pending == {
        "https://a.test/robots_disallowed",
        "https://a.test/login_required",
        "https://a.test/captcha_blocked",
        "https://a.test/access_denied",
    }
    assert state.is_complete("https://a.test/success")
    assert state.is_complete("https://a.test/file.pdf")
    article_payload = state.connection.execute(
        "SELECT payload FROM articles WHERE url = ?",
        ("https://a.test/success",),
    ).fetchone()[0]
    assert json.loads(article_payload) == {"url": "https://a.test/success", "title": "Keep me"}
    assert state.requeue_policy_skips(frozenset({"a.test"})) == 0
    state.close()


def test_requeue_zero_content_bootstrap_only_resets_recoverable_roots(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url({"url": "https://a.test/", "status": "skipped", "reason": "access_denied"})
    state.complete_url({"url": "https://b.test/", "status": "extracted"})
    state.complete_url({"url": "https://c.test/", "status": "skipped", "reason": "host_out_of_scope"})

    assert state.requeue_zero_content_bootstrap(frozenset({"a.test", "b.test", "c.test"})) == 1
    pending = {row["url"] for row in state.iter_pending_scheduled_records()}
    assert "https://a.test/" in pending
    assert "https://b.test/" not in pending
    assert "https://c.test/" not in pending
    state.close()


def test_host_completion_reports_extracted_pages_and_terminal_reasons(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url({"url": "https://a.test/article", "status": "extracted"})
    state.complete_url(
        {"url": "https://b.test/", "status": "skipped", "reason": "host_out_of_scope"}
    )
    state.complete_url(
        {"url": "https://b.test/robots.txt", "status": "skipped", "reason": "robots_disallowed"}
    )

    assert state.host_completion(frozenset({"a.test", "b.test"})) == {
        "a.test": {
            "status": "complete",
            "extracted_pages": 1,
            "terminal_reasons": {},
        },
        "b.test": {
            "status": "zero_content",
            "extracted_pages": 0,
            "terminal_reasons": {
                "host_out_of_scope": 1,
                "robots_disallowed": 1,
            },
        },
    }
    state.close()


def test_legacy_semantic_snapshot_drops_values_reclassified_as_runtime() -> None:
    saved = {
        "assets": "content-only",
        "download_timeout_seconds": 60.0,
        "retry_times": 3,
        "retry_backoff_base_seconds": 2.0,
        "retry_max_delay_seconds": 3600.0,
        "retry_statuses": [408, 429, 500],
    }
    current = {
        "assets": "content-only",
        "retry_statuses": [408, 429, 500],
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
    }

    assert normalize_legacy_semantic_config(saved) == normalize_legacy_semantic_config(current)


def test_resume_rejects_old_asset_policy(tmp_path: Path) -> None:
    state = open_state(
        tmp_path,
        semantic_config={"assets": "documents", "asset_policy_version": 1},
    )
    state.close()
    with pytest.raises(ValueError, match="semantic configuration"):
        open_state(
            tmp_path,
            semantic_config={"assets": "content-only", "asset_policy_version": 2},
            resume=True,
        )


def test_claim_content_deduplication(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    owner1 = state.claim_content("hash-123", "https://a.test/first")
    owner2 = state.claim_content("hash-123", "https://a.test/second")
    assert owner1 == "https://a.test/first"
    assert owner2 == "https://a.test/first"
    state.close()


def test_interruption_safe_reopen(tmp_path: Path) -> None:
    state1 = open_state(tmp_path)
    state1.upsert_url({"url": "https://a.test/done", "status": "extracted"})
    state1.checkpoint("https://a.test/done")
    state1.upsert_url({"url": "https://a.test/pending", "status": "discovered_not_downloaded"})
    state1.close()

    source = tmp_path / "output" / "input.txt"
    state2 = CrawlState.open(
        tmp_path / "output/state",
        phase="crawl",
        input_path=source,
        semantic_config={},
        runtime_config={"max_concurrency": 64},
        resume=True,
    )
    assert state2.is_complete("https://a.test/done")
    assert not state2.is_complete("https://a.test/pending")
    state2.close()


def test_counts_and_finish_manifest(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.upsert_url({"url": "https://a.test/1", "status": "extracted", "completed": 1})
    state.upsert_url({"url": "https://a.test/2", "status": "discovered_not_downloaded"})
    state.put_article({"url": "https://a.test/1", "title": "One"})
    state.put_file({"url": "https://a.test/doc.pdf", "path": "files/abc.pdf"})
    state.put_error({"url": "https://a.test/err", "error": "timeout"})

    counts = state.counts()
    assert counts["total_urls"] == 2
    assert counts["completed_urls"] == 1
    assert counts["url_status_extracted"] == 1
    assert counts["url_status_discovered_not_downloaded"] == 1
    assert counts["articles"] == 1
    assert counts["files"] == 1
    assert counts["errors"] == 1

    state.finish("complete", 0, counts, {"requests": 10})
    state.close()

    manifest = json.loads((tmp_path / "output/state/manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["exit_code"] == 0
    assert manifest["counts"]["total_urls"] == 2


def test_final_disposition_preserves_seed_provenance(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.merge_url(
        {
            "url": "https://a.test/article",
            "status": "scheduled",
            "seed_type": "recursive",
            "discovered_from": "https://a.test/",
        }
    )
    state.complete_url(
        {
            "url": "https://a.test/article",
            "status": "extracted",
            "http_status": 200,
        },
        article={"url": "https://a.test/article", "title": "Article", "text": "Body"},
    )
    state.export(tmp_path / "output")
    record = json.loads((tmp_path / "output/url_records.jsonl").read_text())
    assert record["seed_type"] == "recursive"
    assert record["discovered_from"] == "https://a.test/"
    assert state.is_complete(record["url"])


def test_iter_pending_scheduled_records_excludes_completed_urls(tmp_path: Path) -> None:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    state = CrawlState.open(
        tmp_path / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={},
        runtime_config={},
    )
    state.merge_url(
        {
            "url": "https://a.test/pending",
            "status": "scheduled",
            "frontier_action": "scheduled",
        }
    )
    state.complete_url(
        {
            "url": "https://a.test/done",
            "status": "extracted",
            "frontier_action": "scheduled",
        }
    )
    state.merge_url(
        {
            "url": "https://a.test/disonly",
            "status": "discovered_not_downloaded",
        }
    )

    records = tuple(state.iter_pending_scheduled_records())

    assert [record["url"] for record in records] == ["https://a.test/pending"]
    state.close()


def test_requeue_retryable_failures_only_resets_transport_errors(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    failures = (
        (
            "https://a.test/no-route",
            "An error occurred while connecting: 113: No route to host.",
        ),
        (
            "https://a.test/timeout",
            "User timeout caused connection failure.",
        ),
        (
            "https://a.test/bad-redirect",
            "redirect_without_location",
        ),
    )
    for index, (url, message) in enumerate(failures):
        state.merge_url(
            {
                "url": url,
                "status": "scheduled",
                "frontier_action": "scheduled",
            }
        )
        state.complete_url(
            {"url": url, "status": "failed", "completed": index == 0},
            error={"url": url, "error": "request_failed", "message": message},
        )
    state.complete_url(
        {"url": "https://a.test/missing", "status": "failed"},
        error={"url": "https://a.test/missing", "error": "http_404"},
    )
    state.complete_url(
        {"url": "https://a.test/bad-request", "status": "failed"},
        error={"url": "https://a.test/bad-request", "error": "http_400"},
    )
    state.complete_url(
        {"url": "https://a.test/server-error", "status": "failed"},
        error={"url": "https://a.test/server-error", "error": "http_500"},
    )

    assert state.requeue_retryable_failures() == 2
    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/no-route",
        "https://a.test/timeout",
    ]
    assert all(not record.get("completed") for record in pending)

    state.export(tmp_path / "output")
    remaining_errors = [
        json.loads(line)
        for line in (tmp_path / "output/errors.jsonl").read_text().splitlines()
    ]
    assert [record["url"] for record in remaining_errors] == [
        "https://a.test/bad-redirect",
        "https://a.test/bad-request",
        "https://a.test/missing",
        "https://a.test/server-error",
    ]
    state.close()


def test_requeue_query_variant_truncations_rebuilds_semantic_metadata(
    tmp_path: Path,
) -> None:
    state = open_state(tmp_path)
    records = (
        {
            "url": "https://wp.test/?p=33214",
            "final_url": "https://wp.test/?p=33214",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
        {
            "url": "https://a.test/news?page=22&tag=undergraduate",
            "final_url": "https://a.test/news?page=22&tag=undergraduate",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
        {
            "url": "https://a.test/over-host-budget",
            "status": "skipped",
            "reason": "host_url_limit",
        },
        {
            "url": "https://a.test/news?sort=oldest",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
    )
    for record in records:
        state.complete_url(record)

    assert state.requeue_query_variant_truncations() == 2
    assert state.requeue_query_variant_truncations() == 0

    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/news?page=22&tag=undergraduate",
        "https://wp.test/?p=33214",
    ]
    pagination, wordpress = pending
    assert pagination["link_kind"] == "pagination"
    assert pagination["family_key"] == (
        "a.test/news?page={page}&tag=undergraduate"
    )
    assert pagination["ordinal"] == 22
    assert wordpress["link_kind"] == "content"
    assert wordpress.get("family_key") is None
    assert wordpress.get("ordinal") is None
    assert all(record["status"] == "scheduled" for record in pending)
    assert all("reason" not in record for record in pending)
    assert state.reason_counts(frozenset({"query_variant_limit"})) == {
        "query_variant_limit": 1
    }
    assert state.reason_counts(frozenset({"host_url_limit"})) == {
        "host_url_limit": 1
    }
    state.close()


def test_requeue_access_gates_only_resets_captcha_blocked_pages(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    for url, reason in (
        ("https://a.test/false-captcha", "captcha_blocked"),
        ("https://a.test/login", "login_required"),
        ("https://a.test/query", "query_variant_limit"),
    ):
        state.merge_url({
            "url": url,
            "status": "scheduled",
            "seed_type": "recursive",
            "frontier_action": "scheduled",
            "discovery_source": "html_link",
            "response_purpose": "page",
            "link_kind": "content",
        })
        state.complete_url({"url": url, "status": "skipped", "reason": reason})

    assert state.requeue_access_gates() == 1
    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/false-captcha"
    ]
    assert pending[0]["status"] == "scheduled"
    assert "reason" not in pending[0]
    assert state.is_complete("https://a.test/login")
    assert state.is_complete("https://a.test/query")
    state.close()


def test_failed_article_transaction_leaves_url_incomplete(tmp_path: Path, monkeypatch) -> None:
    state = open_state(tmp_path)
    state.merge_url({"url": "https://a.test/article", "status": "scheduled"})
    monkeypatch.setattr(state, "_put_payload", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        state.complete_url(
            {"url": "https://a.test/article", "status": "extracted"},
            article={"url": "https://a.test/article", "text": "Body"},
        )
    assert not state.is_complete("https://a.test/article")


def test_storage_budget_is_a_truncation_reason(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.merge_url(
        {"url": "https://a.test/large.pdf", "status": "skipped", "reason": "storage_budget"},
        completed=True,
    )
    assert state.reason_counts(frozenset({"storage_budget"})) == {"storage_budget": 1}


def test_resume_rejects_wrong_state_version(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.close()
    manifest_path = tmp_path / "output/state/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["state_version"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="state version"):
        open_state(tmp_path, resume=True)




def test_host_discovery_state_round_trip(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    record = {
        "hostname": "a.test",
        "robots_complete": True,
        "pending_sitemaps": [],
        "registered_sitemaps": {"https://a.test/sitemap.xml": True},
        "sitemaps_succeeded": 1,
        "sitemaps_failed": 0,
        "sitemaps_absent": 0,
        "sitemap_targets": ["https://a.test/page1"],
        "mode": "sitemap",
    }
    state.upsert_host_discovery(record)
    state.close()

    resumed = open_state(tmp_path, resume=True)
    rows = list(resumed.iter_host_discovery())
    assert rows == [record]
    resumed.close()


def test_finish_publishes_discovery_strategy_to_public_manifest(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="discover")
    discovery = {"strategy": "sitemap-only", "sitemap_url_limits": "unbounded", "hosts": {"a.test": {"mode": "sitemap"}}}
    state.finish("complete", 0, {}, {}, discovery=discovery, public_output=tmp_path / "output")
    public = json.loads((tmp_path / "output/manifest.json").read_text())
    assert public["discovery"] == discovery


def test_manifest_groups_stored_bytes_by_asset_class(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.put_file({
        "url": "https://a.test/photo.jpg",
        "final_url": "https://a.test/photo.jpg",
        "content_type": "image/jpeg",
        "size": 5,
        "sha256": "sha-img",
        "asset_role": "inline_image",
    })
    state.put_file({
        "url": "https://a.test/report.pdf",
        "final_url": "https://a.test/report.pdf",
        "content_type": "application/pdf",
        "size": 9,
        "sha256": "sha-pdf",
        "asset_role": "document_attachment",
    })
    # Another URL sharing the same sha256 blob (aliased file)
    state.put_file({
        "url": "https://a.test/photo_alias.jpg",
        "final_url": "https://a.test/photo_alias.jpg",
        "content_type": "image/jpeg",
        "size": 5,
        "sha256": "sha-img",
        "asset_role": "inline_image",
    })
    metrics = state.asset_metrics()
    assert metrics == {
        "image": {"file_count": 2, "unique_bytes": 5},
        "document": {"file_count": 1, "unique_bytes": 9},
        "media": {"file_count": 0, "unique_bytes": 0},
    }
    state.close()


def test_crawl_manifest_includes_hybrid_discovery(tmp_path: Path) -> None:
    state = open_state(
        tmp_path, phase="crawl", semantic_config={"crawl_strategy": "hybrid-unified"}
    )
    state.finish(
        "complete",
        0,
        state.counts(),
        {},
        discovery={"strategy": "hybrid-unified", "hosts": {}},
        public_output=tmp_path / "public",
    )
    manifest = json.loads((tmp_path / "public/manifest.json").read_text(encoding="utf-8"))
    assert manifest["discovery"]["strategy"] == "hybrid-unified"
    state.close()


def test_resume_rejects_fixed_inventory_state_with_clear_message(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="crawl", semantic_config={})
    state.close()
    state_dir = tmp_path if tmp_path.name == "state" or (tmp_path / "manifest.json").exists() else tmp_path / "output" / "state"
    with pytest.raises(ValueError, match="hybrid-unified"):
        validate_resume_workflow(state_dir, expected="hybrid-unified")


def test_validate_resume_workflow_accepts_matching_strategy(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="crawl", semantic_config={"crawl_strategy": "hybrid-unified"})
    state.close()
    state_dir = tmp_path if tmp_path.name == "state" or (tmp_path / "manifest.json").exists() else tmp_path / "output" / "state"
    validate_resume_workflow(state_dir, expected="hybrid-unified")


def test_validate_resume_workflow_ignores_missing_manifest(tmp_path: Path) -> None:
    validate_resume_workflow(tmp_path / "nonexistent", expected="hybrid-unified")


def test_validate_resume_workflow_rejects_sitemap_only_state(tmp_path: Path) -> None:
    state = open_state(tmp_path, phase="crawl", semantic_config={"crawl_strategy": "sitemap-only"})
    state.close()
    state_dir = tmp_path if tmp_path.name == "state" or (tmp_path / "manifest.json").exists() else tmp_path / "output" / "state"
    with pytest.raises(ValueError, match="cannot resume sitemap-only state as hybrid-unified"):
        validate_resume_workflow(state_dir, expected="hybrid-unified")


def test_route_family_state_survives_resume(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    family = RouteFamilyState(
        family_key="a.test/news?page={page}",
        kind="pagination",
        scheduled_next_ordinal=4,
        content_fingerprints=("c1",),
        target_fingerprints=("t1",),
        accepted_targets=("https://a.test/article/1",),
        consecutive_zero_novelty=2,
        closed=True,
        closure_reason="repeated_target_set",
    )
    state.put_route_family(family)
    assert list(state.iter_route_families()) == [family]


def test_asset_referrers_persists_and_updates_file_payload(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p1", "inline_image")
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p2", "inline_image")
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p1", "inline_image")
    assert state.asset_referrers("https://cdn.test/a.png") == ("https://a.test/p1", "https://a.test/p2")

    # If file row already exists, add_asset_referrer updates its payload
    state.put_file({
        "url": "https://cdn.test/a.png",
        "final_url": "https://cdn.test/a.png",
        "asset_role": "inline_image",
        "referring_pages": ["https://a.test/p1", "https://a.test/p2"],
    })
    state.add_asset_referrer("https://cdn.test/a.png", "https://a.test/p3", "inline_image")
    cursor = state.connection.execute("SELECT payload FROM files WHERE url = ?;", ("https://cdn.test/a.png",))
    updated_payload = json.loads(cursor.fetchone()[0])
    assert updated_payload["referring_pages"] == ["https://a.test/p1", "https://a.test/p2", "https://a.test/p3"]
    state.close()
