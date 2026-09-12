# Resumable Complete Active-Domain Crawl Design

## Objective

Make `hust-crawl` continue the existing `data/crawl` run and recover public content from active seed hostnames that currently have no extracted pages. The recovery must preserve all previously extracted articles and must not fetch successful URLs again.

The crawler will no longer obey `robots.txt`, will use a browser-compatible User-Agent, and will retain exact-host scope. It will not authenticate, solve CAPTCHAs, or follow redirects to a different hostname.

## Current Problem

`docs/domain_active.txt` contains 77 hostnames, but the current crawl state has no `extracted` URL for 19 of them. The domain audit and crawler use different access behavior, so an audit result of `public_response` does not guarantee that the crawler can retrieve content.

The failures fall into several groups:

- HTTP 401/403 responses are all recorded as `login_required`, even when the server is only rejecting the crawler User-Agent.
- Some active sites shells require browser rendering.
- URLs skipped under the former robots policy are terminal and are not retried by normal resume.
- Transport failures and malformed sitemap responses can leave a host with no extracted homepage.
- Cross-host redirects are rejected. This behavior is intentional and remains unchanged.

The existing state also treats User-Agent and robots behavior as semantic configuration, so changing either currently makes the state incompatible with `--resume`.

## Scope and Invariants

The change applies to the unified `hust-crawl` workflow and its existing state database.

The following invariants must hold:

1. A URL already stored as `extracted` or `file_saved` is never requeued or fetched by policy recovery.
2. Existing articles, files, URL records, errors, route-family state, and discovery state remain intact.
3. Page expansion is restricted to exact hostname equality with a recursive seed.
4. Redirects to a different hostname remain `host_out_of_scope`, including redirects to another HUST hostname.
5. The crawler does not authenticate, submit login forms, solve CAPTCHAs, or otherwise bypass an interactive access gate.
6. Existing URL, query, file-size, storage, concurrency, and resource limits remain active.

## Access and Rendering Policy

Scrapy requests and Playwright navigation will use the same browser-compatible User-Agent configured by the crawler. The operator contact remains present in crawler configuration and logs, but it will not be embedded in the HTTP User-Agent if that changes the browser signature.

`ROBOTSTXT_OBEY` will be disabled for the crawl phase. The crawler may still fetch `robots.txt` to discover `Sitemap` declarations, but it will not use `Allow` or `Disallow` rules to reject URLs. Homepage startup and the standard sitemap probes remain available even when `robots.txt` is absent or inaccessible.

HTML handling remains two-stage:

1. Fetch with Scrapy.
2. Retry once with Playwright when the response is an HTML/JavaScript shell, has no meaningful readable content, or matches a narrowly defined browser-recoverable access response.

Playwright fallback is not used to bypass a real login form or CAPTCHA. A rendered login page is recorded as `login_required`; a rendered CAPTCHA is recorded as `captcha_blocked`.

Access classification will distinguish:

- `access_denied`: HTTP 401/403 without evidence of a login page;
- `login_required`: a page containing a page-level login form or authentication route;
- `captcha_blocked`: a page-level interactive CAPTCHA;
- `empty` or `javascript_shell`: an otherwise public page that needs rendering or has no extractable content.

## Resume and State Migration

A new explicit recovery option will be added:

```bash
uv run hust-crawl \
  --input docs/domain_active.txt \
  --output data/crawl \
  --resume \
  --retry-policy-skips
```

`--retry-policy-skips` requires `--resume`. It authorizes a one-way, transactional migration of compatible version-4 unified crawl state to the new access-policy revision. It does not generally disable semantic compatibility checks.

The migration will:

1. Validate the state version, phase, input hash, crawl strategy, asset mode, scope rules, and all unrelated semantic settings.
2. Permit only the known access-policy delta: old crawler User-Agent behavior and `robots_txt_obey=true` to the new browser-compatible User-Agent behavior and `robots_txt_obey=false`.
3. Record the policy migration and timestamp in the state manifest so it is idempotent and auditable.
4. Reclassify eligible terminal records as scheduled while retaining their provenance metadata.
5. Commit the migration atomically before starting network requests.

Eligible records are:

- records skipped as `robots_disallowed`;
- all records skipped as the legacy `login_required`, because the old state did not preserve enough detail to distinguish HTTP denial from a detected login form; each is retried once and classified under the new policy;
- records skipped as `captcha_blocked` before the improved page-level classification, so false positives can be rendered and classified again;
- retryable transport failures selected by the existing retry policy;
- homepage/bootstrap requests for an active hostname that still has zero extracted pages, when those requests are absent or ended in a recoverable policy/transport failure.

Ineligible records include successful URLs, true login/CAPTCHA pages after reclassification, cross-host redirects, URL-trap rejections, hard HTTP not-found responses, storage limits, and scope violations.

Normal `--resume` retains its current behavior. Recovery happens only when `--retry-policy-skips` is explicitly provided.

## Host Completion Accounting

The manifest will include a completion summary for every recursive seed under `host_completion`:

```json
{
  "host_completion": {
    "dlib.hust.edu.vn": {
      "status": "complete",
      "extracted_pages": 42,
      "terminal_reasons": {}
    },
    "example.hust.edu.vn": {
      "status": "zero_content",
      "extracted_pages": 0,
      "terminal_reasons": {
        "host_out_of_scope": 1
      }
    }
  }
}
```

Each host summary will report at least:

- number of extracted pages;
- `complete` when at least one page was extracted, otherwise `zero_content`;
- counts of terminal failure and skip reasons relevant to that hostname.

The command will print a concise end-of-run list of any `zero_content` hostnames so missing coverage is visible without manually scanning JSONL files. A zero-content hostname makes the run `complete_with_failures`, unless a stronger status such as `truncated` or `interrupted` already applies.

## Error Handling

Recovery must be safe to rerun. If the process is interrupted after migration, the next invocation with the same flags continues scheduled records from SQLite. A record is marked complete only after its final disposition is committed.

If state compatibility fails outside the explicitly supported policy migration, the command exits before modifying state and explains which semantic setting differs.

If a hostname still has zero extracted pages after recovery, the run finishes with failures and identifies the terminal reason outcome for that hostname. Cross-host redirect failures must include the rejected target hostname in diagnostic state, without expanding scope.

## Tests

Unit and integration coverage tests will verify:

- the new flag requires `--resume`;
- legacy version-4 state accepts only the intended access-policy migration;
- migration is transactional and idempotent;
- extracted and file-saved URLs are not requeued;
- legacy robots, access-denied, false-CAPTCHA, and retryable transport records are requeued;
- true login and CAPTCHA pages remain terminal after retry;
- browser-compatible User-Agent is used consistently by Scrapy and Playwright;
- robots middleware is disabled and robots startup is not a crawl prerequisite;
- same-host links are followed and cross-host links/redirects remain rejected;
- recovered pages can expand new same-host links;
- interrupted recovery resumes without duplicate successful downloads;
- the manifest reports per-host extracted counts and terminal zero-content reasons;
- the existing test suite continues to pass.

An integration fixture will model a host that returns 403 to the legacy crawler User-Agent and public HTML to the browser-compatible User-Agent. A second fixture will model a JavaScript shell requiring Playwright fallback. A third will assert that a redirect to another hostname stays out of scope.

## Documentation and Operation

The README will document the revised access policy, the exact-host boundary, and the recovery command. It will state explicitly that disabling robots compliance does not authorize authentication, CAPTCHA solving, or cross-host expansion.

After implementation, the existing crawl is continued with the recovery command. Verification compares the pre- and post-run state to prove that successful URL fetches were not repeated and reports which of the 19 zero-content active hostnames were recovered.
