# Resumable Complete Active-Domain Crawl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continue the existing crawl state, without refetching successful URLs, and recover public same-host content previously missed because of robots enforcement, legacy access classification, crawler User-Agent blocking, or missing browser rendering.

**Architecture:** Introduce a versioned access policy with a browser-compatible User-Agent and robots enforcement disabled, then add an explicit `--retry-policy-skips` resume migration that requeues only eligible terminal records. Preserve the exact-host frontier and all successful state, and publish per-seed completion diagnostics so zero-content hosts are visible.

**Tech Stack:** Python 3.12, Scrapy, scrapy-playwright, SQLite, PyYAML, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-09-12-resumable-complete-active-domain-crawl-design.md`

## Global Constraints

- Existing `extracted` and `file_saved` URLs must never be requeued by policy recovery.
- Preserve articles, files, URL records, errors, route-family state, and discovery state except for explicitly requeued terminal records and their stale errors.
- Restrict page crawling and redirect handling to exact hostname equality with a recursive seed.
- Do not authenticate, submit login forms, solve CAPTCHAs, or expand across hostnames.
- Keep current URL, query, file-size, storage, concurrency, and resource limits.
- `--retry-policy-skips` must be explicit and must require `--resume`.
- The existing `data/crawl` version-4 state must migrate in place; no fresh crawl output is allowed for the production continuation.

---

## File Structure

- Modify `config/crawler.yaml`: select access policy revision 2, browser-compatible User-Agent, and disabled robots enforcement.
- Modify `src/hust_crawler/config.py`: validate and snapshot the new access-policy fields.
- Modify `src/hust_crawler/crawl/settings.py`: apply one User-Agent consistently and disable Scrapy robots enforcement while retaining explicit sitemap discovery.
- Modify `src/hust_crawler/policies/access.py`: distinguish HTTP denial, login gates, CAPTCHA gates, and browser-recoverable shells.
- Modify `src/hust_crawler/crawl/middlewares.py`: render recoverable responses once and keep true gates terminal.
- Create `src/hust_crawler/crawl/recovery.py`: own semantic-policy migration validation and eligible recovery reasons.
- Modify `src/hust_crawler/crawl/state.py`: transactionally requeue policy skips/bootstrap requests and calculate per-host completion.
- Modify `src/hust_crawler/crawl/cli.py`: expose and validate `--retry-policy-skips`.
- Modify `src/hust_crawler/crawl/runner.py`: orchestrate migration, recovery, manifest reporting, and zero-content exit behavior.
- Modify `tests/fixtures/scrapy_download_handler.py` and `tests/fixtures/run_unified_fixture.py`: model User-Agent rejection, robots skips, rendering recovery, and resume request counts.
- Modify focused unit/integration tests under `tests/unit/` and `tests/integration/`.
- Modify `README.md`: document access behavior, resume recovery, and result interpretation.

---

### Task 1: Versioned Browser-Compatible Access Configuration

**Files:**
- Modify: `config/crawler.yaml`
- Modify: `src/hust_crawler/config.py`
- Modify: `src/hust_crawler/crawl/settings.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/crawl/test_settings.py`

**Interfaces:**
- Produces: `CrawlerConfig.access_policy_revision: int`
- Produces: `CrawlerConfig.browser_user_agent: str`
- Produces: `semantic_config_snapshot(config)` containing both fields
- Consumes: existing `CrawlerConfig.robots_txt_obey`

- [ ] **Step 1: Write failing configuration tests**

Add tests asserting the checked-in YAML loads `access_policy_revision == 2`, a non-empty Chrome-compatible `browser_user_agent`, and `robots_txt_obey is False`. Update the complete-schema test so omission of either new field raises `missing required crawler settings`.

```python
def test_access_policy_v2_uses_browser_transport_without_robots() -> None:
    cfg = CrawlerConfig.load(
        Path("config/crawler.yaml"),
        environ={"CRAWLER_CONTACT": "ops@example.org"},
        hostnames=frozenset({"a.test"}),
    )
    assert cfg.access_policy_revision == 2
    assert "Mozilla/5.0" in cfg.browser_user_agent
    assert "Chrome/" in cfg.browser_user_agent
    assert cfg.robots_txt_obey is False
```

- [ ] **Step 2: Write failing settings tests**

Replace the legacy crawler-identification expectation with exact assertions that crawl and Playwright-bound requests use `config.browser_user_agent`, and that both crawl and discovery set `ROBOTSTXT_OBEY` to `False`.

```python
def test_crawl_uses_browser_user_agent_and_ignores_robots(tmp_path: Path) -> None:
    cfg = config()
    values = build_settings(cfg, state_dir=tmp_path / "job", phase="crawl")
    assert values["USER_AGENT"] == cfg.browser_user_agent
    assert values["ROBOTSTXT_OBEY"] is False
```

- [ ] **Step 3: Run the focused tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/test_config.py tests/unit/crawl/test_settings.py -q
```

Expected: failures for missing fields, legacy User-Agent, and robots still enabled.

- [ ] **Step 4: Implement the minimal configuration change**

Add these dataclass fields and include them in strict YAML validation:

```python
access_policy_revision: int = 2
browser_user_agent: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
```

Set the same values in `config/crawler.yaml`, change the dataclass default to `robots_txt_obey: bool = False`, and set the checked-in YAML value to `false`. Keep `build_settings()` driven by configuration so tests can construct legacy revision-1 state:

```python
"ROBOTSTXT_OBEY": config.robots_txt_obey if phase == "crawl" else False,
"USER_AGENT": config.browser_user_agent,
"ROBOTSTXT_USER_AGENT": config.browser_user_agent,
```

Keep `contact` validated and recorded in configuration snapshots; do not append it to the HTTP User-Agent.

- [ ] **Step 5: Run tests and lint**

Run:

```bash
uv run pytest tests/unit/test_config.py tests/unit/crawl/test_settings.py -q
uv run ruff check src/hust_crawler/config.py src/hust_crawler/crawl/settings.py tests/unit/test_config.py tests/unit/crawl/test_settings.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add config/crawler.yaml src/hust_crawler/config.py src/hust_crawler/crawl/settings.py tests/unit/test_config.py tests/unit/crawl/test_settings.py
git commit -m "feat: add browser-compatible crawl access policy"
```

---

### Task 2: Accurate Access Classification and One-Time Browser Fallback

**Files:**
- Modify: `src/hust_crawler/policies/access.py`
- Modify: `src/hust_crawler/crawl/middlewares.py`
- Test: `tests/unit/policies/test_access.py`
- Test: `tests/unit/crawl/test_middlewares.py`

**Interfaces:**
- Produces: `classify_access(*, status: int, url: str, html: str, rendered: bool) -> AccessDecision`
- Produces outcomes: `public`, `access_denied`, `login_required`, `captcha_blocked`
- Consumes: `AccessDecision.use_playwright` in `PlaywrightFallbackMiddleware.process_response()`

- [ ] **Step 1: Write failing classifier tests**

Replace the expectation that bare 401/403 means login with explicit denial behavior. Preserve terminal detection of real password forms and CAPTCHA markup.

```python
@pytest.mark.parametrize("status", [401, 403])
def test_http_denial_is_not_mislabeled_as_login(status: int) -> None:
    decision = classify_access(
        status=status,
        url="https://example.com/",
        html="<h1>Forbidden</h1>",
        rendered=False,
    )
    assert decision.outcome == "access_denied"
    assert decision.reason == f"http_{status}"
    assert decision.use_playwright is True


def test_password_form_remains_a_login_gate() -> None:
    decision = classify_access(
        status=200,
        url="https://example.com/login",
        html='<form action="/login"><input type="password"></form>',
        rendered=False,
    )
    assert decision.outcome == "login_required"
    assert decision.use_playwright is False
```

- [ ] **Step 2: Write failing middleware tests for a single render attempt**

Assert a non-rendered browser-recoverable shell or HTTP denial becomes a Playwright request, while a rendered 403, login page, or CAPTCHA raises `IgnoreRequest` without another retry.

```python
def test_unrendered_http_denial_gets_one_browser_attempt() -> None:
    request = Request("https://a.test/")
    response = HtmlResponse(
        request.url,
        status=403,
        headers={b"Content-Type": b"text/html"},
        body=b"<h1>Forbidden</h1>",
        request=request,
        encoding="utf-8",
    )
    rerender = PlaywrightFallbackMiddleware().process_response(request, response)
    assert rerender.meta["playwright"] is True
    assert rerender.meta["rendered"] is True
```

```python
def test_rendered_http_denial_is_terminal() -> None:
    request = Request(
        "https://a.test/",
        meta={"playwright": True, "rendered": True},
    )
    response = HtmlResponse(
        request.url,
        status=403,
        headers={b"Content-Type": b"text/html"},
        body=b"<h1>Forbidden</h1>",
        request=request,
        encoding="utf-8",
    )
    with pytest.raises(IgnoreRequest, match="access_denied:http_403"):
        PlaywrightFallbackMiddleware().process_response(request, response)
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/policies/test_access.py tests/unit/crawl/test_middlewares.py -q
```

Expected: denial-classification assertions fail against the legacy `login_required` mapping.

- [ ] **Step 4: Implement the minimal policy change**

In `classify_access()`, parse page-level login/CAPTCHA evidence before returning terminal outcomes, then map status 401/403 without login evidence to one browser attempt:

```python
return AccessDecision("access_denied", not rendered, f"http_{status}")
```

Keep the existing public shell logic and `rendered` guard. In the middleware, honor `use_playwright` before raising a non-public outcome, then preserve `access_denied` in the terminal `IgnoreRequest` message. Do not add retries for a rendered terminal gate.

- [ ] **Step 5: Run tests and lint**

Run:

```bash
uv run pytest tests/unit/policies/test_access.py tests/unit/crawl/test_middlewares.py -q
uv run ruff check src/hust_crawler/policies/access.py src/hust_crawler/crawl/middlewares.py tests/unit/policies/test_access.py tests/unit/crawl/test_middlewares.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add src/hust_crawler/policies/access.py src/hust_crawler/crawl/middlewares.py tests/unit/policies/test_access.py tests/unit/crawl/test_middlewares.py
git commit -m "fix: distinguish access denial from login gates"
```

---

### Task 3: Compatible Version-4 Policy Migration

**Files:**
- Create: `src/hust_crawler/crawl/recovery.py`
- Modify: `src/hust_crawler/crawl/state.py`
- Test: `tests/unit/crawl/test_state.py`
- Create: `tests/unit/crawl/test_recovery.py`

**Interfaces:**
- Produces: `ACCESS_POLICY_SEMANTIC_KEYS: frozenset[str]`
- Produces: `validate_policy_migration(saved: Mapping[str, object], effective: Mapping[str, object]) -> None`
- Extends: `CrawlState.open(..., allow_policy_migration: bool = False) -> CrawlState`
- Produces: `CrawlState.record_policy_migration(*, from_revision: int, to_revision: int) -> None`

- [ ] **Step 1: Write failing migration-validation tests**

Cover a legacy snapshot with `user_agent_name=HUSTPublicCrawler`, `user_agent_version=1.0`, `robots_txt_obey=True`, and no browser User-Agent. Assert it is accepted only when the effective snapshot changes to revision 2, browser User-Agent, and `robots_txt_obey=False`. Assert changes to `assets`, hostnames, crawl strategy, limits, or scope remain rejected.

```python
def test_policy_migration_allows_only_access_policy_delta() -> None:
    saved = {
        "crawl_strategy": "hybrid-unified",
        "assets": "content-only",
        "robots_txt_obey": True,
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
    }
    effective = {
        "crawl_strategy": "hybrid-unified",
        "assets": "content-only",
        "robots_txt_obey": False,
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
        "access_policy_revision": 2,
        "browser_user_agent": "Mozilla/5.0 Chrome/140.0.0.0",
    }
    validate_policy_migration(saved, effective)

    incompatible = {**effective, "assets": "all"}
    with pytest.raises(ValueError, match="assets"):
        validate_policy_migration(saved, incompatible)
```

- [ ] **Step 2: Write failing state-open tests**

Assert normal resume still rejects the policy delta, recovery resume accepts it, and a repeated recovery open is idempotent.

```python
with pytest.raises(ValueError, match="semantic configuration"):
    CrawlState.open(state_dir, phase="crawl", input_path=seeds, semantic_config=v2, runtime_config={}, resume=True)

resumed = CrawlState.open(
    state_dir,
    phase="crawl",
    input_path=seeds,
    semantic_config=v2,
    runtime_config={},
    resume=True,
    allow_policy_migration=True,
)
assert resumed.manifest["semantic_config"] == v2
resumed.close()
```

- [ ] **Step 3: Run migration tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/crawl/test_recovery.py tests/unit/crawl/test_state.py -q
```

Expected: failures because migration validation and the state-open option do not exist.

- [ ] **Step 4: Implement isolated migration validation**

In `recovery.py`, compare normalized snapshots after removing only these access keys:

```python
ACCESS_POLICY_SEMANTIC_KEYS = frozenset({
    "access_policy_revision",
    "browser_user_agent",
    "robots_txt_obey",
    "user_agent_name",
    "user_agent_version",
})
```

Raise a `ValueError` naming every unrelated changed key. Require saved revision to default to `1`, effective revision to equal `2`, saved robots enforcement to be true, and effective robots enforcement to be false.

In `CrawlState.open()`, call this validator only when normal semantic comparison fails and `allow_policy_migration=True`. After SQLite opens successfully, replace the manifest semantic snapshot with the effective snapshot. Store a migration entry containing `from_revision`, `to_revision`, and UTC timestamp. Reopening an already-revision-2 state must pass ordinary equality and must not append a duplicate entry.

- [ ] **Step 5: Run tests and lint**

Run:

```bash
uv run pytest tests/unit/crawl/test_recovery.py tests/unit/crawl/test_state.py -q
uv run ruff check src/hust_crawler/crawl/recovery.py src/hust_crawler/crawl/state.py tests/unit/crawl/test_recovery.py tests/unit/crawl/test_state.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add src/hust_crawler/crawl/recovery.py src/hust_crawler/crawl/state.py tests/unit/crawl/test_recovery.py tests/unit/crawl/test_state.py
git commit -m "feat: migrate legacy crawl access policy on resume"
```

---

### Task 4: Transactional Policy Requeue Without Successful Refetches

**Files:**
- Modify: `src/hust_crawler/crawl/recovery.py`
- Modify: `src/hust_crawler/crawl/state.py`
- Test: `tests/unit/crawl/test_state.py`

**Interfaces:**
- Produces: `POLICY_RETRY_REASONS = frozenset({"robots_disallowed", "login_required", "captcha_blocked", "access_denied"})`
- Produces: `CrawlState.requeue_policy_skips(recursive_hostnames: frozenset[str]) -> int`
- Produces: `CrawlState.requeue_zero_content_bootstrap(recursive_hostnames: frozenset[str]) -> int`
- Produces: scheduled records compatible with `CrawlState.iter_pending_scheduled_records()` and `UnifiedSpider.resume_records`

- [ ] **Step 1: Write a failing transactional requeue test**

Create state containing extracted, file-saved, robots-disallowed, legacy-login, CAPTCHA, access-denied, request-failed, 404, and cross-host records. Assert only policy reasons are changed by `requeue_policy_skips()`, successful payloads remain byte-for-byte equal, stale errors for requeued URLs are removed, and calling the method twice returns zero on the second call.

```python
count = state.requeue_policy_skips(frozenset({"a.test"}))
assert count == 4
pending = {row["url"] for row in state.iter_pending_scheduled_records()}
assert pending == {
    "https://a.test/denied",
    "https://a.test/legacy-login",
    "https://a.test/false-captcha",
    "https://a.test/robots-blocked",
}
assert state.requeue_policy_skips(frozenset({"a.test"})) == 0
```

- [ ] **Step 2: Write a failing zero-content bootstrap test**

Assert a zero-content recursive hostname gets a scheduled HTTPS root when its old root ended in an eligible policy/transport result or is absent. Assert a hostname with one extracted page gets no bootstrap, and a root ending in `host_out_of_scope` remains terminal.

```python
count = state.requeue_zero_content_bootstrap(frozenset({"a.test", "b.test", "c.test"}))
pending = {row["url"] for row in state.iter_pending_scheduled_records()}
assert "https://a.test/" in pending
assert "https://b.test/" not in pending
assert "https://c.test/" not in pending
assert count == 1
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/crawl/test_state.py -q
```

Expected: failures for missing requeue methods.

- [ ] **Step 4: Implement requeue helpers in one SQLite transaction**

Use a private helper that resets a record to the shape expected by resume:

```python
def _scheduled_retry(record: dict[str, object]) -> dict[str, object]:
    retry = dict(record)
    retry.update({"status": "scheduled", "frontier_action": "scheduled"})
    for key in ("completed", "final_url", "http_status", "reason"):
        retry.pop(key, None)
    return retry
```

Select only completed skipped records with an eligible reason whose parsed hostname is in `recursive_hostnames`. Before updating, explicitly skip rows whose status is `extracted` or `file_saved`. Update `payload`, `status='scheduled'`, and `completed=0`, and delete stale errors for the same URL within one `BEGIN IMMEDIATE` transaction.

For zero-content bootstrap, calculate extracted counts from `url_records`, use `https://<hostname>/` as the canonical root, and only insert/requeue roots that are absent or have an eligible policy/transport terminal result. Do not reset `host_out_of_scope`, true HTTP not-found, or successful roots.

- [ ] **Step 5: Run state tests and lint**

Run:

```bash
uv run pytest tests/unit/crawl/test_state.py -q
uv run ruff check src/hust_crawler/crawl/recovery.py src/hust_crawler/crawl/state.py tests/unit/crawl/test_state.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add src/hust_crawler/crawl/recovery.py src/hust_crawler/crawl/state.py tests/unit/crawl/test_state.py
git commit -m "feat: requeue recoverable crawl policy skips"
```

---

### Task 5: CLI and Runner Recovery Orchestration

**Files:**
- Modify: `src/hust_crawler/crawl/cli.py`
- Modify: `src/hust_crawler/crawl/runner.py`
- Test: `tests/unit/crawl/test_cli.py`
- Test: `tests/unit/crawl/test_runner.py`

**Interfaces:**
- Produces CLI flag: `--retry-policy-skips`
- Extends: `run_crawl(..., retry_policy_skips: bool = False) -> int`
- Consumes: `CrawlState.open(..., allow_policy_migration=retry_policy_skips)`
- Consumes: state requeue methods from Task 4

- [ ] **Step 1: Write failing CLI tests**

Add parser, validation, and forwarding coverage:

```python
def test_retry_policy_skips_requires_resume(tmp_path: Path, capsys) -> None:
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")
    result = cli.main([
        "--input", str(seeds),
        "--output", str(tmp_path / "crawl"),
        "--retry-policy-skips",
    ])
    assert result == 2
    assert "--retry-policy-skips requires --resume" in capsys.readouterr().err
```

Also assert `cli.main()` passes `retry_policy_skips=True` to `run_crawl()`.

- [ ] **Step 2: Write failing runner orchestration tests**

Capture `CrawlState.open()` arguments and spider resume records. Assert recovery mode enables migration, invokes policy requeue plus existing retryable transport requeue, restores the frontier after requeue, and supplies only newly pending records to the spider.

```python
assert run_unified_for_test(
    tmp_path,
    resume=True,
    retry_policy_skips=True,
) == 0
assert observed["allow_policy_migration"] is True
assert {row["url"] for row in observed["crawl_kwargs"]["resume_records"]} == {
    "https://a.test/denied",
    "https://a.test/robots-blocked",
}
```

- [ ] **Step 3: Run CLI/runner tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py -q
```

Expected: parser and `run_crawl()` reject the unknown option/signature.

- [ ] **Step 4: Implement CLI and runner wiring**

Add the Boolean parser option, enforce its dependency on `--resume` in both CLI and runner, and pass it through unchanged. In `run_crawl()`, execute recovery in this order:

```python
state = CrawlState.open(
    state_dir,
    phase="crawl",
    input_path=input_path,
    semantic_config=semantic_cfg,
    runtime_config=runtime_cfg,
    resume=resume,
    allow_policy_migration=retry_policy_skips,
)

if resume and retry_policy_skips:
    state.requeue_policy_skips(seeds.recursive_hostnames)
    state.requeue_retryable_failures()
    state.requeue_zero_content_bootstrap(seeds.recursive_hostnames)
```

Perform all requeue operations before `frontier.restore()` and `iter_pending_scheduled_records()`. Keep ordinary `--resume`, `--retry-failed`, `--retry-truncated`, and `--retry-access-gates` behavior backward compatible.

- [ ] **Step 5: Run tests and lint**

Run:

```bash
uv run pytest tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py -q
uv run ruff check src/hust_crawler/crawl/cli.py src/hust_crawler/crawl/runner.py tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add src/hust_crawler/crawl/cli.py src/hust_crawler/crawl/runner.py tests/unit/crawl/test_cli.py tests/unit/crawl/test_runner.py
git commit -m "feat: add resumable policy recovery command"
```

---

### Task 6: Per-Seed Completion Reporting

**Files:**
- Modify: `src/hust_crawler/crawl/state.py`
- Modify: `src/hust_crawler/crawl/runner.py`
- Test: `tests/unit/crawl/test_state.py`
- Test: `tests/unit/crawl/test_runner.py`

**Interfaces:**
- Produces: `CrawlState.host_completion(hostnames: frozenset[str]) -> dict[str, dict[str, object]]`
- Adds manifest key: `host_completion`
- Adds metrics key: `zero_content_hosts`

- [ ] **Step 1: Write failing host-completion tests**

Create one host with extracted content and one with only terminal records. Assert exact schema and reason counts:

```python
assert state.host_completion(frozenset({"a.test", "b.test"})) == {
    "a.test": {
        "status": "complete",
        "extracted_pages": 1,
        "terminal_reasons": {},
    },
    "b.test": {
        "status": "zero_content",
        "extracted_pages": 0,
        "terminal_reasons": {"host_out_of_scope": 1},
    },
}
```

- [ ] **Step 2: Write failing runner manifest tests**

Assert every recursive seed is present, exact URL-only hosts are excluded, `zero_content_hosts` is sorted, and a run with no URL failures but one zero-content recursive seed returns `complete_with_failures`/exit code 3.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/crawl/test_state.py tests/unit/crawl/test_runner.py -q
```

Expected: failures because completion accounting does not exist.

- [ ] **Step 4: Implement completion calculation and publication**

Iterate stored URL records once, group by `urlsplit(record["url"]).hostname`, count `status == "extracted"`, and count terminal reasons only for the requested recursive hostnames. Return sorted hostname keys for deterministic output.

Before `state.finish()`, calculate:

```python
host_completion = state.host_completion(seeds.recursive_hostnames)
zero_content_hosts = sorted(
    host for host, row in host_completion.items()
    if row["status"] == "zero_content"
)
metrics["zero_content_hosts"] = zero_content_hosts
```

Extend `CrawlState.finish()` with a `host_completion` keyword and publish it at top level. Include `len(zero_content_hosts)` in the runner failure count used by `finish_reason_to_exit()`. Print one concise stderr summary when the list is non-empty.

- [ ] **Step 5: Run tests and lint**

Run:

```bash
uv run pytest tests/unit/crawl/test_state.py tests/unit/crawl/test_runner.py -q
uv run ruff check src/hust_crawler/crawl/state.py src/hust_crawler/crawl/runner.py tests/unit/crawl/test_state.py tests/unit/crawl/test_runner.py
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add src/hust_crawler/crawl/state.py src/hust_crawler/crawl/runner.py tests/unit/crawl/test_state.py tests/unit/crawl/test_runner.py
git commit -m "feat: report crawl completion by active hostname"
```

---

### Task 7: End-to-End Recovery Fixtures and Documentation

**Files:**
- Modify: `tests/fixtures/scrapy_download_handler.py`
- Modify: `tests/fixtures/run_unified_fixture.py`
- Modify: `tests/integration/test_unified_crawl.py`
- Modify: `README.md`

**Interfaces:**
- Consumes CLI: `--resume --retry-policy-skips`
- Consumes manifest: `host_completion` and `metrics.zero_content_hosts`
- Verifies request log: previously successful URLs remain at request count 1

- [ ] **Step 1: Add failing integration fixtures**

Add a `policy_recovery_initial` mode that uses an explicit revision-1 configuration (`robots_txt_obey=true` and the legacy crawler string in `browser_user_agent`) to record one successful article, one `robots_disallowed` URL, one legacy `login_required` root, and one JavaScript shell. Add `policy_recovery_resume`; before invoking the CLI, this mode rewrites the fixture config to revision 2 with `robots_txt_obey=false` and the checked-in browser User-Agent. The recoverable URLs then return public HTML. Keep an out-of-scope redirect route that points to `https://other.test/`.

The static handler must branch on the actual request header:

```python
user_agent = request.headers.get(b"User-Agent", b"").decode("latin1")
hostname = urlsplit(request.url).hostname
if hostname == "ua-gated.test" and "Mozilla/5.0" not in user_agent:
    return html("<h1>Forbidden</h1>", status=403)(url, request)
```

- [ ] **Step 2: Write the failing end-to-end recovery test**

Run the initial fixture, snapshot extracted URLs and request counts, resume with policy recovery, and assert:

```python
assert all(after[url] == before[url] for url in extracted_before)
assert records["https://a.test/robots-blocked"]["status"] == "extracted"
assert records["https://ua-gated.test/"]["status"] == "extracted"
assert records["https://redirect.test/"]["reason"] == "host_out_of_scope"
assert manifest["host_completion"]["ua-gated.test"]["status"] == "complete"
```

- [ ] **Step 3: Run the integration test and confirm failure**

Run:

```bash
uv run pytest tests/integration/test_unified_crawl.py -q
```

Expected: recovery mode or recovered records are missing.

- [ ] **Step 4: Complete fixture plumbing and update README**

Teach `run_unified_fixture.py` to append `--resume --retry-policy-skips` for recovery-resume mode, reuse the seed file, and rewrite only the access-policy fields in the fixture config as described above. Replace README statements that the crawler obeys robots with the approved policy. Document this exact production continuation command:

```bash
uv run hust-crawl \
  --input docs/domain_active.txt \
  --output data/crawl \
  --resume \
  --retry-policy-skips
```

Document that successful URLs are retained, same-host scope remains strict, and true login/CAPTCHA/cross-host redirect outcomes remain terminal.

- [ ] **Step 5: Run all integration tests, unit tests, and lint**

Run:

```bash
uv run pytest tests/integration/test_unified_crawl.py -q
uv run pytest -q
uv run ruff check .
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/scrapy_download_handler.py tests/fixtures/run_unified_fixture.py tests/integration/test_unified_crawl.py README.md
git commit -m "test: verify resumable active-domain recovery"
```

---

### Task 8: Safely Continue the Existing Crawl and Verify No Refetch Regression

**Files:**
- Read: `data/crawl/state/index.sqlite3`
- Read after run: `data/crawl/manifest.json`
- Read after run: `data/crawl/url_records.jsonl`
- Read after run: `data/crawl/articles.jsonl`

**Interfaces:**
- Consumes the recovery command from Task 7
- Produces an operational verification report; no source commit is required

- [ ] **Step 1: Capture a pre-run state baseline**

Use read-only SQLite queries to record counts and a SHA-256 digest of the sorted URLs whose status is `extracted` or `file_saved`:

```bash
sqlite3 -readonly data/crawl/state/index.sqlite3 \
  "SELECT status, COUNT(*) FROM url_records GROUP BY status ORDER BY status;"
sqlite3 -readonly data/crawl/state/index.sqlite3 \
  "SELECT url FROM url_records WHERE status IN ('extracted','file_saved') ORDER BY url;" \
  | sha256sum
```

- [ ] **Step 2: Run the policy recovery continuation**

Run:

```bash
uv run hust-crawl \
  --input docs/domain_active.txt \
  --output data/crawl \
  --resume \
  --retry-policy-skips
```

Expected: the existing state opens successfully, eligible records are scheduled, and progress continues without a state-exists or semantic-configuration error.

- [ ] **Step 3: Verify preservation and recovery**

Run the same pre-run queries, inspect `manifest.host_completion`, and verify every pre-run successful URL still exists. The successful-URL set may grow but must not shrink. Report recovered hosts and remaining zero-content hosts from:

```bash
jq '.metrics.zero_content_hosts, .host_completion' data/crawl/manifest.json
```

- [ ] **Step 4: Run final repository verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
git status --short
```

Expected: tests and lint pass; only deliberate runtime output changes under ignored `data/crawl` are present.
