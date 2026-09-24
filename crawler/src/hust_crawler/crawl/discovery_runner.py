from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time
from urllib.parse import urlsplit

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from hust_crawler.config import CrawlerConfig, semantic_config_snapshot
from .discovery_spider import DiscoverySpider
from .discovery_writer import DiscoveryWriter
from .profiles import RuntimeTuning
from .runner import finish_reason_to_exit
from .seeds import SeedSet
from .settings import build_settings
from .sitemaps import SitemapDiscoveryCoordinator
from .state import CrawlState


def run_discovery(
    input_path: Path,
    output: Path,
    config: CrawlerConfig,
    seeds: SeedSet,
    tuning: RuntimeTuning,
    resume: bool = False,
) -> int:
    state_dir = output / "state"
    semantic_cfg = dict(semantic_config_snapshot(config))
    runtime_cfg = asdict(tuning)

    state = CrawlState.open(
        state_dir,
        phase="discover",
        input_path=input_path,
        semantic_config=semantic_cfg,
        runtime_config=runtime_cfg,
        resume=resume,
    )
    writer = DiscoveryWriter(output, state)

    exact_hosts = frozenset(
        (urlsplit(u).hostname or "").lower().rstrip(".")
        for u in seeds.exact_urls
        if urlsplit(u).hostname
    ) - seeds.recursive_hostnames
    coordinator = SitemapDiscoveryCoordinator(
        exact_hostnames=exact_hosts, on_change=state.upsert_host_discovery
    )
    if resume:
        coordinator.restore(state.iter_host_discovery())

    for inv in seeds.invalid:
        err = {
            "url": inv.value,
            "line": inv.line,
            "error": inv.reason,
            "message": f"line {inv.line}: {inv.reason}",
        }
        state.put_error(err)
        state.upsert_url({"url": inv.value, "status": "failed", "reason": inv.reason})

    jobdir = state_dir / "scrapy-job"
    start_time = time.time()
    extensions: dict[object, int] = {}

    settings_map = build_settings(
        config,
        state_dir=jobdir,
        phase="discover",
        tuning=tuning,
        extensions=extensions,
    )
    settings = Settings()
    settings.setmodule("hust_crawler.crawl.settings")

    for k, v in settings_map.items():
        settings.set(k, v, priority="cmdline")

    process = CrawlerProcess(settings=settings)
    crawler = process.create_crawler(DiscoverySpider)

    exit_code = 1
    try:
        process.crawl(
            crawler,
            seeds=seeds,
            config=config,
            coordinator=coordinator,
            writer=writer,
        )
        process.start()

        stats = crawler.stats.get_stats() if crawler.stats else {}
        finish_reason = str(stats.get("finish_reason", "finished"))
        enqueued = int(stats.get("scheduler/enqueued", 0))
        dequeued = int(stats.get("scheduler/dequeued", 0))
        queued = max(0, enqueued - dequeued)

        counts = state.counts()
        failed = counts.get("errors", 0)

        status, exit_code = finish_reason_to_exit(
            finish_reason, queued=queued, failed=failed, truncated=0
        )
        writer.publish()
        elapsed_seconds = time.time() - start_time
        metrics = {
            "elapsed_seconds": elapsed_seconds,
            "stats": stats,
        }
        state.finish(
            status=status,
            exit_code=exit_code,
            counts=counts,
            metrics=metrics,
            discovery=coordinator.snapshot(),
            public_output=output,
        )
        return exit_code
    except KeyboardInterrupt:
        writer.publish()
        counts = state.counts()
        state.finish(
            "interrupted",
            exit_code=130,
            counts=counts,
            metrics={},
            discovery=coordinator.snapshot(),
            public_output=output,
        )
        return 130
    except Exception as exc:
        writer.publish()
        counts = state.counts()
        state.finish(
            "failed",
            exit_code=1,
            counts=counts,
            metrics={"error": str(exc)},
            discovery=coordinator.snapshot(),
            public_output=output,
        )
        return 1
    finally:
        writer.close()
