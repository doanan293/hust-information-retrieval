from pathlib import Path
import yaml

from hust_crawler.crawl import discovery_cli
from hust_crawler.crawl.discovery_cli import build_parser, parse_args


def write_complete_config(tmp_path: Path) -> None:
    values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
    values["contact"] = "replace-with-operator-contact@example.invalid"
    (tmp_path / "crawler.yaml").write_text(yaml.safe_dump(values), encoding="utf-8")


def test_discovery_cli_defaults() -> None:
    args = parse_args(["--input", "seeds.txt", "--output", "data/discovery"])
    assert args.input == "seeds.txt"
    assert args.output == "data/discovery"
    assert args.resume is False


def test_discovery_cli_help() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "discovery" in help_text.lower()
    assert "sitemap" in help_text.lower()


def test_main_loads_contact_from_dotenv_in_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text("CRAWLER_CONTACT=ops@example.org\n", encoding="utf-8")
    (tmp_path / "seeds.txt").write_text("example.com\n", encoding="utf-8")
    write_complete_config(tmp_path)
    observed: dict[str, str] = {}

    def capture_run(**kwargs) -> int:
        observed["contact"] = kwargs["config"].contact
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CRAWLER_CONTACT", raising=False)
    monkeypatch.setattr(discovery_cli, "run_discovery", capture_run)

    exit_code = discovery_cli.main(
        [
            "--input",
            "seeds.txt",
            "--output",
            "discovery-output",
            "--config",
            "crawler.yaml",
        ]
    )

    assert exit_code == 0
    assert observed["contact"] == "ops@example.org"
