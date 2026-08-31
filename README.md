# HUST Public Crawler

Filesystem-only crawler for the public hostnames in `docs/domain_active.txt`. It follows only
exact allowlisted hostnames, archives supported responses as gzip WARC, and writes per-run CDXJ
indexes and coverage reports.

## Local quick start

Python 3.13, `uv`, Chromium/Playwright dependencies, and sufficient free disk space are
required. Network commands also require a real operator email address or URL.

```bash
uv sync --extra dev
uv run playwright install chromium
uv run hust-crawl validate-config
export CRAWLER_CONTACT="$(git config user.email)"
test -n "$CRAWLER_CONTACT"
```

Run a preflight and bounded pilot before the first full crawl:

```bash
uv run hust-crawl preflight preflight-001
uv run hust-crawl pilot pilot-001 \
  --max-pages-per-host 20 \
  --max-requests-per-host 10 \
  --time-limit-seconds 14400
uv run hust-crawl validate-archive pilot-001
```

Start a full crawl only after reviewing the pilot artifacts:

```bash
run_id="full-$(date -u +%Y%m%dT%H%M%SZ)"
uv run hust-crawl crawl "$run_id"
```

A full crawl has no page limit, request budget, or time limit. It also loads previously archived
URLs from the latest CDXJ index. Run it under a service manager or `tmux`, monitor disk usage,
and keep `data/` backed up off-host. The default storage watermark stops archive writes below
50 GiB free or 10% free space.

Resume an interrupted or failed run with its original configuration snapshot:

```bash
uv run hust-crawl resume RUN_ID
uv run hust-crawl validate-archive RUN_ID
```

## Crawl policy

The crawler reads `robots.txt` for sitemap hints but does not use it as an access-control list.
It uses one concurrent request per host with adaptive throttling, never authenticates or submits
forms, tries HTTPS first, and falls back to HTTP only for eligible root connection/TLS failures.
JavaScript shell pages may be rendered by Playwright. CAPTCHA and login/access gates are recorded
as coverage gaps and are never solved or bypassed.

HTML, XML, documents, images, and video are archived. CSS, JavaScript, fonts, and browser
resources are transient rendering inputs only. Every retry, redirect, HTTPS fallback, and browser
rerender is subject to the configured traffic controls.

Pilot runs omit historical CDXJ seeds and start from each root, its `robots.txt`, and the primary
`sitemap.xml`. Every network attempt consumes the per-host request budget. A time-limited pilot
normally exits 130 with a valid partial archive.

## Run status and lifecycle

Manifest status and process exit codes are explicit:

- `preflight_complete` or `complete`: exit 0.
- `complete_with_gaps`: exit 3; the archive is valid, but real coverage gaps remain.
- `failed`: exit 1; an internal error or invalid archive occurred.
- `interrupted`: exit 130; queued/in-flight work is reported as `pending`.
- Invalid arguments or configuration: exit 2.

Lifecycle phases distinguish crawler bookkeeping from real failures:

- `discovered` is informational and does not by itself create a coverage gap.
- `scheduled` means the request reached the downloader; unfinished scheduled work becomes
  `pending` when a run is interrupted.
- `deduplicated`, `redirected`, `fallback`, and `budget_rejected` are expected terminal outcomes.
- `archived` is a successful stored response; `failed`, CAPTCHA, access gates, and unexpected
  rejections remain coverage gaps.

Inspect `data/runs/<run-id>/manifest.json` and `stats.json` for status, per-host coverage,
request budgets, gap reasons, retries, and archive validation. A `finished` Scrapy reason alone
does not imply complete coverage.

## Artifacts

- WARC archives: `data/archives/YYYY/MM/DD/`.
- Per-run manifests, stats, fetches, errors, rejections, and lifecycle: `data/runs/<run-id>/`.
- Per-run and latest CDXJ indexes: `data/indexes/`.
- Persistent Scrapy resume state: `data/state/<run-id>/`.

## Docker

```bash
export CRAWLER_CONTACT='ops@example.org'
export CRAWLER_UID="$(id -u)" CRAWLER_GID="$(id -g)"
docker compose build crawler
docker compose run --rm crawler validate-config
docker compose run --rm crawler preflight preflight-001
docker compose run --rm crawler pilot pilot-001 \
  --max-pages-per-host 20 \
  --max-requests-per-host 10 \
  --time-limit-seconds 14400
docker compose run --rm crawler validate-archive pilot-001
docker compose run --rm crawler crawl full-001
```

Enable the weekly Sunday schedule only after reviewing the preflight and pilot output. The
weekly job intentionally propagates `complete_with_gaps` (exit 3) so monitoring can alert.
Because cron does not inherit the interactive shell environment, put a real contact in the
project `.env` before installing it:

```bash
printf 'CRAWLER_CONTACT=%s\n' "$(git config user.email)" > .env
scripts/install-cron.sh
```
