from __future__ import annotations

from scrapy import signals


class SpiderErrorRecorder:
    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.on_spider_error, signal=signals.spider_error)
        return spider

    def on_spider_error(self, failure, response) -> None:
        request = response.request
        input_url = (
            request.meta.get("input_url", response.url)
            if request is not None
            else response.url
        )
        if self.writer is not None:
            self.writer.write_error(
                {
                    "url": input_url,
                    "error": "spider_exception",
                    "message": str(failure.value),
                }
            )
