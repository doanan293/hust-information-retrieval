from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import CrawlerConfig


@dataclass(frozen=True, slots=True)
class RuntimeTuning:
    profile: str
    max_concurrency: int
    max_per_host: int
    resource_limit_percent: int
    playwright_max_pages: int
    journal_batch_size: int
    journal_flush_seconds: float
    progress_interval_seconds: float


def resolve_runtime_tuning(
    *,
    config: CrawlerConfig | None = None,
    profile: str | None,
    concurrency: int | None = None,
    max_per_host: int | None = None,
    resource_limit_percent: int | None = None,
) -> RuntimeTuning:
    name = profile or "safe-fast"
    if name != "safe-fast":
        raise ValueError(f"unknown profile: {name}")
    if config is None:
        config = CrawlerConfig(hostnames=frozenset(), contact="ops@example.org")
    tuning = RuntimeTuning(
        profile=name,
        max_concurrency=concurrency if concurrency is not None else config.concurrent_requests,
        max_per_host=max_per_host if max_per_host is not None else config.concurrent_per_host,
        resource_limit_percent=(
            resource_limit_percent if resource_limit_percent is not None else config.resource_limit_percent
        ),
        playwright_max_pages=config.playwright_max_pages,
        journal_batch_size=config.journal_batch_size,
        journal_flush_seconds=config.journal_flush_seconds,
        progress_interval_seconds=config.progress_interval_seconds,
    )
    if tuning.max_concurrency <= 0 or tuning.max_per_host <= 0:
        raise ValueError("concurrency limits must be positive")
    if not 1 <= tuning.resource_limit_percent <= 100:
        raise ValueError("resource limit percent must be between 1 and 100")
    return tuning


def tuning_snapshot(tuning: RuntimeTuning) -> dict[str, object]:
    return asdict(tuning)
