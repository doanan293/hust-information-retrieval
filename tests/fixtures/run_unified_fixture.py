from __future__ import annotations

from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).parents[2]))

from scrapy.utils.reactor import install_reactor

install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

from scrapy import signals  # noqa: E402
from hust_crawler.crawl.adaptive import AdaptiveConcurrencyMiddleware  # noqa: E402
from hust_crawler.crawl import cli  # noqa: E402
from hust_crawler.crawl import runner  # noqa: E402
import hust_crawler.crawl.settings as crawl_settings  # noqa: E402
from hust_crawler.policies.network import NonPublicAddressError  # noqa: E402


class InterruptExtension:
    @classmethod
    def from_crawler(cls, crawler):
        ext = cls(crawler)
        crawler.signals.connect(ext.on_response, signal=signals.response_downloaded)
        return ext

    def __init__(self, crawler):
        self.crawler = crawler

    def on_response(self, response, **kw):
        if response.url == "https://a.test/article":
            self.crawler.engine.close_spider(self.crawler.spider, "shutdown")


def main(root: Path, mode: str = "complete", assets: str = "content-only") -> int:
    root.mkdir(parents=True, exist_ok=True)
    seeds_file = root / "seeds.txt"
    if not seeds_file.exists():
        if mode == "cycle":
            seeds_file.write_text("cycle.test\n", encoding="utf-8")
        elif mode == "access_gate":
            seeds_file.write_text(
                "https://a.test/captcha-page\nhttps://a.test/login-page\n",
                encoding="utf-8",
            )
        elif mode == "retry":
            seeds_file.write_text("https://a.test/retry-page\n", encoding="utf-8")
        elif mode == "generated_graph":
            seeds_file.write_text("a.test\nhttps://a.test/news?page=1\n", encoding="utf-8")
        else:
            seeds_file.write_text(
                "map.test\nhtml-only.test\na.test\nhttps://b.test/explicit\n",
                encoding="utf-8",
            )

    config_file = root / "config.yaml"
    if not config_file.exists():
        values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
        values.update(contact="ops@example.org", retry_backoff_base_seconds=0.01, download_delay_seconds=0.001)
        config_file.write_text(yaml.safe_dump(values), encoding="utf-8")

    original_build_settings = crawl_settings.build_settings
    request_log_path = root / "requested_urls.txt"

    def fixture_build_settings(cfg, *, state_dir, phase: str = "crawl", tuning=None, extensions=None):
        vals = original_build_settings(
            cfg, state_dir=state_dir, phase=phase, tuning=tuning, extensions=extensions
        )
        vals["DOWNLOAD_HANDLERS"] = {
            "http": "tests.fixtures.scrapy_download_handler.StaticDownloadHandler",
            "https": "tests.fixtures.scrapy_download_handler.StaticDownloadHandler",
        }
        vals["FIXTURE_REQUEST_LOG"] = str(request_log_path)
        vals["FIXTURE_MODE"] = mode

        async def fixture_resolver(hostname: str, port: int = 443, timeout_seconds: float = 5.0) -> None:
            if mode == "reuse_private_dns" and hostname == "cdn.test":
                raise NonPublicAddressError("hostname resolves to non-public address")
            return None

        vals["HUST_PUBLIC_RESOLVER"] = fixture_resolver

        if mode == "interrupt":
            vals.setdefault("EXTENSIONS", {})[InterruptExtension] = 50
        vals.setdefault("DOWNLOADER_MIDDLEWARES", {})[AdaptiveConcurrencyMiddleware] = None
        return vals

    crawl_settings.build_settings = fixture_build_settings
    runner.build_settings = fixture_build_settings

    reuse_source = root / "crawl" if mode.startswith("reuse") else None
    output_dir = root / "crawl-all" if reuse_source is not None else root / "crawl"
    argv = [
        "--input",
        str(seeds_file),
        "--output",
        str(output_dir),
        "--config",
        str(config_file),
        "--assets",
        assets,
        "--profile",
        "safe-fast",
        "--concurrency",
        "4",
        "--max-per-host",
        "2",
    ]
    if reuse_source is not None:
        argv.extend(["--reuse-content-from", str(reuse_source)])
    if mode == "resume":
        argv.append("--resume")

    return cli.main(argv)


if __name__ == "__main__":
    mode_arg = sys.argv[2] if len(sys.argv) > 2 else "complete"
    assets_arg = sys.argv[3] if len(sys.argv) > 3 else "content-only"
    if len(sys.argv) > 4:
        assets_arg = sys.argv[4]
    raise SystemExit(main(Path(sys.argv[1]), mode_arg, assets=assets_arg))
