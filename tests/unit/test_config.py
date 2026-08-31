from pathlib import Path

import pytest

from hust_crawler.config import CrawlerConfig, load_hostnames, validate_network_contact


def test_load_hostnames_normalizes_and_deduplicates(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("Example.COM\n# note\nexample.com\na.example.com\n", encoding="utf-8")

    assert load_hostnames(source) == frozenset({"example.com", "a.example.com"})


def test_load_hostnames_rejects_urls(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("https://example.com/path\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hostname"):
        load_hostnames(source)


def test_config_enforces_safety_defaults(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")

    config = CrawlerConfig.load(Path("config/crawler.yaml"), domains)

    assert config.concurrent_per_host == 1
    assert config.max_response_bytes == 100 * 1024 * 1024
    assert config.min_free_bytes == 50 * 1024**3


def test_config_loads_complete_runtime_policy(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = tmp_path / "crawler.yaml"
    raw.write_text(
        """data_dir: data
contact: ops@example.org
retry_times: 4
retry_statuses: [408, 425, 429, 500, 502, 503, 504]
retry_backoff_base_seconds: 2
retry_max_delay_seconds: 3600
download_timeout_seconds: 75
playwright_auto_fallback: true
""",
        encoding="utf-8",
    )

    config = CrawlerConfig.load(raw, domains)

    assert config.retry_times == 4
    assert config.retry_statuses == (408, 425, 429, 500, 502, 503, 504)
    assert config.playwright_auto_fallback is True
    assert config.download_timeout_seconds == 75


def test_environment_contact_overrides_yaml(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = tmp_path / "crawler.yaml"
    raw.write_text("contact: yaml@example.org\n", encoding="utf-8")

    config = CrawlerConfig.load(
        raw,
        domains,
        environ={"CRAWLER_CONTACT": "ops@example.org"},
    )

    assert config.contact == "ops@example.org"


@pytest.mark.parametrize(
    "contact",
    ["", "replace-with-operator-contact@example.invalid"],
)
def test_network_contact_rejects_placeholder(contact: str) -> None:
    with pytest.raises(ValueError, match="operator contact"):
        validate_network_contact(contact)


@pytest.mark.parametrize(
    ("field", "value"),
    [("retry_times", -1), ("download_timeout_seconds", 0),
     ("retry_max_delay_seconds", 0), ("max_response_bytes", 0)],
)
def test_config_rejects_non_positive_runtime_values(
    tmp_path: Path, field: str, value: int
) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = tmp_path / "crawler.yaml"
    raw.write_text(
        f"data_dir: data\ncontact: ops@example.org\n{field}: {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        CrawlerConfig.load(raw, domains)
