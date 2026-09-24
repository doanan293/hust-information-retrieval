from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, Iterator
from urllib.parse import urlsplit

import scrapy

from hust_crawler.config import CrawlerConfig

from .assets import AssetPolicy
from .options import CrawlOptions
from .reuse import ReusedAssetTarget
from .spider import UnifiedSpider
from .writer import CrawlWriter


class ReusedAssetSpider(UnifiedSpider):
    name = "hust_reused_assets"

    def __init__(
        self,
        *,
        config: CrawlerConfig,
        options: CrawlOptions,
        writer: CrawlWriter,
        policy: AssetPolicy,
        targets: Iterable[ReusedAssetTarget],
        **kwargs: Any,
    ) -> None:
        scrapy.Spider.__init__(self, **kwargs)
        self.config = config
        self.options = options
        self.writer = writer
        self.policy = policy
        self.targets = targets

    def start_requests(self) -> Iterator[scrapy.Request]:
        for target in self.targets:
            for referring_page in target.referring_pages:
                self.writer.state.add_asset_referrer(target.url, referring_page, target.role)
            self.writer.record_scheduled(
                {
                    "url": target.url,
                    "status": "scheduled",
                    "seed_type": "exact",
                    "discovered_from": (
                        target.referring_pages[0] if target.referring_pages else None
                    ),
                    "frontier_action": "scheduled",
                    "discovery_source": "reused_content",
                    "response_purpose": "asset",
                }
            )
            hostname = (urlsplit(target.url).hostname or "").lower().rstrip(".")
            yield scrapy.Request(
                target.url,
                callback=self.parse_response,
                errback=self.on_request_error,
                priority=500,
                meta={
                    "allowed_hostnames": frozenset({hostname}),
                    "input_url": target.url,
                    "seed_mode": "exact",
                    "response_purpose": "asset",
                    "discovery_source": "reused_content",
                    "request_scope": "asset",
                    "asset_mode": "all",
                    "asset_group": target.group,
                    "asset_role": target.role,
                    "referring_page": (
                        target.referring_pages[0] if target.referring_pages else None
                    ),
                    "handle_httpstatus_all": True,
                },
                dont_filter=True,
            )

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for request in self.start_requests():
            yield request
