from pathlib import Path

from hust_crawler.crawl.discovery_writer import DiscoveryWriter
from .test_state import open_state


def test_discovery_export_contains_targets_but_not_probe_urls(tmp_path: Path) -> None:
    state = open_state(tmp_path / "state", phase="discover")
    writer = DiscoveryWriter(tmp_path / "public", state)
    writer.record_probe({"url": "https://a.test/robots.txt", "status": "complete"})
    writer.record_target({"url": "https://a.test/article", "discovered_from": "https://a.test/sitemap.xml"})
    writer.publish()
    assert (tmp_path / "public/urls.txt").read_text(encoding="utf-8") == "https://a.test/article\n"
    assert not (tmp_path / "public/articles.jsonl").exists()
    assert not (tmp_path / "public/files.jsonl").exists()
    writer.close()
