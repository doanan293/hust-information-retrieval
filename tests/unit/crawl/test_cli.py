from pathlib import Path
import pytest
import yaml

from hust_crawler.crawl import cli
from hust_crawler.crawl.cli import build_parser, parse_args


def write_complete_config(tmp_path: Path, contact: str = "ops@example.org") -> None:
    values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
    values["contact"] = contact
    (tmp_path / "crawler.yaml").write_text(yaml.safe_dump(values), encoding="utf-8")


def test_crawl_cli_accepts_mixed_seeds_and_frontier_limits() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl",
        "--assets", "all",
        "--max-query-variants-per-path", "12",
        "--pagination-empty-pages", "4",
        "--max-urls-per-host", "50000",
        "--max-total-urls", "300000",
    ])
    assert args.assets == "all"
    assert args.max_query_variants_per_path == 12
    assert args.pagination_empty_pages == 4
    assert args.max_urls_per_host == 50_000
    assert args.max_total_urls == 300_000


def test_crawl_cli_defaults() -> None:
    args = parse_args(["--input", "seeds.txt", "--output", "data/crawl"])
    assert args.assets is None
    assert args.max_query_variants_per_path is None
    assert args.pagination_empty_pages is None
    assert args.max_urls_per_host is None
    assert args.max_total_urls is None
    assert args.max_file_bytes is None
    assert args.max_total_file_bytes is None
    assert args.reuse_content_from is None
    assert args.retry_truncated is False
    assert args.retry_access_gates is False
    assert args.retry_policy_skips is False


def test_crawl_cli_accepts_retry_policy_skips_with_resume() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl",
        "--resume",
        "--retry-policy-skips",
    ])
    assert args.retry_policy_skips is True


def test_main_rejects_retry_policy_skips_without_resume(tmp_path: Path, capsys) -> None:
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")

    exit_code = cli.main([
        "--input", str(seeds),
        "--output", str(tmp_path / "crawl"),
        "--retry-policy-skips",
    ])

    assert exit_code == 2
    assert "--retry-policy-skips requires --resume" in capsys.readouterr().err


def test_main_passes_retry_policy_skips_to_runner(tmp_path: Path, monkeypatch) -> None:
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", str(seeds),
        "--output", str(tmp_path / "crawl"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--resume",
        "--retry-policy-skips",
    ]) == 0
    assert observed["retry_policy_skips"] is True


def test_crawl_cli_accepts_lightweight_content_source() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl-all",
        "--assets", "all",
        "--reuse-content-from", "data/crawl",
    ])
    assert args.reuse_content_from == Path("data/crawl")


def test_crawl_cli_accepts_retry_failed_with_resume() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl",
        "--resume",
        "--retry-failed",
    ])
    assert args.resume is True
    assert args.retry_failed is True


def test_main_rejects_retry_failed_without_resume(tmp_path: Path, capsys) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    exit_code = cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--retry-failed",
    ])
    assert exit_code == 2
    assert "--retry-failed requires --resume" in capsys.readouterr().err


def test_main_passes_retry_failed_to_runner(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--resume",
        "--retry-failed",
    ]) == 0
    assert observed["retry_failed"] is True


def test_crawl_cli_accepts_retry_truncated_with_resume() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl",
        "--resume",
        "--retry-truncated",
    ])
    assert args.resume is True
    assert args.retry_truncated is True


def test_main_rejects_retry_truncated_without_resume(tmp_path: Path, capsys) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")

    exit_code = cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--retry-truncated",
    ])

    assert exit_code == 2
    assert "--retry-truncated requires --resume" in capsys.readouterr().err


def test_main_passes_retry_truncated_to_runner(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--resume",
        "--retry-truncated",
    ]) == 0
    assert observed["retry_truncated"] is True


def test_crawl_cli_accepts_retry_access_gates_with_resume() -> None:
    args = parse_args([
        "--input", "seeds.txt",
        "--output", "data/crawl",
        "--resume",
        "--retry-access-gates",
    ])

    assert args.resume is True
    assert args.retry_access_gates is True


def test_main_rejects_retry_access_gates_without_resume(tmp_path: Path, capsys) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")

    exit_code = cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--retry-access-gates",
    ])

    assert exit_code == 2
    assert "--retry-access-gates requires --resume" in capsys.readouterr().err


def test_main_passes_retry_access_gates_to_runner(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--resume",
        "--retry-access-gates",
    ]) == 0
    assert observed["retry_access_gates"] is True


def test_crawl_cli_asset_choices() -> None:
    assert parse_args(["--input", "seeds.txt", "--output", "data/crawl"]).assets is None
    assert parse_args(["--input", "seeds.txt", "--output", "data/crawl", "--assets", "all"]).assets == "all"
    with pytest.raises(SystemExit):
        parse_args(["--input", "seeds.txt", "--output", "data/crawl", "--assets", "documents"])


def test_main_passes_seed_set_to_unified_runner(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "seeds.txt").write_text(
        "a.test\nhttps://b.test/exact\n", encoding="utf-8"
    )
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", "seeds.txt",
        "--output", "crawl-output",
        "--config", "crawler.yaml",
    ]) == 0
    assert observed["seeds"].recursive_hostnames == frozenset({"a.test"})
    assert observed["seeds"].exact_urls == ("https://b.test/exact",)
    assert observed["reuse_content_from"] is None


def test_main_passes_lightweight_content_source_to_runner(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_crawl", capture_run)
    assert cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl-all"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--assets", "all",
        "--reuse-content-from", str(tmp_path / "crawl"),
    ]) == 0
    assert observed["reuse_content_from"] == tmp_path / "crawl"


def test_main_rejects_reuse_source_without_all_assets(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)
    exit_code = cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl-all"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--reuse-content-from", str(tmp_path / "crawl"),
    ])
    assert exit_code == 2
    assert "requires --assets all" in capsys.readouterr().err


def test_main_reports_reuse_validation_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "seeds.txt").write_text("a.test\n", encoding="utf-8")
    write_complete_config(tmp_path)

    def reject_run(**kwargs: object) -> int:
        raise ValueError("source crawl is still running")

    monkeypatch.setattr(cli, "run_crawl", reject_run)
    exit_code = cli.main([
        "--input", str(tmp_path / "seeds.txt"),
        "--output", str(tmp_path / "crawl-all"),
        "--config", str(tmp_path / "crawler.yaml"),
        "--assets", "all",
        "--reuse-content-from", str(tmp_path / "crawl"),
    ])
    assert exit_code == 2
    assert "source crawl is still running" in capsys.readouterr().err


def test_help_describes_document_storage_and_manifest() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "file" in help_text.lower()
    assert "resume" in help_text.lower()


def test_main_loads_contact_from_dotenv_in_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text("CRAWLER_CONTACT=ops@example.org\n", encoding="utf-8")
    (tmp_path / "urls.txt").write_text("https://example.com/a\n", encoding="utf-8")
    write_complete_config(tmp_path, "replace-with-operator-contact@example.invalid")
    observed: dict[str, str] = {}

    def capture_run(**kwargs) -> int:
        observed["contact"] = kwargs["config"].contact
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CRAWLER_CONTACT", raising=False)
    monkeypatch.setattr(cli, "run_crawl", capture_run)

    exit_code = cli.main(
        [
            "--input",
            "urls.txt",
            "--output",
            "crawl-output",
            "--config",
            "crawler.yaml",
        ]
    )

    assert exit_code == 0
    assert observed["contact"] == "ops@example.org"
