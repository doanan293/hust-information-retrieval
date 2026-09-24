# HUST Public Web Crawler

Discover and extract public content from HUST hostnames and URLs using a unified single-pass hybrid crawl workflow.

## Installation

```bash
uv sync --extra dev
uv run playwright install chromium
printf 'CRAWLER_CONTACT=%s\n' "$(git config user.email)" > .env
```

`hust-crawl` (and the diagnostic `hust-discover` utility) automatically load `CRAWLER_CONTACT` from `.env` in the current
working directory. An environment variable already exported by the shell takes
precedence over the value in `.env`.

All crawler policy and performance settings live in `config/crawler.yaml`; the
file is validated strictly and must contain the complete schema. `.env` is only
for `CRAWLER_CONTACT`. Supported CLI tuning flags override YAML for that run:
`--assets`, frontier/file limits, `--concurrency`, `--max-per-host`, and
`--resource-limit-percent`. The checked-in defaults use two requests per host,
AutoThrottle target `2.0`, and a five-second maximum delay. A process already
running in tmux must be stopped and restarted before changed code or YAML is
loaded.

## Unified Hybrid Crawl Workflow

The primary crawl workflow is a single-pass hybrid crawler that combines sitemap exploration and recursive HTML link crawling. Two asset modes are supported:

```bash
# Default lightweight crawl (content-only: zero-binary transfer)
uv run hust-crawl --input docs/domain_active.txt --output data/crawl

# Safe semantic-asset mode (all: images, media, documents)
uv run hust-crawl --input docs/domain_active.txt --output data/crawl-all --assets all

# Upgrade a finished lightweight crawl without crawling HTML again
uv run hust-crawl --input docs/domain_active.txt --output data/crawl-all \
  --assets all --reuse-content-from data/crawl
```

- **`content-only` (default):** Keeps full HTML article content, structure, and rich asset references (`assets` metadata in `articles.jsonl`), but downloads and transfers no recognizable binaries. Ambiguous binary responses are terminated at HTTP headers (`Content-Type`), leaving `files.jsonl` empty and `files/` absent.
- **`all`:** Discovers and downloads supported semantic assets (images, audio/video media, documents) directly referenced by crawled pages, subject to public network safety checks (safe DNS resolution, private/loopback IP blocking) and storage quotas.
- **`--reuse-content-from`:** Copies the articles and crawl index from a finished, compatible `content-only` output, then requests only its recorded semantic assets. The source is read-only and the new output is standalone. Asset-host `robots.txt` requests may still occur, but HTML pages and sitemaps are not crawled again. The source must use the same `--input` file contents and must not still be running; the destination path must not already exist.
- **Resume compatibility:** Crawl state from older versions using `--assets documents` cannot be resumed and will be rejected. The current access-policy migration can continue an existing unified crawl in place with `--retry-policy-skips`.

### Input Seeds and Scope Semantics

The repository's default seed inventory is `docs/domain_active.txt`. Input files are UTF-8 text with one seed target per line. Empty lines and lines starting with `#` are ignored. A line may be:

- A **bare hostname** (e.g. `soict.hust.edu.vn`): treated as a recursive hostname seed. The crawler begins with sitemap and homepage startup (probing `robots.txt`, initial sitemap candidates `sitemap.xml`, `sitemap_index.xml`, `sitemap-index.xml`, and the root homepage `https://<hostname>/`). Discovered pages are crawled recursively, with automatic expansion of same-host HTML links from every recursive HTML page.
- An **absolute URL** (e.g. `https://hust.edu.vn/news/one`): treated as an **exact URL seed**. Exact-URL non-expansion: exact URL targets are fetched and extracted, but outbound HTML links from exact URLs are not expanded or followed. Exact binary seeds obey the active asset mode.

**Exact-Host Scope Boundary:**
Crawling is strictly restricted to exact parsed-host equality. Parent domains never grant access to subdomains (e.g. `lms.hust.edu.vn` does not include `www.lms.hust.edu.vn` or `hust.edu.vn`), and redirects to different hosts are recorded and rejected as out-of-scope. In `--assets all` mode, external asset URLs directly referenced by allowed pages are admitted under isolated asset scope and validated hop-by-hop.

### Crawl Policy and Safety Controls

- **Robots handling:** The crawl does not apply `Allow`/`Disallow` rules. It may still fetch `robots.txt` to discover `Sitemap` declarations, but a robots rule never prevents a same-host URL from being scheduled.
- **Browser-compatible access:** Requests use a browser-compatible User-Agent. JavaScript shells and browser-recoverable denials receive one Playwright attempt.
- **Public-only access:** Login gates and CAPTCHAs are terminal skips; the crawler never authenticates or attempts to solve an access challenge.
- **Operator contact requirements:** A valid operator contact from `CRAWLER_CONTACT` is still required and recorded in configuration. Placeholder contacts are rejected before a crawl starts.
- **Faithful Article Extraction:** Fetches and extracts readable HTML content, headings, structured content blocks, semantic HTML, and metadata.
- **Asset Handling:**
  - In `content-only` mode (default), article asset references are recorded with role and provenance, but no binary files are requested or transferred.
  - In `all` mode, supported images, media references, and documents are downloaded once per canonical URL with SHA-256 deduplication and referrer provenance tracking.
- **Expansion Gates:** Duplicate pages (detected via content hashing) and shell pages (soft-404 error pages, login forms, CAPTCHAs, empty shells) never expand outbound links.

### Frontier Policy and Finite Expansion

The hybrid crawler terminates on finite graph evidence rather than time-based timeouts:

- **Canonical URL Deduplication:** Each canonical URL is fetched at most once; duplicates return `record_only` and are not refetched.
- **Generator Link Rejection:** Calendar archive paths (`calendar_archive`), faceted search/filter queries (`search_filter`), and action/auth endpoints (`action_auth`) are classified and rejected before admission to the frontier.
- **Sequential Pagination Novelty Closure:** Sequential pagination advances one step at a time via `select_semantic_next`. Route families track content and target fingerprints across pages and close naturally when evidence indicates exhaustion. Normal closure reasons include:
  - `repeated_content_fingerprint`: The page body content fingerprint has been seen on a previous page of this route family.
  - `repeated_target_set`: The set of article targets matches a previous page in this route family.
  - `zero_novelty`: Consecutive pages have yielded zero new article targets (`--pagination-empty-pages`, default: `3`).
  - `pagination_closed`: Subsequent pagination candidates belonging to an already closed route family are rejected.

**Circuit Breakers (Truncation Controls):**
Hard caps act as safety circuit breakers rather than the primary stop mechanism. When reached, they truncate the crawl and record truncation reasons in `manifest.json`:
- `--max-query-variants-per-path`: Maximum query variants per path before `query_variant_limit` rejection (default: `20`).
- `--max-urls-per-host`: Maximum URLs per hostname before `host_url_limit` rejection (default: `100000`).
- `--max-total-urls`: Global scheduled URL limit before `total_url_limit` rejection (default: `1000000`).
- `--max-file-bytes`: Maximum single file size in bytes (default: 100 MiB / `104857600`).
- `--max-total-file-bytes`: Maximum total file storage in bytes before `storage_budget` rejection (default: 100 GiB / `107374182400`).
- `--profile`: Concurrency profile (default: `safe-fast`).
- `--concurrency`: Global concurrency limit (overrides YAML).
- `--max-per-host`: Per-host concurrency limit (overrides YAML).
- `--resource-limit-percent`: Adaptive CPU/RAM utilization threshold (overrides YAML).

#### Crawl Outputs (`data/crawl/`):
- `urls.txt`: Sorted list of successfully crawled URLs (one per line; statuses `extracted` and `file_saved`).
- `url_records.jsonl`: Authoritative disposition index for every target URL (`extracted`, `file_saved`, `discovered_not_downloaded`, `skipped`, `failed`).
- `articles.jsonl`: Faithful extracted readable HTML content with typed blocks, semantic markup, asset references, and content hash deduplication (`duplicate_of`).
- `files.jsonl`: Metadata, asset group, first role, referring pages, and relative paths for persisted files (populated in `all` mode; empty in `content-only`).
- `files/`: Content-addressed asset files (`<sha256><ext>`) stored in `all` mode.
- `errors.jsonl`: Final failures and rejections with URL and reason.
- `manifest.json`: Public crawl manifest published atomically, including status, timings, counts, resource metrics, `assets` breakdown, route family diagnostics, and skipped reasons (`"phase": "crawl"`).
- `state/`: Resumable crawl state SQLite database (`index.sqlite3`), lock, and Scrapy job state.
- `final/`: Automatically refreshed when a crawl run ends. Use `final/articles.jsonl`
  and `final/urls.txt` as the matching article dataset; `final/README.json` records
  the article count and source crawl time. Only articles whose current URL status
  is `extracted` are included. If a duplicate points to an excluded article,
  its text is included directly so `final/` can be used on its own. The
  top-level files remain the raw crawl output and resume data; crawl logs
  stay outside `final/`.
- `logs/`: Optional operator logs from prior runs. The crawler does not create
  log files in the output root unless shell output is redirected there.
- `archive/`: Manual snapshots kept for recovery; not used by `--resume`.

---

### Faithful Article Content and Public Output Schema

The crawler guarantees faithful article extraction where semantic layout and readable content are reproducible without refetching the original web page. Source CSS stylesheets and exact pixel appearance are not retained.

#### 1. `articles.jsonl` Schema

Each line is a complete JSON record representing an extracted HTML page:

```json
{
  "url": "https://hust.edu.vn/tuyen-sinh-2026",
  "final_url": "https://hust.edu.vn/tuyen-sinh-2026",
  "title": "Thông tin tuyển sinh năm 2026",
  "language": "vi",
  "canonical_url": "https://hust.edu.vn/tuyen-sinh-2026",
  "author": "Ban Tuyển sinh",
  "published_at": "2026-09-08T08:00:00+07:00",
  "modified_at": "2026-09-08T10:00:00+07:00",
  "summary": "Đề án tuyển sinh đại học chính quy năm 2026 của Đại học Bách khoa Hà Nội.",
  "content_html": "<h1>Thông tin tuyển sinh năm 2026</h1>\n<p>Đại học Bách khoa Hà Nội công bố đề án tuyển sinh.</p>\n<figure><img src=\"https://hust.edu.vn/uploads/campus.jpg\" alt=\"Khuôn viên Bách khoa\"><figcaption>Khuôn viên trường</figcaption></figure>",
  "headings": [
    {
      "level": 1,
      "text": "Thông tin tuyển sinh năm 2026",
      "heading_path": ["Thông tin tuyển sinh năm 2026"]
    }
  ],
  "content_blocks": [
    {
      "type": "heading",
      "level": 1,
      "text": "Thông tin tuyển sinh năm 2026",
      "heading_path": ["Thông tin tuyển sinh năm 2026"]
    },
    {
      "type": "paragraph",
      "text": "Đại học Bách khoa Hà Nội công bố đề án tuyển sinh.",
      "heading_path": ["Thông tin tuyển sinh năm 2026"]
    },
    {
      "type": "figure",
      "asset_url": "https://hust.edu.vn/uploads/campus.jpg",
      "alt": "Khuôn viên Bách khoa",
      "caption": "Khuôn viên trường",
      "heading_path": ["Thông tin tuyển sinh năm 2026"]
    }
  ],
  "text": "Thông tin tuyển sinh năm 2026\nĐại học Bách khoa Hà Nội công bố đề án tuyển sinh.\nKhuôn viên trường",
  "assets": [
    {
      "url": "https://hust.edu.vn/uploads/campus.jpg",
      "role": "inline_image",
      "alt": "Khuôn viên Bách khoa",
      "caption": "Khuôn viên trường",
      "external": false
    },
    {
      "url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
      "role": "document_attachment",
      "alt": "",
      "caption": "Tải về Đề án 2026 (PDF)",
      "external": false
    }
  ],
  "links": [
    {"url": "https://hust.edu.vn/gioi-thieu", "text": "Giới thiệu"}
  ],
  "warnings": [
    "external_asset:https://cdn.partner.test/ad.jpg"
  ],
  "status": 200,
  "page_role": "content",
  "fetched_at": "2026-09-08T08:05:00Z",
  "content_type": "text/html; charset=utf-8"
}
```

- **Metadata Precedence:** Title prefers the selected article container's visible `h1`, then `og:title`, then `<title>`. Ambiguous publication dates or multiple canonical tags emit descriptive warnings (`ambiguous_published_at`, `ambiguous_canonical_url`).
- **Sanitized Semantic HTML (`content_html`):** Strictly allowlists safe semantic elements (`article`, `section`, `div`, `h1`–`h6`, `p`, `br`, `strong`, `em`, `b`, `i`, `mark`, `sub`, `sup`, `a`, `ul`, `ol`, `li`, `blockquote`, `pre`, `code`, `table`, `thead`, `tbody`, `tfoot`, `tr`, `th`, `td`, `figure`, `figcaption`, `picture`, `source`, `img`, `hr`). Drops unsafe tags (`script`, `style`, `form`, `iframe`, `svg`, `dialog`, etc.), removes event handlers (`on*`), inline `style`, `class`, `id`, and `data-*` attributes. Resolves relative URLs to absolute.
- **Ordered Blocks (`content_blocks`):** Preserves document reading order. Every block includes its contextual `heading_path` hierarchy. Tables retain matrix representations (`headers`, `rows`).
- **Synchronized Searchable Text (`text`):** Generated directly from the ordered blocks; guarantees that plain text, block structure, and rendered HTML never silently disagree.
- **Content Deduplication:** Duplicate articles (identical text hash) omit `content_html` and record `"duplicate_of": "<first_seen_url>"`.

#### 2. `files.jsonl` Schema

Maps every downloaded document attachment to its physical disk location:

```json
{
  "url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "final_url": "https://hust.edu.vn/uploads/de-an-2026.pdf",
  "path": "files/a1b2c3d4e5f6...pdf",
  "content_type": "application/pdf",
  "sha256": "a1b2c3d4e5f6...",
  "size": 245120,
  "asset_role": "document_attachment"
}
```

Files are content-addressed by SHA-256 physical deduplication. If multiple URLs point to the same binary payload, only one file is saved to disk while each URL receives an entry in `files.jsonl`.

#### 3. `manifest.json` Asset Metrics

The manifest records disk usage grouped by asset class:

```json
{
  "phase": "crawl",
  "assets": {
    "image": {"file_count": 142, "unique_bytes": 15420000},
    "document": {"file_count": 12, "unique_bytes": 48291000},
    "media": {"file_count": 3, "unique_bytes": 12400000}
  },
  "skipped": {
    "file_size_limit": 2,
    "storage_budget": 0
  }
}
```

- In `content-only` mode (default), no asset files are downloaded (`files.jsonl` is empty, and asset counts/bytes are zero).
- In `all` mode, supported images, media, and documents are downloaded and reported per group.

---

### Interruption and Resume

The crawler supports clean interruption and resume:

- **Graceful interrupt (Ctrl-C):** Scrapy closes active requests cleanly, saves the request queue to disk, marks the run `interrupted`, and exits with code 130.
- **Resume:** Run the same command with `--resume`. The crawler rebuilds pending requests from the SQLite index, skipping already-completed URLs without duplicate fetches even if Scrapy job state was interrupted abruptly.
- **Retry transient failures:** Add `--retry-failed` to a resumed run to retry final network and timeout failures while leaving HTTP errors and policy rejections unchanged: `uv run hust-crawl --input docs/domain_active.txt --output data/crawl --resume --retry-failed`.
- **Recover an older policy run:** Use `--retry-policy-skips` with `--resume` to migrate the known legacy User-Agent/robots policy and requeue only recoverable policy skips, login classifications, CAPTCHA false positives, transport failures, and zero-content host bootstraps. Existing `extracted` and `file_saved` URLs are preserved and not fetched again:

  ```bash
  uv run hust-crawl --input docs/domain_active.txt --output data/crawl \
    --resume --retry-policy-skips
  ```

- **Retry recoverable truncations:** Add `--retry-truncated` after a crawler policy fix to reclassify and requeue `query_variant_limit` records that are now recognized as content or semantic pagination: `uv run hust-crawl --input docs/domain_active.txt --output data/crawl --resume --retry-truncated`. Other query filters and budget truncations remain skipped.
- **State isolation:** `--resume` must point to the same crawl output and input inventory; do not resume a discovery output, a different inventory, or an unrelated fixed-inventory run.

### Completion / Exit Codes

- `0`: Completed cleanly under configured policy without URL failures.
- `3`: Completed under policy with one or more final URL failures.
- `4`: Stopped by a file-size or storage budget limit (truncated).
- `130`: Interrupted with resumable work remaining.
- `1`: Internal crawler failure.
- `2`: Invalid arguments, configuration, or input.

The crawl manifest also contains `host_completion` and `metrics.zero_content_hosts`, so every recursive seed can be checked for extracted coverage without scanning the full URL record file. A host remains `zero_content` when its public content is unavailable under the access policy, is a true login/CAPTCHA gate, redirects outside exact-host scope, or still has a terminal network failure.

---

### Optional Diagnostic Sitemap Discovery (`hust-discover`)

The independent `hust-discover` command is an optional diagnostic utility that performs sitemap-only URL discovery without HTML link following or content extraction:

```bash
uv run hust-discover --input docs/domain_active.txt --output data/discovery
```

Features and limitations of `hust-discover`:
1. Probes `robots.txt` and initial sitemap candidates (`sitemap.xml`, `sitemap_index.xml`, `sitemap-index.xml`).
2. Recursively expands sitemap indexes, supporting nested, extensionless, and gzipped (`.xml.gz`) sitemaps.
3. Canonical sitemap targets stream into `urls.txt` without URL-count limits.
4. Note: This applies only to `hust-discover`—it performs sitemap-only inspection and has **no HTML link fallback** (hosts without sitemaps or unreachable paths are not crawled for HTML links by `hust-discover`).

Each host's effective discovery mode is published in `manifest.json` under `discovery.hosts`:
- `sitemap`: One or more usable sitemaps succeeded with zero sitemap document failures. All declared targets are written to `urls.txt`.
- `sitemap_partial`: Reachable sitemap targets coexist with one or more unreachable or broken sitemap documents. Discovery proceeds for all discovered targets without falling back to HTML link expansion.
- `no_sitemap`: `robots.txt` and all sitemap probes complete without any usable sitemap targets. Zero URLs are discovered for this host.
- `exact`: Host represented solely by explicit URL targets; outbound links are data only.

#### Discovery Outputs (`data/discovery/`):
- `urls.txt`: Sorted canonical list of all discovered URLs (one per line).
- `url_records.jsonl`: Detailed record of all probe requests and exact target records.
- `manifest.json`: Discovery run manifest with status, counts, and host mode breakdown (`"phase": "discover"`).
- `state/`: Resumable discovery state SQLite database (`index.sqlite3`), lock, and Scrapy job state.

---

### Optional Domain Audit Utility

The independent `hust-domain-audit` command can be used to probe DNS/HTTP reachability:

```bash
uv run hust-domain-audit
```

### Development & Verification

Run all test suites, linters, and help commands locally:

```bash
uv run pytest -q
uv run ruff check .
uv run hust-crawl --help
uv run hust-discover --help
uv run hust-domain-audit --help
```
