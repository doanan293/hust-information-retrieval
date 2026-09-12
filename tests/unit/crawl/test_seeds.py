from pathlib import Path
import pytest

from hust_crawler.crawl.seeds import parse_seed_file


def test_mixed_seeds_are_canonical_and_recursive_host_wins(tmp_path: Path) -> None:
    source = tmp_path / "seeds.txt"
    source.write_text(
        "# class sites\nLMS.HUST.EDU.VN.\n"
        "https://lms.hust.edu.vn/course/../course/view.php?id=2#top\n"
        "https://hust.edu.vn/news/one\nhttps://hust.edu.vn/news/one#copy\n",
        encoding="utf-8",
    )

    seeds = parse_seed_file(source)

    assert seeds.recursive_hostnames == frozenset({"lms.hust.edu.vn"})
    assert seeds.exact_urls == (
        "https://hust.edu.vn/news/one",
        "https://lms.hust.edu.vn/course/view.php?id=2",
    )
    assert seeds.mode_for("https://lms.hust.edu.vn/course/view.php?id=2") == "recursive"
    assert seeds.mode_for("https://hust.edu.vn/news/one") == "exact"


def test_invalid_lines_are_retained_but_one_valid_seed_is_enough(tmp_path: Path) -> None:
    source = tmp_path / "seeds.txt"
    source.write_text("not a host\nftp://files.test/a\na.test\n", encoding="utf-8")

    seeds = parse_seed_file(source)

    assert [(item.line, item.reason) for item in seeds.invalid] == [
        (1, "invalid_hostname"),
        (2, "unsupported_scheme"),
    ]
    assert seeds.recursive_hostnames == frozenset({"a.test"})


def test_empty_seed_file_raises_value_error(tmp_path: Path) -> None:
    source = tmp_path / "seeds.txt"
    source.write_text("# only comments\n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="input contains no valid seeds"):
        parse_seed_file(source)


def test_all_invalid_seeds_raises_value_error(tmp_path: Path) -> None:
    source = tmp_path / "seeds.txt"
    source.write_text("ftp://files.test/a\n", encoding="utf-8")

    with pytest.raises(ValueError, match="input contains no valid seeds"):
        parse_seed_file(source)
