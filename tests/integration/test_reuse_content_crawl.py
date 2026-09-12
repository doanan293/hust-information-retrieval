from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


def run_fixture(root: Path, mode: str, assets: str) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).parents[1] / "fixtures" / "run_unified_fixture.py"
    return subprocess.run(
        [sys.executable, str(script), str(root), mode, assets],
        capture_output=True,
        text=True,
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def requested_urls(root: Path) -> list[str]:
    return [
        line.strip()
        for line in (root / "requested_urls.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_reuse_downloads_assets_without_refetching_html(tmp_path: Path) -> None:
    lightweight = run_fixture(tmp_path, "complete", "content-only")
    assert lightweight.returncode == 0, lightweight.stderr
    first_requests = requested_urls(tmp_path)
    source_articles = read_jsonl(tmp_path / "crawl" / "articles.jsonl")

    upgraded = run_fixture(tmp_path, "reuse", "all")
    assert upgraded.returncode == 0, upgraded.stderr
    second_requests = requested_urls(tmp_path)[len(first_requests) :]

    assert second_requests
    content_urls = {str(article.get("final_url") or article["url"]) for article in source_articles}
    assert not content_urls.intersection(second_requests)
    assert not any("sitemap" in urlsplit(url).path for url in second_requests)
    assert read_jsonl(tmp_path / "crawl-all" / "articles.jsonl") == source_articles
    files = read_jsonl(tmp_path / "crawl-all" / "files.jsonl")
    assert {record["asset_group"] for record in files} == {
        "image",
        "media",
        "document",
    }


def test_reuse_rejects_private_dns_before_asset_host_robots_request(
    tmp_path: Path,
) -> None:
    lightweight = run_fixture(tmp_path, "complete", "content-only")
    assert lightweight.returncode == 0, lightweight.stderr
    first_request_count = len(requested_urls(tmp_path))

    upgraded = run_fixture(tmp_path, "reuse_private_dns", "all")
    assert upgraded.returncode != 0
    second_requests = requested_urls(tmp_path)[first_request_count:]

    assert not any(urlsplit(url).hostname == "cdn.test" for url in second_requests)
