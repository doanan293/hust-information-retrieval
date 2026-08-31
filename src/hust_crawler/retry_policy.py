from __future__ import annotations

import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections.abc import Mapping


def _header(headers: Mapping[bytes, bytes], key: bytes) -> bytes | None:
    for name, value in headers.items():
        if name.lower() == key:
            return value[0] if isinstance(value, list) else value
    return None


def parse_retry_after(headers: Mapping[bytes, bytes], now: datetime) -> float | None:
    retry_after = _header(headers, b"retry-after")
    if not retry_after:
        return None
    value = retry_after.decode("latin-1").strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - now.astimezone(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def retry_delay(
    status: int | None,
    headers: Mapping[bytes, bytes],
    attempt: int,
    now: datetime,
    *,
    max_attempts: int = 3,
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504),
    base_delay: float = 2.0,
    max_delay: float = 3600.0,
    jitter: float = 0.2,
) -> float | None:
    if attempt > max_attempts or (status is not None and status not in retry_statuses):
        return None
    retry_after = parse_retry_after(headers, now)
    if retry_after is not None:
        return min(retry_after, max_delay)
    delay = min(max_delay, base_delay ** (attempt - 1))
    return delay + random.uniform(0.0, delay * jitter)
