import tomllib
from pathlib import Path

from hust_crawler.config import CrawlerConfig


ROOT = Path(__file__).parents[3]
CRAWL_DIR = ROOT / "src/hust_crawler/crawl"


def read_crawl_package_sources() -> str:
    chunks: list[str] = []
    for path in sorted(CRAWL_DIR.rglob("*.py")):
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_legacy_phase_tree_is_absent() -> None:
    assert not (ROOT / "src/hust_crawler/phases").exists()


def test_crawl_package_has_no_link_fallback_implementation() -> None:
    source = read_crawl_package_sources()
    assert "link_fallback" not in source


def test_runner_imports_unified_spider_and_frontier_policy() -> None:
    runner_source = (CRAWL_DIR / "runner.py").read_text(encoding="utf-8")
    assert "UnifiedSpider" in runner_source
    assert "FrontierPolicy" in runner_source
    assert "ContentSpider" not in runner_source


def test_retired_crawler_surfaces_are_absent() -> None:
    absent_paths = [
        "src/hust_crawler/crawl/content_spider.py",
        "src/hust_crawler/crawl/targets.py",
        "tests/fixtures/run_two_phase_fixture.py",
        "tests/integration/test_two_phase_crawl.py",
        "tests/unit/crawl/test_targets.py",
    ]
    assert [path for path in absent_paths if (ROOT / path).exists()] == []

    retired_config = {
        "trap_query_keys",
        "trap_exceptions",
        "accepted_mime_types",
        "accepted_mime_prefixes",
        "media_extensions",
    }
    assert retired_config.isdisjoint(CrawlerConfig.__dataclass_fields__)


def test_fixed_inventory_production_path_is_removed() -> None:
    assert not (ROOT / "src/hust_crawler/crawl/targets.py").exists()
    assert not (ROOT / "src/hust_crawler/crawl/content_spider.py").exists()


def test_optional_discovery_command_remains_registered() -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["hust-crawl"] == "hust_crawler.crawl.cli:main"
    assert scripts["hust-discover"] == "hust_crawler.crawl.discovery_cli:main"
