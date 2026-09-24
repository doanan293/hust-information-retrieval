from __future__ import annotations

from unittest.mock import MagicMock
from scrapy.http import Request, Response

from hust_crawler.crawl.errors import SpiderErrorRecorder


class DummySpider(SpiderErrorRecorder):
    def __init__(self, writer) -> None:
        self.writer = writer


def test_spider_error_recorder_writes_spider_exception() -> None:
    writer = MagicMock()
    spider = DummySpider(writer)

    request = Request("https://a.test/page", meta={"input_url": "https://a.test/page"})
    response = Response("https://a.test/page", request=request)
    failure = MagicMock()
    failure.value = ValueError("boom")

    spider.on_spider_error(failure, response)

    writer.write_error.assert_called_once_with(
        {
            "url": "https://a.test/page",
            "error": "spider_exception",
            "message": "boom",
        }
    )
