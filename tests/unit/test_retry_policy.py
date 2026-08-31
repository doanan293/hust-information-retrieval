from datetime import datetime, timezone

import pytest

from hust_crawler.retry_policy import retry_delay


NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def test_retry_after_seconds_wins() -> None:
    assert retry_delay(429, {b"retry-after": b"49"}, 1, NOW) == 49.0


def test_non_retryable_access_control() -> None:
    assert retry_delay(403, {}, 1, NOW) is None


def test_retry_cap() -> None:
    assert retry_delay(503, {}, 4, NOW) is None


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_configured_transient_statuses_retry(status: int) -> None:
    assert retry_delay(status, {}, 1, NOW, max_attempts=3, jitter=0) == 1.0


def test_retry_after_is_capped() -> None:
    assert retry_delay(
        429,
        {b"retry-after": b"7200"},
        1,
        NOW,
        max_attempts=3,
        max_delay=3600,
        jitter=0,
    ) == 3600.0


def test_backoff_is_deterministic_when_jitter_is_zero() -> None:
    assert retry_delay(503, {}, 3, NOW, max_attempts=3, base_delay=2, jitter=0) == 4.0
