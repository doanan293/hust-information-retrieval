from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from hust_crawler.config import CrawlerConfig
from .options import crawl_options_from_config
from .profiles import resolve_runtime_tuning
from .runner import run_crawl
from .seeds import parse_seed_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hust-crawl",
        description="Unified hybrid crawler for mixed hostname and exact URL seeds.",
    )
    parser.add_argument(
        "--input", "-i", type=str, required=True, help="Path to canonical URLs file"
    )
    parser.add_argument(
        "--output", "-o", type=str, required=True, help="Crawl output directory"
    )
    parser.add_argument(
        "--assets",
        type=str,
        default=None,
        choices=["content-only", "all"],
        help="download directly referenced semantic assets; default records references only",
    )
    parser.add_argument(
        "--reuse-content-from",
        type=Path,
        default=None,
        help="reuse a completed content-only crawl and download only its recorded assets",
    )
    parser.add_argument(
        "--max-query-variants-per-path",
        type=int,
        default=None,
        help="Maximum query parameter variants per path (default: 20)",
    )
    parser.add_argument(
        "--pagination-empty-pages",
        type=int,
        default=None,
        help="Consecutive empty pagination pages before stopping (default: 3)",
    )
    parser.add_argument(
        "--max-urls-per-host",
        type=int,
        default=None,
        help="Maximum URLs crawled per hostname (default: 100000)",
    )
    parser.add_argument(
        "--max-total-urls",
        type=int,
        default=None,
        help="Maximum total URLs crawled across all hosts (default: 1000000)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="safe-fast",
        choices=["safe-fast"],
        help="Crawl concurrency profile (default: safe-fast)",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=None,
        help="Maximum single file size in bytes (default: 100 MiB)",
    )
    parser.add_argument(
        "--max-total-file-bytes",
        type=int,
        default=None,
        help="Maximum total file storage in bytes (default: 100 GiB)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Global concurrency limit (overrides YAML)",
    )
    parser.add_argument(
        "--max-per-host",
        type=int,
        default=None,
        help="Per-host concurrency limit (overrides YAML)",
    )
    parser.add_argument(
        "--resource-limit-percent",
        type=int,
        default=None,
        help="CPU/RAM utilization threshold (overrides YAML)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/crawler.yaml"),
        help="Configuration YAML file path",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume an interrupted crawl run",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        default=False,
        help="Retry transient network failures while resuming",
    )
    parser.add_argument(
        "--retry-truncated",
        action="store_true",
        default=False,
        help="Requeue recoverable query-variant truncations while resuming",
    )
    parser.add_argument(
        "--retry-access-gates",
        action="store_true",
        default=False,
        help="Requeue pages previously skipped as CAPTCHA-blocked while resuming",
    )
    parser.add_argument(
        "--retry-policy-skips",
        action="store_true",
        default=False,
        help="Migrate access policy and requeue recoverable policy skips while resuming",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if args.reuse_content_from is not None and args.assets not in (None, "all"):
        print("Error: --reuse-content-from requires --assets all", file=sys.stderr)
        return 2
    if args.retry_failed and not args.resume:
        print("Error: --retry-failed requires --resume", file=sys.stderr)
        return 2
    if args.retry_truncated and not args.resume:
        print("Error: --retry-truncated requires --resume", file=sys.stderr)
        return 2
    if args.retry_access_gates and not args.resume:
        print("Error: --retry-access-gates requires --resume", file=sys.stderr)
        return 2
    if args.retry_policy_skips and not args.resume:
        print("Error: --retry-policy-skips requires --resume", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        seeds = parse_seed_file(input_path)
    except Exception as exc:
        print(f"Error reading seed file: {exc}", file=sys.stderr)
        return 2

    try:
        config = CrawlerConfig.load(args.config, hostnames=seeds.allowed_hostnames)
    except Exception as exc:
        print(f"Error loading configuration: {exc}", file=sys.stderr)
        return 2

    if args.reuse_content_from is not None and config.assets != "all" and args.assets is None:
        print("Error: --reuse-content-from requires --assets all", file=sys.stderr)
        return 2

    options = crawl_options_from_config(
        config,
        assets=args.assets,
        max_file_bytes=args.max_file_bytes,
        max_total_file_bytes=args.max_total_file_bytes,
        max_query_variants_per_path=args.max_query_variants_per_path,
        pagination_empty_pages=args.pagination_empty_pages,
        max_urls_per_host=args.max_urls_per_host,
        max_total_urls=args.max_total_urls,
    )

    tuning = resolve_runtime_tuning(
        config=config,
        profile=args.profile,
        concurrency=args.concurrency,
        max_per_host=args.max_per_host,
        resource_limit_percent=args.resource_limit_percent,
    )

    try:
        return run_crawl(
            input_path=input_path,
            output=Path(args.output),
            config=config,
            seeds=seeds,
            options=options,
            tuning=tuning,
            resume=args.resume,
            retry_failed=args.retry_failed,
            retry_truncated=args.retry_truncated,
            retry_access_gates=args.retry_access_gates,
            retry_policy_skips=args.retry_policy_skips,
            reuse_content_from=args.reuse_content_from,
        )
    except (FileExistsError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
