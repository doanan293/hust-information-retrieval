from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from hust_crawler.config import CrawlerConfig
from .discovery_runner import run_discovery
from .profiles import resolve_runtime_tuning
from .seeds import parse_seed_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hust-discover",
        description="Sitemap-only URL discovery for mixed hostname and exact URL seeds.",
    )
    parser.add_argument("--input", "-i", type=str, required=True, help="Path to seeds file")
    parser.add_argument(
        "--output", "-o", type=str, required=True, help="Discovery output directory"
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="safe-fast",
        choices=["safe-fast"],
        help="Crawl concurrency profile (default: safe-fast)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Global concurrency limit (default: 64)",
    )
    parser.add_argument(
        "--max-per-host",
        type=int,
        default=None,
        help="Per-host concurrency limit (default: 6)",
    )
    parser.add_argument(
        "--resource-limit-percent",
        type=int,
        default=None,
        help="CPU/RAM utilization threshold (default: 80)",
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
        help="Resume an interrupted discovery run",
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

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        seeds = parse_seed_file(input_path)
    except ValueError as exc:
        print(f"Error reading seeds: {exc}", file=sys.stderr)
        return 2

    try:
        config = CrawlerConfig.load(args.config, hostnames=seeds.allowed_hostnames)
    except Exception as exc:
        print(f"Error loading configuration: {exc}", file=sys.stderr)
        return 2

    tuning = resolve_runtime_tuning(
        config=config,
        profile=args.profile,
        concurrency=args.concurrency,
        max_per_host=args.max_per_host,
        resource_limit_percent=args.resource_limit_percent,
    )

    return run_discovery(
        input_path=input_path,
        output=Path(args.output),
        config=config,
        seeds=seeds,
        tuning=tuning,
        resume=args.resume,
    )


if __name__ == "__main__":
    sys.exit(main())
