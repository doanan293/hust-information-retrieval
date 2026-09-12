from pathlib import Path
import pytest

from hust_crawler.crawl.options import CrawlOptions
from hust_crawler.crawl.state import CrawlState
from hust_crawler.crawl.writer import CrawlWriter, StorageLimit


def make_writer(
    tmp_path: Path,
    *,
    max_total_file_bytes: int = 1024,
    max_file_bytes: int = 1024,
) -> CrawlWriter:
    source = tmp_path / "seeds.txt"
    source.write_text("https://a.test/a\n", encoding="utf-8")
    state = CrawlState.open(
        tmp_path / "output" / "state",
        phase="crawl",
        input_path=source,
        semantic_config={},
        runtime_config={},
        resume=False,
    )
    options = CrawlOptions(
        max_total_file_bytes=max_total_file_bytes,
        max_file_bytes=max_file_bytes,
    )
    return CrawlWriter(tmp_path / "output", state, options)


def test_identical_files_share_one_blob(tmp_path: Path) -> None:
    writer = make_writer(tmp_path, max_total_file_bytes=1024)
    first = writer.write_file(
        "https://a.test/a.pdf", "https://a.test/a.pdf", "application/pdf", b"same"
    )
    second = writer.write_file(
        "https://a.test/b.pdf", "https://a.test/b.pdf", "application/pdf", b"same"
    )
    assert first["path"] == second["path"]
    assert len(list((tmp_path / "output/files").iterdir())) == 1
    assert writer.stored_bytes == 4
    writer.close()

def test_file_size_limit_rejection(tmp_path: Path) -> None:
    writer = make_writer(tmp_path, max_file_bytes=3)
    with pytest.raises(StorageLimit) as exc_info:
        writer.write_file(
            "https://a.test/large.pdf", "https://a.test/large.pdf", "application/pdf", b"four"
        )
    assert exc_info.value.reason == "file_size_limit"
    writer.close()


def test_storage_budget_rejection(tmp_path: Path) -> None:
    writer = make_writer(tmp_path, max_total_file_bytes=5)
    writer.write_file("https://a.test/1.pdf", "https://a.test/1.pdf", "application/pdf", b"abc")
    assert writer.stored_bytes == 3

    with pytest.raises(StorageLimit) as exc_info:
        writer.write_file("https://a.test/2.pdf", "https://a.test/2.pdf", "application/pdf", b"def")
    assert exc_info.value.reason == "storage_budget"
    assert writer.stored_bytes == 3
    # No partial file created for rejected blob
    assert len(list((tmp_path / "output/files").iterdir())) == 1
    writer.close()


def test_write_article_content_deduplication(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)
    a1 = writer.write_article({
        "url": "https://a.test/1",
        "title": "One",
        "text": "Same text content",
    })
    a2 = writer.write_article({
        "url": "https://a.test/2",
        "title": "Two",
        "text": "Same text content",
    })
    assert "duplicate_of" not in a1 or a1["duplicate_of"] is None
    assert a2["duplicate_of"] == "https://a.test/1"
    writer.close()


def test_write_article_completes_url(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)
    writer.write_article({
        "url": "https://a.test/article",
        "title": "Article",
        "text": "Body",
    })
    assert writer.state.is_complete("https://a.test/article")
    writer.close()



def test_file_record_preserves_article_asset_role(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)
    record = writer.write_file("https://a.test/photo.jpg", "https://a.test/photo.jpg", "image/jpeg", b"image", asset_role="inline_image")
    assert record["asset_role"] == "inline_image"
    assert record["path"].startswith("files/")
    writer.close()


@pytest.mark.parametrize(
    ("mime", "expected_suffix"),
    [
        ("application/vnd.oasis.opendocument.text", ".odt"),
        ("application/vnd.oasis.opendocument.spreadsheet", ".ods"),
        ("application/vnd.oasis.opendocument.presentation", ".odp"),
    ],
)
def test_extensionless_odf_files_resolve_extension(
    tmp_path: Path, mime: str, expected_suffix: str
) -> None:
    writer = make_writer(tmp_path)
    record = writer.write_file(
        "https://a.test/download",
        "https://a.test/download",
        mime,
        b"odf content",
    )
    assert record["path"].endswith(expected_suffix)
    writer.close()


def test_file_record_merges_referrers_and_reuses_body(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)
    first = writer.write_file(
        "https://cdn.test/a.png", "https://cdn.test/a.png", "image/png", b"same",
        asset_group="image", asset_role="inline_image", referring_page="https://a.test/one",
    )
    second = writer.write_file(
        "https://cdn.test/a.png", "https://cdn.test/a.png", "image/png", b"same",
        asset_group="image", asset_role="inline_image", referring_page="https://a.test/two",
    )
    assert first["sha256"] == second["sha256"]
    assert second["referring_pages"] == ["https://a.test/one", "https://a.test/two"]
    assert len(list((tmp_path / "output" / "files").iterdir())) == 1
    writer.close()


def test_content_only_export_has_empty_files_jsonl(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)
    writer.publish()
    files_jsonl = tmp_path / "output" / "files.jsonl"
    assert files_jsonl.exists()
    assert files_jsonl.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "output" / "files").exists()
    writer.close()
