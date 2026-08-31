from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from .archive_validation import validate_run
from .coverage import CoverageLedger
from .config import CrawlerConfig, validate_network_contact
from .cdxj import finalize_indexes, recover_run_index
from .middlewares import should_abort_browser_request
from .run_store import RunPaths, RunStore
from .settings import settings as crawler_settings
from .spiders.public_sites import PublicSitesSpider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hust-crawl")
    parser.add_argument("command", choices=("validate-config", "preflight", "crawl", "pilot", "resume", "validate-archive"))
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--config", type=Path, default=Path("config/crawler.yaml"))
    parser.add_argument("--domains", type=Path, default=Path("docs/domain_active.txt"))
    parser.add_argument("--max-pages-per-host", type=int)
    parser.add_argument("--max-requests-per-host", type=int)
    parser.add_argument("--time-limit-seconds", type=int)
    return parser


def _load(args: argparse.Namespace) -> CrawlerConfig:
    return CrawlerConfig.load(args.config, args.domains)


def _config_snapshot(
    config: CrawlerConfig,
    mode: str,
    max_pages_per_host: int | None,
    time_limit_seconds: int | None,
    max_requests_per_host: int | None = None,
    pilot: bool = False,
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "mode": mode,
        "hostnames": sorted(config.hostnames),
        "playwright_hosts": sorted(config.playwright_hosts),
        "contact": config.contact,
        "concurrent_requests": config.concurrent_requests,
        "concurrent_per_host": config.concurrent_per_host,
        "throttle_start_seconds": config.throttle_start_seconds,
        "throttle_max_seconds": config.throttle_max_seconds,
        "retry_times": config.retry_times,
        "retry_statuses": list(config.retry_statuses),
        "retry_backoff_base_seconds": config.retry_backoff_base_seconds,
        "retry_max_delay_seconds": config.retry_max_delay_seconds,
        "retry_jitter": config.retry_jitter,
        "download_timeout_seconds": config.download_timeout_seconds,
        "playwright_auto_fallback": config.playwright_auto_fallback,
        "playwright_abort_resource_types": list(config.playwright_abort_resource_types),
        "sitemap_max_depth": config.sitemap_max_depth,
        "trap_query_keys": list(config.trap_query_keys),
        "trap_exceptions": [[host, list(reasons)] for host, reasons in config.trap_exceptions],
        "accepted_mime_types": list(config.accepted_mime_types),
        "accepted_mime_prefixes": list(config.accepted_mime_prefixes),
        "media_extensions": list(config.media_extensions),
        "max_response_bytes": config.max_response_bytes,
        "warc_rotate_bytes": config.warc_rotate_bytes,
        "min_free_bytes": config.min_free_bytes,
        "min_free_percent": config.min_free_percent,
        "max_pages_per_host": max_pages_per_host,
        "time_limit_seconds": time_limit_seconds,
        "max_requests_per_host": max_requests_per_host,
        "pilot": pilot,
    }


def _effective_settings(
    config: CrawlerConfig,
    run_id: str,
    paths: RunPaths,
    time_limit_seconds: int | None = None,
    max_requests_per_host: int | None = None,
) -> Settings:
    settings = Settings(values=crawler_settings)
    settings.set("ITEM_PIPELINES", {"hust_crawler.pipelines.CapturePipeline": 300})
    settings.set("CRAWL_DATA_DIR", str(config.data_dir))
    settings.set("CRAWL_RUN_ID", run_id)
    settings.set("JOBDIR", str(paths.state))
    settings.set("CRAWL_ALLOWED_HOSTS", sorted(config.hostnames))
    settings.set("CRAWL_MAX_REQUESTS_PER_HOST", max_requests_per_host or 0)
    settings.set("CRAWL_PLAYWRIGHT_HOSTS", sorted(config.playwright_hosts))
    settings.set("CRAWL_RETRY_TIMES", config.retry_times)
    settings.set("CRAWL_RETRY_STATUSES", list(config.retry_statuses))
    settings.set("CRAWL_RETRY_BACKOFF_BASE", config.retry_backoff_base_seconds)
    settings.set("CRAWL_RETRY_MAX_DELAY", config.retry_max_delay_seconds)
    settings.set("CRAWL_RETRY_JITTER", config.retry_jitter)
    settings.set("CRAWL_MAX_RESPONSE_BYTES", config.max_response_bytes)
    settings.set("CRAWL_WARC_ROTATE_BYTES", config.warc_rotate_bytes)
    settings.set("CRAWL_MIN_FREE_BYTES", config.min_free_bytes)
    settings.set("CRAWL_MIN_FREE_PERCENT", config.min_free_percent)
    settings.set("CRAWL_SITEMAP_MAX_DEPTH", config.sitemap_max_depth)
    settings.set("CRAWL_PLAYWRIGHT_AUTO_FALLBACK", config.playwright_auto_fallback)
    settings.set("CRAWL_ACCEPTED_MIME_TYPES", list(config.accepted_mime_types))
    settings.set("CRAWL_ACCEPTED_MIME_PREFIXES", list(config.accepted_mime_prefixes))
    settings.set("CRAWL_MEDIA_EXTENSIONS", list(config.media_extensions))
    settings.set("CONCURRENT_REQUESTS", config.concurrent_requests)
    settings.set("CONCURRENT_REQUESTS_PER_DOMAIN", config.concurrent_per_host)
    settings.set("AUTOTHROTTLE_START_DELAY", config.throttle_start_seconds)
    settings.set("AUTOTHROTTLE_MAX_DELAY", config.throttle_max_seconds)
    settings.set("DOWNLOAD_TIMEOUT", config.download_timeout_seconds)
    settings.set("DOWNLOAD_MAXSIZE", config.max_response_bytes)
    settings.set("DOWNLOAD_WARNSIZE", int(config.max_response_bytes * 0.9))
    settings.set("ROBOTSTXT_OBEY", False)
    settings.set("DOWNLOADER_MIDDLEWARES", {
        "hust_crawler.middlewares.ScopeDownloaderMiddleware": 40,
        "hust_crawler.middlewares.RequestBudgetMiddleware": 50,
        "hust_crawler.middlewares.CrawlerRetryMiddleware": 60,
        "hust_crawler.middlewares.HttpsRootFallbackMiddleware": 70,
        "hust_crawler.middlewares.PlaywrightRoutingMiddleware": 80,
    })
    settings.set("EXTENSIONS", {
        "hust_crawler.coverage.LifecycleTracker": 400,
        "hust_crawler.middlewares.DownloadSizeGuard": 500,
    })
    settings.set("DOWNLOAD_HANDLERS", {
        "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    })
    settings.set("TWISTED_REACTOR", "twisted.internet.asyncioreactor.AsyncioSelectorReactor")
    settings.set("PLAYWRIGHT_ABORT_REQUEST", should_abort_browser_request)
    settings.set("PLAYWRIGHT_BROWSER_TYPE", "chromium")
    settings.set("USER_AGENT", f"HUSTPublicCrawler/0.1 (+{config.contact})")
    settings.set("LOG_LEVEL", "WARNING")
    if time_limit_seconds is not None:
        settings.set("CLOSESPIDER_TIMEOUT", time_limit_seconds)
    return settings


def _run_crawl(
    config: CrawlerConfig,
    mode: str,
    run_id: str,
    max_pages_per_host: int | None = None,
    time_limit_seconds: int | None = None,
    max_requests_per_host: int | None = None,
    pilot: bool = False,
    resume: bool = False,
) -> int:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    paths = RunPaths.for_run(config.data_dir, run_id)
    if resume:
        try:
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
            if manifest.get("status") not in {"interrupted", "failed", "time_limit"}:
                raise ValueError(f"run is not resumable: {manifest.get('status')}")
            stored_config = manifest.get("config")
            if manifest.get("config", {}).get("schema_version") != 3:
                raise ValueError("run manifest uses legacy schema; start a new run instead of resuming")
            if not isinstance(stored_config, dict) or "hostnames" not in stored_config:
                raise ValueError("run has no resumable configuration snapshot")
            max_pages_per_host = stored_config.get("max_pages_per_host")
            time_limit_seconds = stored_config.get("time_limit_seconds")
            max_requests_per_host = stored_config.get("max_requests_per_host")
            pilot = bool(stored_config.get("pilot", False))
            expected_config = _config_snapshot(
                config, mode, max_pages_per_host, time_limit_seconds,
                max_requests_per_host, pilot,
            )
            if stored_config != expected_config:
                raise ValueError("current configuration differs from the original run")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
    elif paths.root.exists():
        print(json.dumps({"error": f"run already exists: {run_id}"}), file=sys.stderr)
        return 2
    store = RunStore(paths)
    try:
        store.acquire_lock()
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    try:
        if resume:
            paths.state.mkdir(parents=True, exist_ok=True)
            recover_run_index(config.data_dir, run_id)
            store.resume()
        else:
            paths.root.mkdir(parents=True, exist_ok=False)
            paths.state.mkdir(parents=True, exist_ok=True)
            store.start(
                _config_snapshot(config, mode, max_pages_per_host, time_limit_seconds,
                                 max_requests_per_host, pilot)
            )
        settings = _effective_settings(config, run_id, paths, time_limit_seconds,
                                       max_requests_per_host)
        process = CrawlerProcess(settings)
        crawler = process.create_crawler(PublicSitesSpider)
        process.crawl(
            crawler,
            hostnames=config.hostnames,
            mode=mode,
            max_pages_per_host=max_pages_per_host,
            history_index=config.data_dir / "indexes" / "latest.cdxj",
            playwright_hosts=config.playwright_hosts,
            sitemap_max_depth=config.sitemap_max_depth,
            playwright_auto_fallback=config.playwright_auto_fallback,
            pilot=pilot,
        )
        process.start()
        finalize_indexes(config.data_dir, run_id)
        finish_reason = crawler.stats.get_value("finish_reason") or "finished"
        interrupted_reasons = {"shutdown", "closespider_timeout", "closespider_itemcount", "storage_watermark", "time_limit"}
        interrupted = finish_reason in interrupted_reasons
        spider_exception_count = int(crawler.stats.get_value("spider_exceptions/count") or 0)
        run_index = config.data_dir / "indexes" / "runs" / f"{run_id}.cdxj"
        archive_result = validate_run(config.data_dir, run_id) if run_index.exists() else {
            "status": "valid", "errors": [], "records": 0
        }
        coverage = CoverageLedger.load(paths.lifecycle).summarize(
            config.hostnames,
            archive_valid=archive_result["status"] == "valid",
            interrupted=interrupted,
            internal_error=spider_exception_count > 0,
        )
        status = coverage.status
        if mode == "preflight" and status == "complete":
            status = "preflight_complete"
        crawler_stats = {
            key: value for key in (
                "downloader/request_count", "downloader/response_count", "retry/count",
                "spider_exceptions/count", "finish_reason",
            ) if (value := crawler.stats.get_value(key)) is not None
        }
        raw_stats = crawler.stats.get_stats() if hasattr(crawler.stats, "get_stats") else {}
        crawler_stats["downloader/response_status_count"] = {
            key.removeprefix("downloader/response_status_count/"): value
            for key, value in raw_stats.items() if key.startswith("downloader/response_status_count/")
        }
        crawler_stats["request_budget/attempts"] = {
            key.removeprefix("request_budget/attempts/"): value
            for key, value in raw_stats.items() if key.startswith("request_budget/attempts/")
        }
        crawler_stats["request_budget/rejected"] = {
            key.removeprefix("request_budget/rejected/"): value
            for key, value in raw_stats.items() if key.startswith("request_budget/rejected/")
        }
        store.finish(
            status,
            {
                "mode": mode,
                "hostname_count": len(config.hostnames),
                "finish_reason": finish_reason,
                "archive_validation": archive_result,
                "coverage": coverage.to_dict(),
                "crawler_stats": crawler_stats,
            },
        )
        return {"complete": 0, "preflight_complete": 0, "complete_with_gaps": 3, "failed": 1, "interrupted": 130}.get(status, 1)
    except KeyboardInterrupt:
        if paths.manifest.exists():
            store.finish("interrupted", {"mode": mode})
        return 130
    except Exception as exc:
        if paths.manifest.exists():
            store.append_error({"error": str(exc), "type": type(exc).__name__})
            store.finish("failed", {"mode": mode})
        else:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        store.release_lock()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = _load(args)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    if args.command == "validate-config":
        digest = hashlib.sha256("\n".join(sorted(config.hostnames)).encode()).hexdigest()
        print(json.dumps({"hostname_count": len(config.hostnames), "allowlist_sha256": digest}, sort_keys=True))
        return 0
    if args.command in {"preflight", "pilot", "crawl", "resume"}:
        try:
            validate_network_contact(config.contact)
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
    if args.command == "pilot":
        if args.max_pages_per_host is None or args.max_pages_per_host <= 0:
            print("--max-pages-per-host must be positive", file=sys.stderr)
            return 2
        if args.time_limit_seconds is None or args.time_limit_seconds <= 0:
            print("--time-limit-seconds must be positive", file=sys.stderr)
            return 2
        if args.max_requests_per_host is not None and args.max_requests_per_host <= 0:
            print("--max-requests-per-host must be positive", file=sys.stderr)
            return 2
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.command == "preflight":
        return _run_crawl(config, "preflight", run_id)
    if args.command in {"crawl", "pilot"}:
        return _run_crawl(
            config,
            "crawl",
            run_id,
            max_pages_per_host=args.max_pages_per_host if args.command == "pilot" else None,
            time_limit_seconds=args.time_limit_seconds if args.command == "pilot" else None,
            max_requests_per_host=(args.max_requests_per_host or 10) if args.command == "pilot" else None,
            pilot=args.command == "pilot",
        )
    if args.command == "resume":
        if not args.run_id:
            print("resume requires RUN_ID", file=sys.stderr)
            return 2
        return _run_crawl(config, "crawl", args.run_id, resume=True)
    if args.command == "validate-archive":
        target = args.run_id or "LATEST"
        if target == "LATEST":
            indexes = sorted((config.data_dir / "indexes" / "runs").glob("*.cdxj"))
            if indexes:
                target = indexes[-1].stem
        result = validate_run(config.data_dir, target)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "valid" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
