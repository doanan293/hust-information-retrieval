from hust_crawler.coverage import CoverageLedger, LifecycleEvent


def event(url: str, phase: str, reason: str | None = None, **overrides) -> LifecycleEvent:
    return LifecycleEvent(
        url=url,
        phase=phase,
        hostname=overrides.pop("hostname", "example.com"),
        source=overrides.pop("source", "html"),
        reason=reason,
        **overrides,
    )


def test_root_discovery_without_terminal_reports_root_gap_only(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/a", "discovered", source="root"))

    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.status == "complete_with_gaps"
    assert summary.gap_reasons == {"root_missing_terminal_outcome": 1}


def test_discovery_not_accepted_by_scheduler_is_informational(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/image.png", "discovered", source="embedded_media"))

    summary = ledger.summarize(frozenset(), archive_valid=True)

    assert summary.status == "complete"
    assert summary.totals == {"discovered": 1}


def test_archived_urls_produce_complete_when_every_host_was_attempted(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    for phase in ("discovered", "scheduled", "fetched", "archived"):
        ledger.append(event("https://example.com/a", phase, source="root"))

    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.status == "complete"
    assert summary.by_host["example.com"]["archived"] == 1


def test_terminal_gap_reasons_are_complete_with_gaps(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/a", "discovered", source="root"))
    ledger.append(event("https://example.com/a", "failed", "captcha_blocked", source="root"))

    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.status == "complete_with_gaps"
    assert summary.gap_reasons["captcha_blocked"] == 1


def test_expected_policy_rejection_is_terminal_but_not_a_gap(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://outside.test/page", "discovered", source="html"))
    ledger.append(event("https://outside.test/page", "rejected", "host_out_of_scope", source="html"))

    summary = ledger.summarize(frozenset(), archive_valid=True)

    assert summary.status == "complete"
    assert summary.gap_reasons.get("host_out_of_scope", 0) == 0


def test_resume_loads_ledger_and_uses_latest_phase_per_url(tmp_path) -> None:
    path = tmp_path / "lifecycle.jsonl"
    first = CoverageLedger(path)
    first.append(event("https://example.com/a", "discovered", source="root"))
    second = CoverageLedger.load(path)
    second.append(event("https://example.com/a", "archived", source="root", bytes=10))

    summary = second.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.totals["archived"] == 1
    assert summary.to_dict()["bytes_archived"] == 10


def test_interrupted_run_reports_nonterminal_work_as_pending(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/", "scheduled", source="root"))
    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True, interrupted=True)
    assert summary.status == "interrupted"
    assert summary.totals == {"pending": 1}
    assert summary.gap_reasons.get("missing_terminal_outcome", 0) == 0


def test_internal_error_has_precedence_over_interruption(tmp_path) -> None:
    summary = CoverageLedger(tmp_path / "lifecycle.jsonl").summarize(
        frozenset(), archive_valid=True, interrupted=True, internal_error=True
    )
    assert summary.status == "failed"


def test_invalid_archive_has_precedence_over_interruption(tmp_path) -> None:
    summary = CoverageLedger(tmp_path / "lifecycle.jsonl").summarize(
        frozenset(), archive_valid=False, interrupted=True
    )
    assert summary.status == "failed"
    assert summary.gap_reasons["archive_invalid"] == 1


def test_operational_terminal_outcomes_do_not_create_coverage_gaps(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/duplicate", "deduplicated"))
    ledger.append(event("https://example.com/old", "redirected"))
    ledger.append(event("https://example.com/", "fallback", source="root"))
    ledger.append(event("https://example.com/over-budget", "budget_rejected"))

    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.status == "complete"
    assert summary.gaps == []


def test_archived_outcome_is_not_overwritten_by_later_duplicate_or_schedule(tmp_path) -> None:
    ledger = CoverageLedger(tmp_path / "lifecycle.jsonl")
    ledger.append(event("https://example.com/", "archived", source="root", bytes=12))
    ledger.append(event("https://example.com/", "deduplicated", source="robots_sitemap"))
    ledger.append(event("https://example.com/", "scheduled", source="sitemap"))

    summary = ledger.summarize(frozenset({"example.com"}), archive_valid=True)

    assert summary.status == "complete"
    assert summary.totals == {"archived": 1}
    assert summary.bytes_archived == 12
