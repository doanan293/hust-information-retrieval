# Information Retrieval Monorepo

Kho mã này gồm hai dự án độc lập:

| Thư mục | Dự án | Công cụ |
| --- | --- | --- |
| [`crawler/`](crawler/README.md) | Thu thập nội dung công khai từ các website HUST | Python 3.13, `uv` |
| [`lucene/`](lucene/README.md) | Lập chỉ mục và tìm kiếm tài liệu với Apache Lucene | JDK 17+, Maven 3.9+ |

Chạy lệnh trong thư mục của từng dự án để các đường dẫn tương đối, cấu hình và dữ liệu đầu ra hoạt động đúng.

## Crawler

```bash
cd crawler
uv sync --extra dev
uv run pytest -q
```

Xem [hướng dẫn crawler](crawler/README.md) để cấu hình và chạy crawl.

## Lucene

```bash
cd lucene
mvn test
mvn package
```

Xem [hướng dẫn Lucene](lucene/README.md) để cài VnCoreNLP và chạy tìm kiếm.
