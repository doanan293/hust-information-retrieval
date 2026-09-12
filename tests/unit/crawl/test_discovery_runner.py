from pathlib import Path

from hust_crawler.config import CrawlerConfig
from hust_crawler.crawl import discovery_runner
from hust_crawler.crawl.discovery_runner import run_discovery
from hust_crawler.crawl.discovery_spider import DiscoverySpider
from hust_crawler.crawl.seeds import SeedSet
from hust_crawler.crawl.state import CrawlState


def capture_state_open(monkeypatch) -> dict[str, object]:
    observed: dict[str, object] = {}
    original_open = CrawlState.open

    def fake_open(cls, root: Path, **kwargs) -> CrawlState:
        observed["root"] = root
        observed.update(kwargs)
        return original_open(root, **kwargs)

    monkeypatch.setattr(CrawlState, "open", classmethod(fake_open))
    return observed


def run_discovery_for_test(tmp_path: Path) -> int:
    seeds_file = tmp_path / "seeds.txt"
    seeds_file.write_text("https://a.test/explicit\n", encoding="utf-8")
    seeds = SeedSet(
        recursive_hostnames=frozenset(),
        exact_urls=("https://a.test/explicit",),
        invalid=(),
    )
    config = CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.com")
    from hust_crawler.crawl.profiles import resolve_runtime_tuning
    tuning = resolve_runtime_tuning(profile="safe-fast", concurrency=4, max_per_host=2, resource_limit_percent=80)
    output = tmp_path / "output"
    return run_discovery(
        input_path=seeds_file,
        output=output,
        config=config,
        seeds=seeds,
        tuning=tuning,
        resume=False,
    )


def test_discovery_runner_opens_discover_phase(monkeypatch, tmp_path: Path) -> None:
    class FakeCrawler:
        stats = None

    class FakeCrawlerProcess:
        def __init__(self, *args, **kwargs):
            pass

        def create_crawler(self, *args, **kwargs):
            return FakeCrawler()

        def crawl(self, crawler, **kwargs):
            spider = DiscoverySpider(**kwargs)
            list(spider.start_requests())

        def start(self):
            pass

    monkeypatch.setattr(discovery_runner, "CrawlerProcess", FakeCrawlerProcess)
    observed = capture_state_open(monkeypatch)
    exit_code = run_discovery_for_test(tmp_path)
    assert exit_code == 0
    assert observed["phase"] == "discover"
    assert (tmp_path / "output/urls.txt").exists()
    assert (tmp_path / "output/urls.txt").read_text(encoding="utf-8") == "https://a.test/explicit\n"
