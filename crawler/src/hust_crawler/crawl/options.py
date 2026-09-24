from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal

from .seeds import SeedSet
from ..config import CrawlerConfig

AssetMode = Literal["content-only", "all"]
ASSET_POLICY_VERSION: Final[int] = 2


@dataclass(frozen=True, slots=True)
class CrawlOptions:
    assets: AssetMode = "content-only"
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_file_bytes: int = 100 * 1024**3
    max_query_variants_per_path: int = 20
    pagination_empty_pages: int = 3
    max_urls_per_host: int = 100_000
    max_total_urls: int = 1_000_000

    def __post_init__(self) -> None:
        if self.assets not in {"content-only", "all"}:
            raise ValueError("assets must be content-only or all")
        for name, value in asdict(self).items():
            if name != "assets" and int(value) <= 0:
                raise ValueError(f"{name} must be positive")

    def semantic_snapshot(self, seeds: SeedSet | None = None) -> dict[str, object]:
        return {
            "crawl_strategy": "hybrid-unified",
            **asdict(self),
            "asset_policy_version": ASSET_POLICY_VERSION,
            "recursive_hostnames": sorted(seeds.recursive_hostnames) if seeds else [],
            "exact_urls": list(seeds.exact_urls) if seeds else [],
        }


def crawl_options_from_config(config: CrawlerConfig, **overrides: object) -> CrawlOptions:
    names = (
        "assets", "max_file_bytes", "max_total_file_bytes",
        "max_query_variants_per_path", "pagination_empty_pages",
        "max_urls_per_host", "max_total_urls",
    )
    values = {
        name: overrides[name] if overrides.get(name) is not None else getattr(config, name)
        for name in names
    }
    return CrawlOptions(**values)
