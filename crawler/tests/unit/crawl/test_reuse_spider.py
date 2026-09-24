from __future__ import annotations

import json

from scrapy.http import Request, Response

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.assets import AssetPolicy
from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.reuse import ReusedAssetTarget
from hust_crawler.crawl.reuse_spider import ReusedAssetSpider
from hust_crawler.crawl.state import CrawlState
from hust_crawler.crawl.writer import CrawlWriter


def make_spider(tmp_path) -> tuple[ReusedAssetSpider, CrawlState]:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    options = CrawlOptions(assets="all")
    state = CrawlState.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    writer = CrawlWriter(tmp_path / "crawl-all", state, options)
    target = ReusedAssetTarget(
        url="https://cdn.test/image?id=42",
        role="inline_image",
        group="image",
        referring_pages=("https://a.test/one", "https://a.test/two"),
    )
    spider = ReusedAssetSpider(
        config=CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.org"),
        options=options,
        writer=writer,
        policy=AssetPolicy(options),
        targets=(target,),
    )
    return spider, state


def test_reused_asset_spider_schedules_only_recorded_asset_urls(tmp_path) -> None:
    spider, state = make_spider(tmp_path)

    requests = list(spider.start_requests())

    assert [request.url for request in requests] == ["https://cdn.test/image?id=42"]
    assert requests[0].meta["request_scope"] == "asset"
    assert requests[0].meta["asset_role"] == "inline_image"
    assert requests[0].meta["asset_group"] == "image"
    state.close()


def test_reused_asset_spider_stores_binary_and_all_referrers(tmp_path) -> None:
    spider, state = make_spider(tmp_path)
    request = next(spider.start_requests())
    response = Response(
        request.url,
        status=200,
        headers={b"Content-Type": b"image/png"},
        body=b"image-body",
        request=Request(request.url, meta=request.meta),
    )

    list(spider.parse_response(response))

    payload = json.loads(state.connection.execute("SELECT payload FROM files").fetchone()[0])
    assert payload["asset_group"] == "image"
    assert payload["asset_role"] == "inline_image"
    assert payload["referring_pages"] == ["https://a.test/one", "https://a.test/two"]
    state.close()
