from hust_crawler.settings import settings


def test_polite_settings() -> None:
    assert settings["ROBOTSTXT_OBEY"] is False
    assert settings["CONCURRENT_REQUESTS_PER_DOMAIN"] == 1
    assert settings["DOWNLOAD_MAXSIZE"] == 100 * 1024 * 1024
