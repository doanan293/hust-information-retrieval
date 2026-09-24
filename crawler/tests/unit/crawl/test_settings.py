from pathlib import Path

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl.adaptive import AdaptiveConcurrencyMiddleware
from hust_crawler.crawl.middlewares import PublicAssetAddressMiddleware, SafeRedirectMiddleware
from hust_crawler.crawl.settings import build_settings


def config() -> CrawlerConfig:
    return CrawlerConfig(
        hostnames=frozenset({"a.test"}),
        contact="ops@example.org",
    )


def test_settings_enable_async_concurrency_and_bounded_browser(tmp_path: Path) -> None:
    values = build_settings(config(), state_dir=tmp_path / "job")
    assert values["CONCURRENT_REQUESTS"] == 64
    assert values["CONCURRENT_REQUESTS_PER_DOMAIN"] == 2
    assert values["AUTOTHROTTLE_ENABLED"] is True
    assert values["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 2.0
    assert values["RETRY_ENABLED"] is False
    assert values["PLAYWRIGHT_MAX_PAGES_PER_CONTEXT"] == 8
    assert values["JOBDIR"] == str(tmp_path / "job")
    assert values["DOWNLOAD_HANDLERS"]["https"] == "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
    assert AdaptiveConcurrencyMiddleware in values["DOWNLOADER_MIDDLEWARES"]


def test_multi_request_profile_registers_adaptive_middleware(tmp_path: Path) -> None:
    values = build_settings(
        CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.org", concurrent_per_host=4),
        state_dir=tmp_path / "job",
    )
    assert AdaptiveConcurrencyMiddleware in values["DOWNLOADER_MIDDLEWARES"]
    assert values["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 2.0


def test_content_crawl_ignores_robots_while_discovery_can_fetch_robots(tmp_path: Path) -> None:
    crawl = build_settings(config(), state_dir=tmp_path / "crawl", phase="crawl")
    discovery = build_settings(config(), state_dir=tmp_path / "discover", phase="discover")

    assert crawl["ROBOTSTXT_OBEY"] is False
    assert discovery["ROBOTSTXT_OBEY"] is False


def test_requests_use_browser_user_agent_without_contact_suffix(tmp_path: Path) -> None:
    values = build_settings(config(), state_dir=tmp_path / "job", phase="crawl")

    assert values["USER_AGENT"] == config().browser_user_agent
    assert values["ROBOTSTXT_USER_AGENT"] == config().browser_user_agent


def test_middlewares_register_public_asset_address_and_safe_redirect(tmp_path: Path) -> None:
    values = build_settings(config(), state_dir=tmp_path / "job")
    middlewares = values["DOWNLOADER_MIDDLEWARES"]
    assert middlewares[PublicAssetAddressMiddleware] == 50
    assert middlewares[SafeRedirectMiddleware] == 550
