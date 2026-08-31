from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from hust_crawler.archive_validation import validate_run

CHILD_SCRIPT = r'''
import sys
from pathlib import Path
from scrapy.utils.reactor import install_reactor
install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
from hust_crawler import cli
from hust_crawler.config import CrawlerConfig
original = cli._effective_settings
def fixture_settings(*args, **kwargs):
    settings = original(*args, **kwargs)
    settings.set("DOWNLOAD_HANDLERS", {"http": "tests.fixtures.static_download_handler.StaticDownloadHandler", "https": "tests.fixtures.static_download_handler.StaticDownloadHandler"})
    settings.set("DOWNLOAD_TIMEOUT", 2)
    return settings
cli._effective_settings = fixture_settings
config = CrawlerConfig(data_dir=Path(sys.argv[1]), hostnames=frozenset({"example.com"}), playwright_hosts=frozenset(), contact="ops@example.org", min_free_bytes=1, min_free_percent=0, playwright_auto_fallback=False)
raise SystemExit(cli._run_crawl(config, "crawl", "fixture-run", max_pages_per_host=3, max_requests_per_host=10, time_limit_seconds=30, pilot=True))
'''


def test_crawl_is_scoped_binary_safe_and_archivable(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, "-c", CHILD_SCRIPT, str(tmp_path)], cwd=os.getcwd(), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Traceback (most recent call last)" not in result.stderr
    run_root = tmp_path / "runs" / "fixture-run"
    fetches = [json.loads(line) for line in (run_root / "fetches.jsonl").read_text().splitlines()]
    assert {urlsplit(item["url"]).hostname for item in fetches if item.get("status") != "error"} == {"example.com"}
    rejections = [json.loads(line) for line in (run_root / "rejected-urls.jsonl").read_text().splitlines()]
    external = {item["discovered_url"] for item in rejections if item["reason"] == "host_out_of_scope"}
    assert "https://outside.test/leak" in external
    assert "https://outside.test/redirected" in external
    index = (tmp_path / "indexes" / "runs" / "fixture-run.cdxj").read_text()
    assert "https://example.com/image.png" in index
    assert "outside.test" not in index
    assert validate_run(tmp_path, "fixture-run")["status"] == "valid"
    stats = json.loads((run_root / "stats.json").read_text())
    assert stats["crawler_stats"]["request_budget/attempts"] == {"example.com": 6}
