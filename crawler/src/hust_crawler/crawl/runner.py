from __future__ import annotations

from pathlib import Path
import shutil
import time
from typing import Literal
from urllib.parse import urlsplit

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from hust_crawler.config import CrawlerConfig, runtime_config_snapshot, semantic_config_snapshot
from .assets import AssetPolicy
from .frontier import FrontierPolicy
from .options import CrawlOptions
from .profiles import RuntimeTuning
from .reuse import (
    import_reused_content,
    prepare_reused_asset_targets,
    reused_asset_targets,
    validate_reuse_source,
)
from .reuse_spider import ReusedAssetSpider
from .seeds import SeedSet
from .settings import build_settings
from .sitemaps import SitemapDiscoveryCoordinator
from .spider import UnifiedSpider
from .state import CrawlState, validate_resume_workflow
from .writer import CrawlWriter

TRUNCATION_REASONS = frozenset(
    {
        "file_size_limit",
        "storage_budget",
        "query_variant_limit",
        "host_url_limit",
        "total_url_limit",
    }
)


def _clean_failed_reuse_output(
    output: Path, state_dir: Path, *, output_existed: bool
) -> None:
    if output_existed:
        shutil.rmtree(state_dir, ignore_errors=True)
    else:
        shutil.rmtree(output, ignore_errors=True)


def finish_reason_to_exit(
    finish_reason: str,
    *,
    queued: int = 0,
    failed: int = 0,
    truncated: int = 0,
) -> tuple[
    Literal[
        "complete",
        "complete_with_failures",
        "truncated",
        "interrupted",
        "failed",
    ],
    int,
]:
    if finish_reason in {"shutdown", "signal"}:
        return "interrupted", 130
    if queued > 0 and finish_reason != "finished":
        return "interrupted", 130
    if truncated > 0:
        return "truncated", 4
    if failed > 0:
        return "complete_with_failures", 3
    if finish_reason == "finished":
        return "complete", 0
    return "failed", 1


def run_crawl(
    input_path: Path,
    output: Path,
    config: CrawlerConfig,
    seeds: SeedSet,
    options: CrawlOptions,
    tuning: RuntimeTuning,
    resume: bool = False,
    retry_failed: bool = False,
    retry_truncated: bool = False,
    retry_access_gates: bool = False,
    retry_policy_skips: bool = False,
    reuse_content_from: Path | None = None,
) -> int:
    if retry_failed and not resume:
        raise ValueError("--retry-failed requires --resume")
    if retry_truncated and not resume:
        raise ValueError("--retry-truncated requires --resume")
    if retry_access_gates and not resume:
        raise ValueError("--retry-access-gates requires --resume")
    if retry_policy_skips and not resume:
        raise ValueError("--retry-policy-skips requires --resume")
    if reuse_content_from is not None and resume:
        raise ValueError("--reuse-content-from cannot be combined with --resume")
    if reuse_content_from is not None and reuse_content_from.resolve() == output.resolve():
        raise ValueError("--reuse-content-from must differ from --output")
    if reuse_content_from is not None and output.exists():
        raise FileExistsError(f"reuse output already exists: {output}")
    if reuse_content_from is not None:
        validate_reuse_source(reuse_content_from, input_path)

    state_dir = output / "state"
    if resume:
        validate_resume_workflow(state_dir, expected="hybrid-unified")

    semantic_cfg = {
        **semantic_config_snapshot(config),
        **options.semantic_snapshot(seeds),
    }
    runtime_cfg = runtime_config_snapshot(config, tuning)

    output_existed = output.exists()
    state = CrawlState.open(
        state_dir,
        phase="crawl",
        input_path=input_path,
        semantic_config=semantic_cfg,
        runtime_config=runtime_cfg,
        resume=resume,
        allow_policy_migration=retry_policy_skips,
    )
    try:
        if reuse_content_from is not None:
            import_reused_content(reuse_content_from, state, input_path)
            prepare_reused_asset_targets(state)
        writer = CrawlWriter(output, state, options)
        policy = AssetPolicy(options)
        reused_targets = reused_asset_targets(state) if reuse_content_from is not None else ()
    except Exception:
        state.close()
        if reuse_content_from is not None:
            _clean_failed_reuse_output(
                output, state_dir, output_existed=output_existed
            )
        raise

    try:
        exact_hosts = frozenset(
            (urlsplit(u).hostname or "").lower().rstrip(".")
            for u in seeds.exact_urls
            if urlsplit(u).hostname
        ) - seeds.recursive_hostnames

        coordinator = SitemapDiscoveryCoordinator(
            exact_hostnames=exact_hosts,
            on_change=state.upsert_host_discovery,
            strategy="hybrid-unified",
        )
        frontier = FrontierPolicy(
            seeds, options, route_families=state.iter_route_families()
        )
        resume_records: tuple[dict[str, object], ...] = ()

        if resume:
            if retry_failed:
                state.requeue_retryable_failures()
            if retry_truncated:
                state.requeue_query_variant_truncations()
            if retry_access_gates:
                state.requeue_access_gates()
            if retry_policy_skips:
                state.requeue_policy_skips(seeds.recursive_hostnames)
                state.requeue_retryable_failures()
                state.requeue_zero_content_bootstrap(seeds.recursive_hostnames)
            frontier.restore(state.iter_url_records())
            coordinator.restore(state.iter_host_discovery())
            resume_records = tuple(state.iter_pending_scheduled_records())

        # Record invalid seeds
        for inv in seeds.invalid:
            err = {
                "url": inv.value,
                "line": inv.line,
                "error": inv.reason,
                "message": f"line {inv.line}: {inv.reason}",
            }
            state.put_error(err)
            state.upsert_url(
                {"url": inv.value, "status": "failed", "reason": inv.reason}
            )

        jobdir = state_dir / "scrapy-job"
        start_time = time.time()
        extensions: dict[object, int] = {}
        if tuning.progress_interval_seconds > 0:
            from .progress import CrawlProgressReporter

            extensions[CrawlProgressReporter] = 500

        settings_map = build_settings(
            config,
            state_dir=jobdir,
            phase="crawl",
            tuning=tuning,
            extensions=extensions,
        )
        if resume:
            settings_map.pop("JOBDIR", None)
        settings = Settings()
        settings.setmodule("hust_crawler.crawl.settings")

        for k, v in settings_map.items():
            settings.set(k, v, priority="cmdline")
        settings.set("HUST_CRAWL_OPTIONS", options, priority="cmdline")

        process = CrawlerProcess(settings=settings)
        spider_cls = (
            ReusedAssetSpider if reuse_content_from is not None else UnifiedSpider
        )
        crawler = process.create_crawler(spider_cls)
    except Exception:
        writer.close()
        if reuse_content_from is not None:
            _clean_failed_reuse_output(
                output, state_dir, output_existed=output_existed
            )
        raise

    exit_code = 1
    try:
        if reuse_content_from is not None:
            process.crawl(
                crawler,
                config=config,
                options=options,
                writer=writer,
                policy=policy,
                targets=reused_targets,
            )
        else:
            process.crawl(
                crawler,
                seeds=seeds,
                config=config,
                options=options,
                frontier=frontier,
                writer=writer,
                policy=policy,
                coordinator=coordinator,
                resume_records=resume_records,
            )
        process.start()

        stats = crawler.stats.get_stats() if crawler.stats else {}
        finish_reason = str(stats.get("finish_reason", "finished"))
        enqueued = int(stats.get("scheduler/enqueued", 0))
        dequeued = int(stats.get("scheduler/dequeued", 0))
        queued = max(0, enqueued - dequeued)

        counts = state.counts()
        failed = counts.get("errors", 0)
        host_completion = state.host_completion(seeds.recursive_hostnames)
        zero_content_hosts = sorted(
            hostname
            for hostname, summary in host_completion.items()
            if summary["status"] == "zero_content"
        )
        truncation_counts = state.reason_counts(TRUNCATION_REASONS)
        truncated = sum(truncation_counts.values())

        elapsed_seconds = time.time() - start_time
        completed = counts.get("completed_urls", 0)
        avg_rate = completed / max(0.001, elapsed_seconds)

        reporter = getattr(crawler, "progress_reporter", None)
        rep_metrics = reporter.metrics() if reporter is not None else {}
        peak_queue = rep_metrics.get("peak_queue", queued)
        recent_rate = rep_metrics.get("recent_rate", avg_rate)

        status, exit_code = finish_reason_to_exit(
            finish_reason, queued=queued, failed=failed, truncated=truncated
        )
        writer.publish()
        metrics = {
            "elapsed_seconds": elapsed_seconds,
            "average_rate": avg_rate,
            "recent_rate": recent_rate,
            "peak_queue": peak_queue,
            "stats": stats,
            "frontier": frontier.snapshot(),
            "zero_content_hosts": zero_content_hosts,
        }
        state.finish(
            status=status,
            exit_code=exit_code,
            counts=counts,
            metrics=metrics,
            truncation=truncation_counts if truncated > 0 else None,
            discovery=coordinator.snapshot(),
            frontier=frontier.snapshot(),
            host_completion=host_completion,
            public_output=output,
        )
        return exit_code
    except KeyboardInterrupt:
        writer.publish()
        counts = state.counts()
        metrics = {
            "elapsed_seconds": time.time() - start_time,
            "frontier": frontier.snapshot(),
        }
        state.finish(
            "interrupted",
            exit_code=130,
            counts=counts,
            metrics=metrics,
            discovery=coordinator.snapshot(),
            frontier=frontier.snapshot(),
            public_output=output,
        )
        return 130
    except Exception as exc:
        writer.publish()
        counts = state.counts()
        metrics = {
            "elapsed_seconds": time.time() - start_time,
            "error": str(exc),
            "frontier": frontier.snapshot(),
        }
        state.finish(
            "failed",
            exit_code=1,
            counts=counts,
            metrics=metrics,
            discovery=coordinator.snapshot(),
            frontier=frontier.snapshot(),
            public_output=output,
        )
        return 1
    finally:
        writer.close()
