from __future__ import annotations

import pytest

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.profiles import resolve_runtime_tuning


def test_safe_fast_defaults_are_available_to_both_phases() -> None:
    tuning = resolve_runtime_tuning(profile="safe-fast")
    assert tuning.max_concurrency == 64
    assert tuning.max_per_host == 2
    assert tuning.resource_limit_percent == 80
    assert tuning.playwright_max_pages == 8
    assert tuning.journal_batch_size == 500
    assert tuning.journal_flush_seconds == 2.0
    assert tuning.progress_interval_seconds == 10.0


def test_custom_concurrency_overrides() -> None:
    tuning = resolve_runtime_tuning(
        profile="safe-fast",
        concurrency=32,
        max_per_host=4,
        resource_limit_percent=70,
    )
    assert tuning.max_concurrency == 32
    assert tuning.max_per_host == 4
    assert tuning.resource_limit_percent == 70


def test_runtime_tuning_uses_config_values() -> None:
    config = CrawlerConfig(
        hostnames=frozenset({"a.test"}), contact="ops@example.org",
        concurrent_requests=11, concurrent_per_host=3,
        resource_limit_percent=65, playwright_max_pages=5,
        journal_batch_size=17, journal_flush_seconds=1.5,
        progress_interval_seconds=4.0,
    )
    tuning = resolve_runtime_tuning(config=config, profile="safe-fast")
    assert tuning.max_concurrency == 11
    assert tuning.max_per_host == 3
    assert tuning.resource_limit_percent == 65
    assert tuning.playwright_max_pages == 5
    assert tuning.journal_batch_size == 17


def test_invalid_profile_or_limits_rejected() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        resolve_runtime_tuning(profile="unknown")
    with pytest.raises(ValueError, match="concurrency limits must be positive"):
        resolve_runtime_tuning(profile="safe-fast", concurrency=0)
    with pytest.raises(ValueError, match="resource limit percent"):
        resolve_runtime_tuning(profile="safe-fast", resource_limit_percent=150)
