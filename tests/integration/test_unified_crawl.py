from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import parse_qsl, urlsplit


def run_fixture(
    tmp_path: Path,
    mode: str = "complete",
    *,
    assets: str = "content-only",
) -> subprocess.CompletedProcess[str]:
    fixture_script = Path(__file__).parents[1] / "fixtures" / "run_unified_fixture.py"
    return subprocess.run(
        [sys.executable, str(fixture_script), str(tmp_path), mode, assets],
        capture_output=True,
        text=True,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def asset_requests(tmp_path: Path) -> list[str]:
    suffixes = (".png", ".jpg", ".svg", ".mp3", ".mp4", ".pdf", ".docx", ".xlsx", ".pptx")
    return [url for url in requested_urls(tmp_path) if urlsplit(url).path.lower().endswith(suffixes)]


def request_counts(tmp_path: Path) -> Counter[str]:
    return Counter(requested_urls(tmp_path))


def skipped_reason(tmp_path: Path, url: str) -> str | None:
    return records_by_url(tmp_path / "crawl" / "url_records.jsonl").get(url, {}).get("reason")


def requested_pagination_pages(tmp_path: Path) -> list[int]:
    pages: list[int] = []
    for url in requested_urls(tmp_path):
        values = dict(parse_qsl(urlsplit(url).query)).get("page")
        if values is not None:
            pages.append(int(values))
    return pages


def extracted_article_ids(tmp_path: Path) -> set[int]:
    return {
        int(urlsplit(record["url"]).path.rsplit("/", 1)[-1])
        for record in read_jsonl(tmp_path / "crawl" / "articles.jsonl")
        if "/article/" in urlsplit(record["url"]).path
    }


def requested_urls(tmp_path: Path) -> list[str]:
    log_path = tmp_path / "requested_urls.txt"
    if not log_path.exists():
        return []
    return [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def records_by_url(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            records[row["url"]] = row
    return records


def completed_urls(tmp_path: Path, first: list[str]) -> list[str]:
    records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    first_set = set(first)
    return [
        url
        for url, rec in records.items()
        if rec.get("status") == "extracted"
        and url in first_set
        and not url.endswith(("/robots.txt", "sitemap.xml", "sitemap_index.xml", "sitemap-index.xml"))
    ]


def test_one_pass_crawls_sitemap_html_only_and_off_sitemap_pages(tmp_path: Path) -> None:
    result = run_fixture(tmp_path)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    articles = records_by_url(tmp_path / "crawl/articles.jsonl")
    assert "https://map.test/from-sitemap" in articles
    assert "https://map.test/outside-sitemap" in articles
    assert "https://html-only.test/article" in articles
    assert "https://a.test/article" in articles
    assert "https://b.test/explicit" in articles

    assert read_jsonl(tmp_path / "crawl" / "files.jsonl") == []
    assert asset_requests(tmp_path) == []

    req_urls = requested_urls(tmp_path)
    assert not any("sub.a.test" in url for url in req_urls)

    manifest = json.loads((tmp_path / "crawl/manifest.json").read_text(encoding="utf-8"))
    hosts = manifest["discovery"]["hosts"]
    assert hosts["map.test"]["mode"] in {"sitemap", "sitemap_partial"}
    assert hosts["html-only.test"]["mode"] == "no_sitemap"
    assert hosts["b.test"]["mode"] == "exact"


def test_default_records_assets_but_transfers_no_asset_body(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, assets="content-only")
    assert result.returncode == 0
    articles = read_jsonl(tmp_path / "crawl" / "articles.jsonl")
    assert any(article["assets"] for article in articles)
    assert read_jsonl(tmp_path / "crawl" / "files.jsonl") == []
    assert asset_requests(tmp_path) == []


def test_all_downloads_supported_assets_once_without_external_html(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, assets="all")
    assert result.returncode == 0
    files = read_jsonl(tmp_path / "crawl" / "files.jsonl")
    assert {record["asset_group"] for record in files} == {"image", "media", "document"}
    assert request_counts(tmp_path)["https://cdn.test/assets/a.png"] == 1
    assert "https://cdn.test/frame.html" not in requested_urls(tmp_path)
    assert skipped_reason(tmp_path, "https://cdn.test/private-redirect") == "non_public_address"


def test_generated_graph_closes_before_circuit_breakers(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="generated_graph")
    assert result.returncode == 0
    manifest = read_json(tmp_path / "crawl" / "manifest.json")
    assert manifest["status"] == "complete"
    assert manifest["truncation_reasons"] == []
    assert requested_pagination_pages(tmp_path) == [1, 2, 3]
    assert extracted_article_ids(tmp_path) == {123, 124, 125}
    assert manifest["frontier"]["route_families"]["closure_reasons"]["repeated_target_set"] >= 1


def test_cycles_fetch_each_canonical_url_once(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="cycle")
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    requested = requested_urls(tmp_path)
    assert requested.count("https://cycle.test/cycle/a") == 1
    assert requested.count("https://cycle.test/cycle/b") == 1


def test_interrupted_run_resumes_without_refetching_completed_urls(tmp_path: Path) -> None:
    first_res = run_fixture(tmp_path, mode="interrupt")
    assert first_res.returncode == 130, f"STDOUT:\n{first_res.stdout}\nSTDERR:\n{first_res.stderr}"
    first = requested_urls(tmp_path)

    second_res = run_fixture(tmp_path, mode="resume")
    assert second_res.returncode == 0, f"STDOUT:\n{second_res.stdout}\nSTDERR:\n{second_res.stderr}"
    all_requests = requested_urls(tmp_path)
    completed_before_interrupt = completed_urls(tmp_path, first)
    assert len(completed_before_interrupt) > 0
    assert all(all_requests.count(url) == 1 for url in completed_before_interrupt)


def test_crawl_ignores_url_disallowed_by_robots(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="robots_disallow")
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert "https://a.test/about" in requested_urls(tmp_path)
    records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    assert records["https://a.test/about"]["status"] == "extracted"


def test_crawl_ignores_redirected_robots_disallow_rules(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="robots_redirect_disallow")
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert "https://a.test/robots-public.txt" in requested_urls(tmp_path)
    assert "https://a.test/about" in requested_urls(tmp_path)
    records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    assert records["https://a.test/about"]["status"] == "extracted"


def test_policy_recovery_resumes_without_refetching_successful_urls(tmp_path: Path) -> None:
    initial = run_fixture(tmp_path, mode="policy_recovery_initial")
    assert initial.returncode == 0, f"STDOUT:\n{initial.stdout}\nSTDERR:\n{initial.stderr}"
    before = request_counts(tmp_path)
    first_records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    assert first_records["https://a.test/about"]["reason"] == "robots_disallowed"
    assert first_records["https://ua-gated.test/"]["reason"] == "login_required"
    assert first_records["https://js-gated.test/"]["status"] == "extracted"

    resumed = run_fixture(tmp_path, mode="policy_recovery_resume")
    assert resumed.returncode == 0, f"STDOUT:\n{resumed.stdout}\nSTDERR:\n{resumed.stderr}"
    after = request_counts(tmp_path)
    records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    manifest = read_json(tmp_path / "crawl/manifest.json")

    assert after["https://a.test/article"] == before["https://a.test/article"]
    assert records["https://a.test/about"]["status"] == "extracted"
    assert records["https://ua-gated.test/"]["status"] == "extracted"
    assert records["https://js-gated.test/"]["status"] == "extracted"
    assert manifest["host_completion"]["ua-gated.test"]["status"] == "complete"


def test_captcha_and_login_access_gates_are_skipped(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="access_gate")
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    records = records_by_url(tmp_path / "crawl/url_records.jsonl")
    assert records["https://a.test/captcha-page"]["status"] == "skipped"
    assert records["https://a.test/captcha-page"]["reason"] == "captcha_blocked"
    assert records["https://a.test/login-page"]["status"] == "skipped"
    assert records["https://a.test/login-page"]["reason"] == "login_required"


def test_polite_retry_recovers_transient_failures(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, mode="retry")
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    req_urls = requested_urls(tmp_path)
    assert req_urls.count("https://a.test/retry-page") == 2
    articles = records_by_url(tmp_path / "crawl/articles.jsonl")
    assert "https://a.test/retry-page" in articles
