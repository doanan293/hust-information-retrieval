from __future__ import annotations

from pathlib import Path

from hust_crawler.crawl.state import CrawlState


class DiscoveryWriter:
    def __init__(self, root: Path, state: CrawlState) -> None:
        self.root = root
        self.state = state

    def record_target(self, record: dict[str, object]) -> None:
        self.state.put_target(record)

    def record_probe(self, record: dict[str, object]) -> None:
        self.state.upsert_url(record)

    def write_error(self, record: dict[str, object]) -> None:
        self.state.complete_url(
            {"url": record["url"], "status": "failed", "reason": record["error"]},
            error=record,
        )

    def checkpoint(self, url: str) -> None:
        self.state.checkpoint(url)

    def publish(self) -> None:
        self.state.export_discovery(self.root)

    def close(self) -> None:
        self.state.close()
