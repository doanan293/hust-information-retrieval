# Lucene search — bài tập Information Retrieval

Chương trình đơn giản minh hoạ **Apache Lucene**:

- **Đầu vào**: một thư mục văn bản (`.txt`, `.md`, `.csv`, `.log`, `.pdf`) + một file truy vấn.
- **Đầu ra**:
  - chỉ mục Lucene trong `index/`
  - mô tả chỉ mục dạng đọc được trong `output/index-dump.txt` (tự sinh)
  - kết quả tìm kiếm: in ra console + `output/results.txt`

## Yêu cầu

- JDK 17+ (bản này pin Lucene 9.12.3; Lucene 10 cần JDK 21)
- Maven 3.9+
- Kết nối mạng cho lần cài VnCoreNLP đầu tiên (analyzer tiếng Việt là **mặc định** — xem [mục dưới](#analyzer-tiếng-việt-tách-từ-vncorenlp--mặc-định))
- Chạy được trên **Linux / macOS / Windows** — xem [Chạy trên Windows](#chạy-trên-windows)

## Chạy

```bash
bash scripts/setup-vncorenlp.sh    # 1 lần: tải bộ tách từ tiếng Việt (~28 MB)
mvn -q package                     # build ra target/lucene-search.jar
```

> Chưa chạy `setup-vncorenlp.sh` thì chương trình vẫn chạy được: nó cảnh báo rồi tự lùi về analyzer `standard` (tách theo âm tiết).

### Chạy trên Windows

Bản thân chương trình Java chạy bình thường trên Windows. Chỉ khác vài chỗ:

- **Cài VnCoreNLP** dùng script PowerShell (không cần Git Bash):
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\setup-vncorenlp.ps1
  mvn -q package
  java -jar target\lucene-search.jar --mode batch --queries data\queries-vi.txt
  ```
  (Có Git Bash / WSL thì chạy `bash scripts/setup-vncorenlp.sh` cũng được.)
- **Hiển thị tiếng Việt trên console**: chương trình tự ép `System.out` sang UTF-8 nên chữ có dấu hiện đúng. Nếu vẫn thấy `?`, chạy `chcp 65001` trước, hoặc dùng **Windows Terminal** / terminal của VS Code.
- **Tô màu từ khoá**: chỉ bật khi terminal chắc chắn hỗ trợ ANSI (Windows Terminal, VS Code, ConEmu/Cmder). `cmd.exe` cổ điển → không tô màu, thay vào đó bọc `«...»` như trong file. Đặt biến môi trường `NO_COLOR=1` để tắt hẳn màu.
- File `output/results.txt` luôn là UTF-8 và luôn dùng `«...»`, không phụ thuộc terminal.

Chương trình có **3 kiểu chạy** qua tham số `--mode`:

### Kiểu 1 — interactive: nhập từng truy vấn ở console (mặc định)

```bash
java -jar target/lucene-search.jar                       # mode interactive là mặc định
java -jar target/lucene-search.jar --skip-index          # dùng lại chỉ mục có sẵn, không lập lại
```

Sau khi lập chỉ mục, chương trình hiện dấu nhắc `truy van>`. Gõ một truy vấn, Enter, xem kết quả ngay; lặp lại. Thoát bằng `:q` hoặc `Ctrl+D`. Cả phiên vẫn được ghi lại vào `output/results.txt`.

### Kiểu 2 — batch: đọc toàn bộ truy vấn từ file

```bash
java -jar target/lucene-search.jar --mode batch                          # đọc data/queries.txt
java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt   # bộ truy vấn tiếng Việt
```

Đọc lần lượt mọi dòng trong file `--queries`, in kết quả từng truy vấn và ghi tất cả vào `output/results.txt`.

### Kiểu 3 — segment: xem kết quả tách từ (không cần chỉ mục)

```bash
java -jar target/lucene-search.jar --mode segment
```

Chế độ debug/kiểm tra riêng cho bước **tách từ tiếng Việt** (VnCoreNLP), tách biệt hẳn khỏi việc lập chỉ mục và tìm kiếm — không đọc `--docs`, không lập `index/`, không ghi `output/results.txt`. Hữu ích khi muốn biết VnCoreNLP tách một câu thành những từ nào *trước khi* các bước sau (hạ chữ thường, bỏ từ dừng, bỏ dấu) xử lý tiếp.

Chương trình hiện dấu nhắc `cau>`. Gõ một câu, Enter, xem ngay danh sách từ đã tách (nối âm tiết bằng `_`); lặp lại. Thoát bằng `:q` hoặc `Ctrl+D`.

```
cau> công nghệ thông tin
  So tu: 1
   1. công_nghệ_thông_tin
  => công_nghệ_thông_tin

cau> trí tuệ nhân tạo
  So tu: 1
   1. trí_tuệ_nhân_tạo
  => trí_tuệ_nhân_tạo
```

Chỉ dùng được với `--analyzer vietnamese` (mặc định) — báo lỗi và không tách nếu chọn `standard`/`english`. Cần đã cài VnCoreNLP (`bash scripts/setup-vncorenlp.sh`), nếu không phần "Analyzer tiếng Việt" phía dưới sẽ tự lùi về `standard` và mode `segment` không hoạt động.

### Các tham số chung

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--mode` | `interactive` | `interactive` \| `batch` \| `segment` |
| `--docs` | `data/docs` | thư mục văn bản đầu vào |
| `--queries` | `data/queries.txt` | file truy vấn (chỉ dùng ở mode batch) |
| `--index` | `index` | thư mục chứa chỉ mục Lucene |
| `--out` | `output/results.txt` | file ghi kết quả tìm kiếm |
| `--index-dump` | `output/index-dump.txt` | file mô tả chỉ mục (tự sinh sau khi lập chỉ mục) |
| `--topk` | `10` | số kết quả tối đa mỗi truy vấn |
| `--analyzer` | `vietnamese` | `vietnamese` (tách từ VnCoreNLP) \| `standard` \| `english` (stemming) |
| `--vn-home` | `.` | thư mục chứa `lib/VnCoreNLP-1.2.jar` và `models/` (cho `--analyzer vietnamese`) |
| `--skip-index` | (tắt) | bỏ qua lập chỉ mục nếu `index/` đã có sẵn |

## Đoạn trích chứa từ khoá (KWIC)

Mỗi kết quả in kèm một đoạn văn bản **cắt quanh vị trí từ khoá** (dùng `lucene-highlighter`). Từ khớp được làm nổi bật:

- **Console** (khi chạy trong terminal thật): tô **nền vàng, chữ đen** (mã ANSI).
- **File `output/results.txt`** (và console khi bị chuyển hướng/pipe): bọc bằng `«...»`.

```
  1. score=0.5645  id=ranking-bm25.txt
     file: data/docs/ranking-bm25.txt
     … Lucene, use «BM25» as the default ranking function. «BM25» is a probabilistic model … Modern search engines use «BM25» as the default …
```

Với `--analyzer english`, từ khoá được so khớp *sau khi stemming*, nên truy vấn `searches` vẫn làm nổi bật `searching` / `searched` / `search` trong văn bản.

## Analyzer tiếng Việt (tách từ VnCoreNLP) — mặc định

`--analyzer vietnamese` dùng bộ tách từ **VnCoreNLP** (RDRsegmenter). Token trong chỉ mục là **từ**, các âm tiết được nối bằng `_`:

```
"trí tuệ nhân tạo"  →  trí_tuệ_nhân_tạo        (1 token)
"công nghệ thông tin" → công_nghệ_thông_tin
"đồng bằng sông Cửu Long" → đồng_bằng · sông · cửu_long
```

Nhờ đó truy vấn `"trí tuệ nhân tạo"` chỉ khớp tài liệu thật sự nói về khái niệm đó, không khớp nhầm tài liệu tình cờ chứa `trí`, `tuệ`… rời rạc.

### Cài đặt (một lần)

```bash
bash scripts/setup-vncorenlp.sh                                        # Linux / macOS / Git Bash
powershell -ExecutionPolicy Bypass -File scripts\setup-vncorenlp.ps1   # Windows
```

Script tải 3 file (đều nằm trong `.gitignore`):

| File | Đích | Cỡ |
|---|---|---|
| `https://raw.githubusercontent.com/vncorenlp/VnCoreNLP/master/VnCoreNLP-1.2.jar` | `lib/VnCoreNLP-1.2.jar` | ~27 MB |
| `.../master/models/wordsegmenter/wordsegmenter.rdr` | `models/wordsegmenter/wordsegmenter.rdr` | ~128 KB |
| `.../master/models/wordsegmenter/vi-vocab` | `models/wordsegmenter/vi-vocab` | ~515 KB |

Tải tay cũng được — cứ đặt đúng 3 file vào đúng vị trí trên (thư mục gốc dự án).

### Cách hoạt động (không phải dependency lúc build)

- `VnSegmenter` nạp `lib/VnCoreNLP-1.2.jar` lúc chạy bằng `URLClassLoader` + reflection, chỉ bật annotator `wseg`. Vì vậy `mvn package` **không cần** VnCoreNLP; project vẫn build/chạy khi chưa cài.
- Chưa cài mà chọn `vietnamese` (kể cả do mặc định) → in cảnh báo và **tự lùi về `standard`**, chương trình vẫn chạy:
  ```
  !! Chua dung duoc analyzer 'vietnamese' (tach tu VnCoreNLP):
     thieu file cua VnCoreNLP ...
     -> Tam thoi dung 'standard' (tach theo am tiet).
  ```
- Kết quả tách từ được cache trong `VnSegmenter` (Highlighter re-analyze mỗi tài liệu nhiều lần).

### Chuỗi xử lý của `VietnameseAnalyzer`

```
VnWordTokenizer     tách TỪ bằng VnCoreNLP        → "trí_tuệ_nhân_tạo", "xử_lý"
LowerCaseFilter     chữ thường
StopFilter          bỏ từ chức năng + dấu câu     (và, của, là, ",", "." …)
ASCIIFoldingFilter  bỏ dấu                        "tiếng" → "tieng"
CompoundSplitFilter từ ghép → giữ nguyên + phát từng âm tiết LIÊN TIẾP đúng thứ tự
                    "tri_tue_nhan_tao"  +  tri · tue · nhan · tao (vị trí kề nhau)
```

Các filter này xử lý những ca thường gặp (đã kiểm bằng ~25 truy vấn):

| Ca | Không có filter | Có |
|---|---|---|
| Gõ **không dấu**: `tieng viet`, `hoc may` | 0 kết quả (`tieng` ≠ `tiếng`) | khớp bình thường |
| **Ngữ cảnh tách khác nhau**: `xử lý ngôn ngữ` vs tài liệu `…ngôn ngữ tự nhiên` | 0 (query `ngôn_ngữ`, index `ngôn_ngữ_tự_nhiên`) | khớp — âm tiết `ngon ngu` nằm kề nhau trong cả hai |
| **`đại học`** (âm tiết `học` cực phổ biến) | nhiễu: khớp cả "sinh học", "khoa học" | chỉ khớp nơi "đại" **ngay trước** "học" |
| Truy vấn toàn **từ dừng**: `của`, `và`, `một` | trả gần hết tài liệu | báo "không còn từ khoá" |
| **Dấu câu lọt vào**: `học máy, trí tuệ nhân tạo` | `,` thành term → khớp mọi tài liệu | dấu câu bị bỏ |

**Đánh đổi còn lại** (ghi rõ để không nhầm là lỗi):

- Bỏ dấu gộp thanh điệu: `cá` = `cà` = `cả` → `ca`; `phở` cũng khớp "phổ". Ưu tiên recall.
- `từ` **không** phải từ dừng (vì cụm "tách từ" cần) → gõ mỗi `từ` vẫn ra nhiều kết quả.
- Truy vấn nhiều khái niệm rời (`Apache Lucene tiếng Việt`) cho kết quả yếu — dùng `AND` để nối.

### Truy vấn — không cần ngoặc kép

Câu truy vấn **"trơn"** (chỉ chữ, không có `AND`/`OR`/`NOT`/`" "`/`( )`/`:`/`*`…) được chương trình **tự tách từ rồi dựng truy vấn** thay vì đưa thẳng vào `QueryParser`. Vì `CompoundSplitFilter` đưa các âm tiết vào chỉ mục **liên tiếp nhau đúng thứ tự**, truy vấn dựng ra là:

```
(content:"<âm tiết 1> <âm tiết 2> …"~2)^2   ← các âm tiết phải NẰM GẦN NHAU, đúng thứ tự (clause chính)
(content:<từ ghép>)^0.4 …                    ← từ ghép như một token — phụ, giúp xếp hạng
```

- `content:"dai hoc"~2` là **PhraseQuery**: 2 token `dai`, `hoc` cách nhau ≤ 2 vị trí, đúng thứ tự.
- `content:dai_hoc` là **TermQuery**: đúng 1 token `dai_hoc` (chỉ có nếu VnCoreNLP ghép "đại học" thành 1 từ lúc lập chỉ mục).
- Số `^` là **boost** — nhân điểm relevance của clause đó (không đổi tài liệu nào khớp, chỉ đổi thứ hạng).

| Gõ | Truy vấn dựng ra | Kết quả |
|---|---|---|
| `học máy` | `(content:"hoc may"~2)^2` | 2 tài liệu có "học máy" |
| `đại học` | `(content:"dai hoc"~2)^2 (content:dai_hoc)^0.4` | 0 — tập mẫu không có "đại học" (đúng, không kéo rác) |
| `xử lý ngôn ngữ` | `(content:"xu ly ngon ngu"~2)^2 (content:xu_ly)^0.4 (content:ngon_ngu)^0.4` | nlp #1, hoc-may #2 |

Nhờ so khớp **theo cụm âm tiết** (không phải OR từng âm tiết), `đại học` chỉ khớp nơi "đại" đứng ngay trước "học" — **không** dính "sinh học" / "khoa học".

**Truy vấn nhiều khái niệm rời** (vd `Apache Lucene tiếng Việt`) không phải một cụm liền → clause chính không khớp, kết quả yếu. Lúc đó dùng toán tử: `Lucene AND "tiếng Việt"`.

Khi câu có toán tử thì chương trình **không** can thiệp — tự đặt ngoặc kép quanh từng cụm:

```
phở OR "bánh mì"
"học máy" AND "trí tuệ nhân tạo"
```

Bộ truy vấn mẫu: `data/queries-vi.txt`.

```bash
java -jar target/lucene-search.jar --mode batch --queries data/queries-vi.txt
java -jar target/lucene-search.jar                    # interactive, gõ: học máy
```

Chạy nhanh khi phát triển, không cần đóng gói jar:

```bash
mvn -q compile exec:java                                 # interactive
mvn -q compile exec:java -Dexec.args="--mode batch"      # batch
```

## Định dạng file truy vấn (`data/queries.txt`)

- Mỗi dòng một truy vấn. Dòng trống hoặc bắt đầu bằng `#` bị bỏ qua.
- Dùng được cú pháp `QueryParser` của Lucene: `AND`, `OR`, `NOT`, `"cụm từ"`, `tiền tố*`, `filename:tên`.
- Muốn tự đặt mã truy vấn: `<id><TAB><nội dung>` (ví dụ `Q99<TAB>elasticsearch OR solr`).

## Xem nội dung chỉ mục

Thư mục `index/` là file nhị phân (`.cfs`), phải xem qua công cụ.

### File mô tả tự sinh — `output/index-dump.txt`

**Mỗi lần lập chỉ mục, chương trình tự ghi** một file văn bản mô tả toàn bộ chỉ mục:

- **Tổng quan**: số tài liệu, số segment, dung lượng.
- **Bảng Field**: mỗi field được đánh chỉ mục kiểu gì (`DOCS` / `DOCS_AND_FREQS_AND_POSITIONS` / `NONE`), có `stored` / `norms` không.
- **Từ điển term = chỉ mục ngược**: với mỗi field, mỗi term → **danh sách tài liệu chứa nó** (kèm `(số lần)` khi > 1), cùng `df` (số tài liệu) và `tf` (tổng số lần).
- **Stored fields** của từng tài liệu (`content` cắt 200 ký tự).

```
== Field ==
  id       | index=DOCS                          | stored=co | norms=-  | ...
  path     | index=NONE                          | stored=co | norms=-  | ...   ← chỉ lưu, không đánh chỉ mục
  content  | index=DOCS_AND_FREQS_AND_POSITIONS  | stored=co | norms=co | ...

== Tu dien term (chi muc nguoc)  |  term -> [tai lieu chua no] ==
  [content]  so term = 615,  tong token = 1308
      trí_tuệ_nhân_tạo   df=2  tf=2  -> [hoc-may.txt, nlp-tieng-viet.txt]
      đồng_bằng          df=2  tf=4  -> [bien-doi-khi-hau.txt, dia-ly-viet-nam.txt(3)]
      lucene             df=2  tf=7  -> [apache-lucene-vi.txt(4), lucene.txt(3)]
```

Muốn xem thêm **vị trí** từng lần xuất hiện thì dùng `IndexDump --postings` (bên dưới).

### Tiện ích `IndexDump` (chạy tay, để soi kỹ hơn)

```bash
java -cp target/lucene-search.jar com.example.ir.IndexDump index --field content --limit 0   # tất cả term của content
java -cp target/lucene-search.jar com.example.ir.IndexDump index --postings content:lucene   # term này ở tài liệu nào
java -cp target/lucene-search.jar com.example.ir.IndexDump index --docs                       # stored fields từng tài liệu
java -cp target/lucene-search.jar com.example.ir.IndexDump index --report my-dump.txt         # ghi mô tả ra file khác
```

`--postings content:lucene` cho thấy trực tiếp cơ chế chỉ mục ngược:

```
== Postings cho content:lucene ==
  doc #2  [apache-lucene-vi.txt]  freq=4  vi_tri=[15, 35, 65, 133]
  doc #7  [lucene.txt]            freq=3  vi_tri=[30, 64, 86]
```

### Luke (GUI chính thức)

Tải bản `lucene-9.12.3` từ trang Apache, chạy `luke.sh` / `luke.bat`, mở thư mục `index/` — duyệt term, xem document, chạy thử truy vấn, xem giải thích điểm BM25.

## Luồng xử lý & các lớp

| Bước | Lớp | Việc làm |
|---|---|---|
| Rút văn bản | `TextExtractor` | đọc text thẳng; PDF qua Apache PDFBox |
| Nạp tài liệu | `DocLoader` | quét thư mục đệ quy → `List<Doc>` (id = đường dẫn tương đối) |
| Tạo chỉ mục | `Indexer` | `IndexWriter` + analyzer; field `content` (phân tích **và lưu** để highlighter cắt được đoạn trích), `id`/`path`/`filename` |
| Nạp truy vấn | `QueryLoader` | đọc `queries.txt` (mode batch) |
| Tìm kiếm | `Searcher` | `runBatch` (từ file) / `runInteractive` (REPL ở console); dùng chung `QueryParser` → `IndexSearcher.search` → top-K BM25; `Highlighter` cắt đoạn trích, `teeSink` tô nền ANSI (console) / bọc `«...»` (file) |
| Tách từ tiếng Việt | `VnSegmenter` + `VietnameseAnalyzer` + `CompoundSplitFilter` | nạp động VnCoreNLP từ `lib/`; token là **từ** (`trí_tuệ_nhân_tạo`) + âm tiết cùng vị trí; kèm bỏ dấu + lọc từ dừng |
| Xem chỉ mục | `IndexDump` | tiện ích riêng: in field, từ điển term, postings (`java -cp ... com.example.ir.IndexDump`) |

## Ghi chú

- Analyzer khi lập chỉ mục và khi phân tích truy vấn phải giống nhau — ở đây dùng chung một instance.
- `--analyzer english` bật stemming (searching/searched/searches → cùng gốc), chỉ hợp cho tiếng Anh.
- `--analyzer standard` xử lý tiếng Việt ở mức **âm tiết**; `vietnamese` (mặc định) tách theo **từ**.
- Mỗi lần chạy, chỉ mục được tạo lại từ đầu (`OpenMode.CREATE`); dùng `--skip-index` để giữ lại.
- `content` được lưu bản gốc (`Store.YES`) để `Highlighter` cắt đoạn trích. Nếu tập văn bản rất lớn và không cần snippet, đổi lại `Store.NO` trong `Indexer`.
- VnCoreNLP dùng log4j 1.x nên khi khởi tạo có thể in dòng `INFO ... Loading Word Segmentation model` — bình thường.
