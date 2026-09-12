from pathlib import Path

import pytest
import yaml

from hust_crawler.config import (
    CrawlerConfig,
    config_snapshot,
    load_hostnames,
    validate_network_contact,
    runtime_config_snapshot,
)
from hust_crawler.crawl.profiles import RuntimeTuning


def complete_yaml(tmp_path: Path, **overrides: object) -> Path:
    values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
    values.update(overrides)
    path = tmp_path / "crawler.yaml"
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return path


def test_load_hostnames_normalizes_and_deduplicates(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("Example.COM\n# note\nexample.com\na.example.com\n", encoding="utf-8")

    assert load_hostnames(source) == frozenset({"example.com", "a.example.com"})


def test_domain_active_uses_plain_hostname_per_line_format(tmp_path: Path) -> None:
    source = tmp_path / "domain_active.txt"
    source.write_text("b.hust.edu.vn\na.hust.edu.vn\n", encoding="utf-8")

    assert load_hostnames(source) == frozenset(
        {"a.hust.edu.vn", "b.hust.edu.vn"}
    )


def test_load_hostnames_rejects_urls(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("https://example.com/path\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hostname"):
        load_hostnames(source)


def test_config_exposes_only_active_two_phase_fields(tmp_path: Path) -> None:
    raw = complete_yaml(tmp_path, contact="ops@example.org")
    config = CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}))

    removed = {
        "trap_query_keys",
        "trap_exceptions",
        "accepted_mime_types",
        "accepted_mime_prefixes",
        "media_extensions",
    }
    assert removed.isdisjoint(config.__dataclass_fields__)


def test_config_enforces_safety_defaults(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")

    config = CrawlerConfig.load(
        Path("config/crawler.yaml"),
        domains,
        environ={"CRAWLER_CONTACT": "ops@example.org"},
    )

    assert config.concurrent_per_host == 2
    assert config.concurrent_requests == 64
    assert config.playwright_max_pages == 8
    assert config.download_delay_seconds == 0.25
    assert config.throttle_start_seconds == 0.25
    assert config.autothrottle_target_concurrency == 2.0
    assert config.max_response_bytes == 100 * 1024 * 1024


def test_config_loads_complete_runtime_policy(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = complete_yaml(
        tmp_path,
        contact="ops@example.org",
        retry_times=4,
        download_timeout_seconds=75,
    )

    config = CrawlerConfig.load(raw, domains)

    assert config.retry_times == 4
    assert config.retry_statuses == (408, 425, 429, 500, 502, 503, 504)
    assert config.download_timeout_seconds == 75


def test_environment_contact_overrides_yaml(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = complete_yaml(tmp_path, contact="yaml@example.org")

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


def test_config_load_rejects_placeholder_operator_contact(tmp_path: Path) -> None:
    raw = complete_yaml(tmp_path)

    with pytest.raises(ValueError, match="operator contact"):
        CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}), environ={})


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
    raw = complete_yaml(tmp_path, contact="ops@example.org", **{field: value})

    with pytest.raises(ValueError, match=field):
        CrawlerConfig.load(raw, domains)


@pytest.mark.parametrize("field", ["concurrent_requests", "concurrent_per_host", "playwright_max_pages"])
def test_config_rejects_non_positive_concurrency_fields(tmp_path: Path, field: str) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("example.com\n", encoding="utf-8")
    raw = tmp_path / "crawler.yaml"
    raw = complete_yaml(tmp_path, contact="ops@example.org", **{field: 0})

    with pytest.raises(ValueError, match=field):
        CrawlerConfig.load(raw, domains)


def test_config_snapshot_is_json_safe_and_overrides_concurrency(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("b.test\na.test\n", encoding="utf-8")
    raw = tmp_path / "crawler.yaml"
    raw = complete_yaml(tmp_path, contact="ops@example.org")
    snapshot = config_snapshot(CrawlerConfig.load(raw, domains), concurrency=24)

    assert snapshot["concurrent_requests"] == 24
    assert snapshot["hostnames"] == ["a.test", "b.test"]
    assert isinstance(snapshot["retry_statuses"], list)


def test_config_reports_all_missing_required_keys(tmp_path: Path) -> None:
    raw = tmp_path / "crawler.yaml"
    raw.write_text("contact: ops@example.org\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}))
    message = str(exc_info.value)
    assert "missing required crawler settings" in message
    assert "concurrent_requests" in message
    assert "progress_interval_seconds" in message


def test_runtime_snapshot_contains_yaml_performance_controls(tmp_path: Path) -> None:
    config = CrawlerConfig.load(
        complete_yaml(tmp_path, contact="ops@example.org"),
        hostnames=frozenset({"a.test"}),
    )
    tuning = RuntimeTuning("safe-fast", 64, 2, 80, 8, 500, 2.0, 10.0)
    snapshot = runtime_config_snapshot(config, tuning)
    assert snapshot["download_timeout_seconds"] == 60.0
    assert snapshot["retry_backoff_base_seconds"] == 2.0
    assert snapshot["progress_interval_seconds"] == 10.0


@pytest.mark.parametrize("field", ["robots_txt_obey", "cookies_enabled", "autothrottle_enabled"])
def test_config_rejects_non_boolean_policy(tmp_path: Path, field: str) -> None:
    raw = complete_yaml(tmp_path, contact="ops@example.org", **{field: "yes"})
    with pytest.raises(ValueError, match=field):
        CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}))


def test_config_rejects_invalid_cross_field_limits(tmp_path: Path) -> None:
    raw = complete_yaml(
        tmp_path,
        contact="ops@example.org",
        autothrottle_target_concurrency=3.0,
        concurrent_per_host=2,
    )
    with pytest.raises(ValueError, match="autothrottle_target_concurrency"):
        CrawlerConfig.load(raw, hostnames=frozenset({"a.test"}))
