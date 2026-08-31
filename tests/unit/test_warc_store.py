from datetime import datetime, timezone

from warcio.archiveiterator import ArchiveIterator

from hust_crawler.models import Capture
from hust_crawler.warc_store import WarcStore


def test_writes_readable_gzip_warc_response(tmp_path):
    store = WarcStore(tmp_path, "example.com", rotate_bytes=1_000_000)
    ref = store.write(
        Capture(
            url="https://example.com/",
            captured_at=datetime.now(timezone.utc),
            status=200,
            reason="OK",
            headers=(("Content-Type", "text/html"),),
            payload=b"<h1>Hello</h1>",
            mime="text/html",
        )
    )
    store.close()
    with (tmp_path / ref.filename).open("rb") as stream:
        record = next(ArchiveIterator(stream))
        assert record.rec_type == "response"
        assert record.rec_headers["WARC-Target-URI"] == "https://example.com/"
        assert record.content_stream().read() == b"<h1>Hello</h1>"


def test_resume_opens_a_new_warc_sequence(tmp_path):
    capture = Capture(
        url="https://example.com/",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status=200,
        reason="OK",
        headers=(("Content-Type", "text/html"),),
        payload=b"first",
        mime="text/html",
    )
    first = WarcStore(tmp_path, "example.com")
    first_ref = first.write(capture, run_id="run-1")
    first.close()

    second = WarcStore(tmp_path, "example.com")
    second_ref = second.write(capture, run_id="run-2")
    second.close()

    assert first_ref.filename == "example.com-00001.warc.gz"
    assert second_ref.filename == "example.com-00002.warc.gz"
