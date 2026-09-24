from __future__ import annotations

from pathlib import Path

from ..config import CrawlerConfig


def build_settings(
    config: CrawlerConfig,
    *,
    state_dir: Path,
    phase: str | None = None,
    tuning: object | None = None,
    extensions: dict[object, int] | None = None,
) -> dict[str, object]:
    from .adaptive import AdaptiveConcurrencyMiddleware
    from .middlewares import (
        ContentOnlyHeadersGuard,
        PlaywrightFallbackMiddleware,
        PoliteRetryMiddleware,
        PublicAssetAddressMiddleware,
        SafeRedirectMiddleware,
    )

    concurrency = getattr(tuning, "max_concurrency", config.concurrent_requests)
    per_host = getattr(tuning, "max_per_host", config.concurrent_per_host)
    playwright_pages = getattr(tuning, "playwright_max_pages", config.playwright_max_pages)
    settings: dict[str, object] = {
        # Discovery must always be able to fetch robots.txt and declared
        # sitemaps. The fixed-inventory content phase applies its rules.
        "ROBOTSTXT_OBEY": config.robots_txt_obey if phase == "crawl" else False,
        "ROBOTSTXT_USER_AGENT": config.browser_user_agent,
        "USER_AGENT": config.browser_user_agent,
        "COOKIES_ENABLED": config.cookies_enabled,
        "CONCURRENT_REQUESTS": concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": per_host,
        "DOWNLOAD_DELAY": config.download_delay_seconds,
        "AUTOTHROTTLE_ENABLED": config.autothrottle_enabled,
        "AUTOTHROTTLE_START_DELAY": config.throttle_start_seconds,
        "AUTOTHROTTLE_MAX_DELAY": config.throttle_max_seconds,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": config.autothrottle_target_concurrency,
        "RETRY_ENABLED": False,
        "DOWNLOAD_MAXSIZE": config.max_response_bytes,
        "DOWNLOAD_WARNSIZE": config.max_response_bytes,
        "DOWNLOAD_TIMEOUT": config.download_timeout_seconds,
        "REDIRECT_MAX_TIMES": config.redirect_max_times,
        "HTTPERROR_ALLOW_ALL": True,
        "REDIRECT_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_FORMAT": "%(message)s",
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "JOBDIR": str(state_dir),
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": playwright_pages,
        "DOWNLOADER_MIDDLEWARES": {
            # Asset DNS safety must run before Scrapy's RobotsTxtMiddleware
            # (priority 100), otherwise Scrapy can fetch the asset host's
            # robots.txt before the host is proven public.
            PublicAssetAddressMiddleware: 50,
            SafeRedirectMiddleware: 550,
            PoliteRetryMiddleware: 560,
            PlaywrightFallbackMiddleware: 570,
            AdaptiveConcurrencyMiddleware: 580,
        },
        "HUST_CONFIG": config,
    }
    if phase:
        settings["HUST_PHASE"] = phase
    if tuning is not None:
        settings["HUST_TUNING"] = tuning
    ext_dict: dict[object, int] = {ContentOnlyHeadersGuard: 100}
    if extensions:
        ext_dict.update(extensions)
    settings["EXTENSIONS"] = ext_dict
    return settings
