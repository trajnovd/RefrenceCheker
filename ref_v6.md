# References Checker v6 — Robust Download for Bot-Protected Sites

## Context

After v5, references with URLs to bot-protected sites (SEC, IMF, SSRN/doi.org, EUR-Lex, publisher paywalls fronted by AWS WAF / Cloudflare / Akamai) silently fail to download. They're left with `files: []` — no PDF, no HTML, no `.md` — which means claim-checking has nothing to verify against. The user must manually use Set Link / Upload PDF / Paste Content for every such reference.

**Concrete measurement** — a real project (`projects/finai-ch6-new`, 102 references) had **refs missing `.md` OR `.md` with only the abstract**, all with the same root cause:

| Ref | URL | Failure |
|---|---|---|
| `SEC15c3_5Final` | sec.gov/files/rules/final/2010/34-63241.pdf | HTTP 403 — SEC blocks default UA |
| `SECRegSCIRelease` | sec.gov/files/rules/final/2014/34-73639.pdf | HTTP 403 |
| `SECRegNMS` | sec.gov/rules-regulations/2005/06/regulation-nms | HTTP 403 |
| `imf2024gfsr` | imf.org/en/publications/gfsr/... | HTTP 403 |
| `BaileyBorweinLopezdePradoZhu2014` | doi.org/10.2139/ssrn.2568435 | HTTP 403 via SSRN |
| `breiman2001random` | ine.es/up/zEkXkJIZ | 200 OK but HTML (not PDF) |
| `fischer2018deep` | econstor.eu/bitstream/10419/157808/1/886576210.pdf | 200 OK but HTML — bot-check interstitial ("Making sure you're not a bot!"). Paper not on arXiv; no Wayback snapshot → **only Tier 2 (`curl_cffi`) would recover it.** |

The 5 bot-blocked cases are particularly painful because the URLs work perfectly in a normal browser. Our `requests`-based downloader has the right URL; it just can't get past the WAF.

**Goal:** automate the fallback chain so most bot-blocked cases are handled without user intervention. Manual replacement (v4) remains as the final escape hatch, but should become rare.

---

## 1. Design goals

1. **Free-tier first.** No new paid services required by default.
2. **Fail gracefully through tiers.** Each tier is tried in order; success short-circuits the rest. No user choice required.
3. **Minimize dependency weight.** Each tier can be opt-in via `settings.json` so users with simple projects aren't forced to install a browser.
4. **Respect site policies.** Fix SEC at the protocol level (correct User-Agent per their documented fair-use rule), not by masquerading.
5. **Transparency.** The `sources` list records which tier actually delivered the content (`URL`, `wayback`, `arxiv_preprint`, `curl_cffi`, `playwright`) so the user can audit provenance.

---

## 2. Tiered fallback strategy

The baseline v5 flow becomes tier 0 of a 5-tier cascade:

```
Tier 0: requests + default UA                      (current)
   ↓ fails (403 / empty / non-PDF)
Tier 1A: requests + per-site UA rules              [SHIP — Phase A]
   ↓ fails
Tier 1B: Archive.org Wayback                        [SHIP — Phase A]
   ↓ fails
Tier 1C: arXiv preprint title search                [SHIP — Phase A]
   ↓ fails
Tier 2: curl_cffi (Chrome TLS impersonation)        [OPTIONAL — Phase B]
   ↓ fails
Tier 3: Playwright headless browser                 [OPTIONAL — Phase C]
   ↓ fails
User-driven: Paste Content / Upload PDF / Set Link  (v4, unchanged)
```

### Tier 0 — Baseline (current)

`requests.get(url, headers=_HEADERS)` with a generic Chrome UA. Works for most academic/publisher PDFs hosted openly.

### Tier 1A — Per-site rules (SHIPPED — architecture + SEC rule)

**Status:** shipped ahead of v6 proper. SEC rule is live; the registry architecture is in place so adding more domains is a no-code change for users and a ~3-line change for maintainers.

#### Architecture (three layers)

```
┌─ download_rules.py  ─ BUILTIN_RULES dict          ← ships with app
│    (Python data; devs edit when adding SEC-class rules)
│
├─ settings.json → download.site_rules              ← users edit, no code change
│    (deep-merges over built-ins; {contact_email} templates)
│
└─ download_rules/ dir  (v7 — OPTIONAL)             ← drop-in Python plugins
     (sites needing multi-step flows, cookie handshakes, body transforms)
```

**Host matching:** longest suffix wins. So `www.sec.gov`, `efts.sec.gov`, `data.sec.gov` all share one `"sec.gov"` rule automatically; a more specific `"efts.sec.gov"` rule would override it.

**Rule shape:**

```python
{
  "sec.gov": {
    "headers": {
      "User-Agent": "RefChecker {contact_email}",
      "Accept-Encoding": "gzip, deflate",
    },
    "notes": "SEC fair-use: UA must include contact email",
    "rate_limit_per_sec": 10,   # reserved for v6 Phase-D throttling
  }
}
```

Template strings (`{contact_email}`) expand from `UNPAYWALL_EMAIL` at request time.

#### Built-in rules (`download_rules.py::BUILTIN_RULES`)

Ships with:
- **sec.gov** — SEC fair-use UA policy (recovers all 3 SEC failures from the motivating `finai-ch6-new` project).

Future candidates (when someone reports them failing):
- **econstor.eu** — needs bot-check bypass (requires curl_cffi from Phase B, not a header tweak)
- **imf.org** — `Accept: application/pdf` reportedly bypasses some interstitials (test before adding)
- **ssrn.com** — downloads require session cookies (also Phase B territory)

#### User overrides (`settings.json`)

```json
{
  "download": {
    "site_rules": {
      "imf.org": {
        "headers": {
          "Accept": "application/pdf",
          "Referer": "https://www.imf.org/"
        }
      }
    }
  }
}
```

Deep-merged over built-ins. Empty by default.

#### Startup banner

`config.print_startup_banner()` now shows active rules:

```
  --- Download rules ---
  sec.gov                 builtin   (SEC fair-use: UA must include contact email)
  imf.org                 user-override
```

#### Files

| File | Purpose |
|---|---|
| `download_rules.py` | NEW — `BUILTIN_RULES` dict + `resolve_headers()` function + `rules_summary()` for banner |
| `file_downloader.py` | `_headers_for(url)` now delegates to `download_rules.resolve_headers()` |
| `config.py` | `_DEFAULT_SETTINGS["download"]["site_rules"] = {}` ; banner prints rules list |
| `tests/test_set_link.py` | `TestPerSiteHeaders` — pins SEC behavior, subdomain match, non-SEC passthrough, bad-URL safety |

#### When a site needs more than headers

Some sites (econstor, EUR-Lex) need a real browser or TLS fingerprint, not just a header. Those fall through to Phase B (`curl_cffi`) or Phase C (Playwright) — this registry isn't the right place. The rule schema has a reserved slot for this:

```python
{
  "some-site.com": {
    "headers": {...},
    "force_tier": "curl_cffi",   # future — skip default requests, go straight to Tier 2
  }
}
```

Not implemented in v6 Phase A; listed as an open question.

### Tier 1B — Archive.org Wayback fallback

When direct download fails, query the Wayback Machine's CDX API for the closest capture:

```
GET https://archive.org/wayback/available?url=<original>
→ {closest: {url: "https://web.archive.org/web/20231115120000/<original>", ...}}
```

Download that archived URL (Wayback never bot-blocks its own cache). Coverage:
- ~60% of URLs globally
- ~80% for URLs >1 year old
- ~95% for URLs from government sites (crawled heavily)

Returns original status code, content-type, and bytes — near-perfect replay. **Recovers IMF, most EUR-Lex, many SSRN abstract pages, SEC legacy docs.**

### Tier 1C — arXiv preprint title search

When the bib URL fails and the reference has a title, call `search_arxiv(title)` (already in `api_clients/arxiv_client.py`) as a **content source** (not just metadata). If arXiv returns a match whose title ≥90% similar to the ref title, use its PDF.

Covers the common case where a paper was published behind a paywall (SSRN / Elsevier / Springer) but also has a preprint on arXiv. **Recovers the Bailey et al. case** (SSRN preprint has an arXiv twin).

### Tier 2 — `curl_cffi` (OPTIONAL)

`curl_cffi` impersonates a real browser's TLS ClientHello fingerprint — the specific byte sequence that Cloudflare / Akamai / AWS WAF check before even looking at the request headers.

```python
from curl_cffi import requests as cf_requests
r = cf_requests.get(url, impersonate="chrome120")
```

Defeats ~80% of WAF products **without running a browser**. ~200 MB dep (includes bundled curl + nss). Enabled via `settings.json`:

```json
{
  "download": {
    "use_curl_cffi_fallback": true
  }
}
```

Disabled by default — turn on after installing the dep.

### Tier 3 — Playwright headless (OPTIONAL)

Real Chromium, full JS execution, handles reCAPTCHA-gated and SPA-rendered sites. Costs:
- ~400 MB install (Chromium + Node runtime)
- 3–8 s per download (vs <1 s for `requests`)
- High memory per parallel worker
- Must be queued to avoid spawning N browsers

Only triggered when tiers 0–2 all fail, and only if `settings.json.download.use_playwright_fallback = true`.

**When to use:** projects heavy on EUR-Lex (WAF-JS-challenge) or modern SPA-rendered publisher pages. **When NOT to use:** academic-paper-heavy projects where arXiv + Unpaywall cover most needs.

### Out of scope for v6

Commercial scraping APIs (ZenRows, ScraperAPI, BrightData) are the ultimate escape hatch but introduce:
- Recurring cost
- Third-party dependency
- API key management

Documented as a **v7 candidate** if users report Tiers 0–3 aren't enough.

---

## 3. Recommended default configuration

```json
{
  "download": {
    "per_site_rules": true,        // Tier 1A  — DEFAULT ON
    "use_wayback_fallback": true,  // Tier 1B  — DEFAULT ON
    "use_arxiv_fallback": true,    // Tier 1C  — DEFAULT ON
    "use_curl_cffi_fallback": false, // Tier 2  — OPT-IN
    "use_playwright_fallback": false // Tier 3  — OPT-IN
  }
}
```

Phase-A tiers are free (no new deps) and should be on by default. Tiers 2–3 require installing heavy packages, so they're opt-in with a clear settings toggle.

---

## 4. Implementation — Phase A (the free tiers)

### 4.1 New module: `file_downloader_fallback.py`

```python
def download_with_fallback(url, target_path, is_pdf, bib_key, title=None):
    """Try every enabled tier in order. Returns {tier, success, final_url} or None."""
    settings = get_download_settings()

    # Tier 0: plain requests (current behavior)
    if _tier0_default_download(url, target_path, is_pdf):
        return {"tier": "direct", "final_url": url}

    # Tier 1A: per-site UA rules
    if settings.get("per_site_rules", True):
        rule = _match_site_rule(url)
        if rule and _tier1a_with_rule(url, target_path, is_pdf, rule):
            return {"tier": "direct+ua", "final_url": url}

    # Tier 1B: Wayback
    if settings.get("use_wayback_fallback", True):
        archived = _tier1b_wayback(url, target_path, is_pdf)
        if archived:
            return {"tier": "wayback", "final_url": archived}

    # Tier 1C: arXiv preprint (only for PDF requests with a title)
    if settings.get("use_arxiv_fallback", True) and is_pdf and title:
        arxiv_pdf = _tier1c_arxiv(title, target_path)
        if arxiv_pdf:
            return {"tier": "arxiv_preprint", "final_url": arxiv_pdf}

    # Tier 2/3 — gated by opt-in flags (Phase B / C)
    ...

    return None
```

### 4.2 Source-tag propagation

When a non-direct tier succeeds, add its tier name to `result["sources"]` as the **first** entry (mirrors how v5 tags bib-URL refs with `"URL"`):

```python
result["sources"].insert(0, outcome["tier"])  # e.g. "wayback"
```

The results view and dashboard breakdowns automatically pick up new tier names as colored badges.

### 4.3 Wiring into existing downloader

`file_downloader.py::download_reference_files` is the single integration point. Replace direct `_download_pdf` / `_download_page` calls with `download_with_fallback`. Same for `pre_download_bib_url`.

### 4.4 Settings

Extend `_DEFAULT_SETTINGS` in `config.py`:

```python
"download": {
    "per_site_rules": True,
    "use_wayback_fallback": True,
    "use_arxiv_fallback": True,
    "use_curl_cffi_fallback": False,
    "use_playwright_fallback": False,
    "wayback_timeout_s": 20,
    "site_rules": {
        "sec.gov": {
            "User-Agent": "{app_name}/{app_version} {unpaywall_email}",
            "Accept-Encoding": "gzip, deflate"
        }
    }
}
```

Template strings in the User-Agent (`{unpaywall_email}`, etc.) let users customize without Python.

### 4.5 Tests (Phase A)

`tests/test_download_fallback.py`:

- `test_sec_ua_rule_applied` — mocked requests.get; verifies User-Agent contains the email
- `test_wayback_fallback_on_403` — mocks primary fail, Wayback API return, final download
- `test_wayback_skipped_when_disabled_in_settings`
- `test_arxiv_fallback_finds_preprint_by_title`
- `test_arxiv_fallback_skipped_for_html_requests` (is_pdf=False)
- `test_tier_order_honored` — all tiers pass, we should stop at tier 0
- `test_all_tiers_fail_returns_none`
- `test_source_tag_added` — after Wayback success, result.sources has "wayback" first

### 4.6 Download-failure visibility (ALREADY SHIPPED — UX groundwork for v6)

A UX improvement that's in place ahead of v6's download-tier work, because it makes the failure mode *legible* to the user:

**Right-panel warning banner.** When `result.pdf_url` is set but `files.pdf` is missing (local download failed — site bot-blocked, wrong content-type, etc.), an amber banner renders above the tab bar:

```
⚠ PDF shown from remote URL — the file couldn't be downloaded locally
(site probably bot-blocks). Claim-checking has only the abstract.
Click Upload PDF (in this panel) to save the file so it's included in the .md.
```

The banner hides automatically on refs where `files.pdf` exists. Browsers happily render the remote `pdf_url` in the iframe (following redirects + cookies + JS challenges), so until now the user could see a PDF and not realize claim-checking wasn't using it.

**Why this matters for v6 planning.** Every new tier (Wayback, arXiv-preprint, curl_cffi, Playwright) has the same failure-mode question: *did it succeed or not, and does the user know?*

When v6 ships, the same banner logic should be extended:

| Situation | Banner message |
|---|---|
| `files.pdf` missing, `pdf_url` set | (current) PDF shown from remote — upload manually |
| `files.pdf` from Wayback snapshot | ℹ Content loaded from Web Archive (captured 2023-11-15) — may be outdated |
| `files.pdf` from arXiv preprint override | ℹ Fetched arXiv preprint instead of `<original_url>` — preprint may differ from published version |
| `files.pdf` from curl_cffi | (no banner — just a source badge in left panel) |
| `files.pdf` from Playwright browser capture | ℹ Captured via headless browser |

The banner slot (`#review-dl-warning`) is already in the DOM; v6 just needs to populate it based on `result.files_origin[<filetype>] = {tier, url, captured_at}`.

---

## 5. Phase B — `curl_cffi` (OPTIONAL)

When Phase A isn't enough (user reports specific WAF'd site), add:

### 5.1 New dep

```
curl_cffi>=0.7
```

Added to `requirements.txt` as optional/extras. Imported lazily inside `_tier2_curl_cffi()` so users who don't enable it don't need the install.

### 5.2 Implementation

```python
def _tier2_curl_cffi(url, target_path, is_pdf):
    try:
        from curl_cffi import requests as cf
    except ImportError:
        logger.warning("curl_cffi not installed; skipping Tier 2")
        return False

    try:
        r = cf.get(url, impersonate="chrome120", timeout=20, allow_redirects=True)
        if r.status_code != 200:
            return False
        if is_pdf and not r.content[:5].startswith(b"%PDF"):
            return False
        with open(target_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        logger.debug("curl_cffi failed: %s", e)
        return False
```

### 5.3 Startup banner update

`print_startup_banner` shows Tier 2 availability:

```
  --- Download fallback ---
  Per-site rules:        enabled
  Wayback fallback:      enabled
  arXiv preprint search: enabled
  curl_cffi (Tier 2):    enabled, curl_cffi available
  Playwright (Tier 3):   disabled
```

---

## 6. Phase C — Playwright (OPTIONAL, large)

### 6.1 New dep + one-time Chromium install

```
playwright>=1.40
```
After install: `playwright install chromium` (user-run, ~200 MB).

### 6.2 Pool/queue

A shared `BrowserPool` with N long-lived Chromium instances. Each download acquires → navigates → extracts content → releases. Prevents launching N Chromiums in parallel.

Max pool size default `1` (single-threaded Playwright), configurable. Downloads serialize through it; parallel reference lookups only block on Tier 3, not earlier tiers.

### 6.3 Page → PDF conversion

For HTML-only results, use Playwright's `page.pdf()` as a last resort to capture a snapshot. Uglier than an original PDF but readable.

---

## 7. Implementation phases

| Phase | Scope | Deps added | Approx effort |
|---|---|---|---|
| **A** | Tiers 1A, 1B, 1C + settings + tests | none | 1 day |
| **B** | Tier 2 `curl_cffi` | `curl_cffi` (optional) | 2–3 hours |
| **C** | Tier 3 Playwright + pool | `playwright` (optional) | 1 day |
| **D** | Dashboard UI: show tier counts in breakdown ("via Wayback: 4") | none | 2 hours |

Phase A is the minimum viable v6. B/C are opt-in upgrades.

---

## 8. Files to create / modify

| File | Action | What changes |
|---|---|---|
| `download_rules.py` | **DONE** | Site-rules registry — BUILTIN_RULES, resolve_headers, rules_summary |
| `file_downloader_fallback.py` | NEW | Tiered download orchestrator + all tier implementations |
| `file_downloader.py` | MODIFY | `download_reference_files` + `pre_download_bib_url` route through the orchestrator |
| `config.py` | MODIFY | Add `download` settings block to `_DEFAULT_SETTINGS`; extend banner |
| `app.py` | MODIFY | After `result["sources"].insert(0, tier)`, propagate through save |
| `static/js/app.js` | MODIFY | Render new source-tier badges in breakdowns |
| `static/css/style.css` | MODIFY | Tier-badge colors (`.source-badge--wayback`, `--arxiv_preprint`) |
| `requirements.txt` | MODIFY | Phase A: no new deps. Phase B: optional `curl_cffi`. Phase C: optional `playwright` |
| `tests/test_download_fallback.py` | NEW | Per-tier tests + full-chain tests |

---

## 9. Expected impact on `finai-ch6-new` (the motivating project)

After shipping Phase A:

| Ref | Recovered by | Tier |
|---|---|---|
| `SEC15c3_5Final` | SEC UA rule | 1A |
| `SECRegSCIRelease` | SEC UA rule | 1A |
| `SECRegNMS` | SEC UA rule | 1A |
| `imf2024gfsr` | Wayback | 1B |
| `BaileyBorweinLopezdePradoZhu2014` | arXiv preprint search (if preprint exists) *or* SSRN direct via Wayback | 1C or 1B |
| `breiman2001random` | Wayback (the ine.es URL has historic crawls) | 1B |
| `fischer2018deep` | **None of Phase A** — arXiv doesn't have it, Wayback has no snapshot | **Needs 1A** |

Projected: **~5 of 7 recovered by Phase A alone**. The remaining need Phase B (`curl_cffi` — defeats econstor's bot-check) or manual `Upload PDF`.

### Motivating case for Phase B priority

`fischer2018deep` is the clearest Phase B example: well-known paper (Fischer & Krauss 2018, EJOR) published only on econstor.eu behind a bot-check. No arXiv preprint, no Wayback archive. The amber "PDF shown from remote URL" banner (§ 4.6) tells the user why claim-checking has only the abstract, but until Phase B ships, the fix is to manually upload the PDF. If users hit this often, prioritize Phase B.

---

## 10. Open questions

1. **Default-on for Wayback:** it's free and fast, but adds a 1–3 s latency per failed download. Acceptable? Users with 500 refs might hit a few extra minutes of total runtime. Alternative: make it opt-in but show a "would have worked with Wayback" hint on failed refs.
2. **Tier ordering for PDF vs HTML:** should arXiv (Tier 1C) come **before** Wayback for PDF-seeking refs? arXiv gives the *real* current PDF; Wayback gives a historic snapshot. Probably yes → swap 1B and 1C when `is_pdf=True`.
3. **curl_cffi as default:** it's heavy (~200 MB) but defeats most WAFs. Could be default-on if install footprint isn't a concern in the target deployment. Currently proposed as opt-in out of caution.
4. **Wayback "replay" vs original fetch:** should we display in the UI that content is from Wayback rather than the live URL? (Relevant because the content might be years old.) Suggest: right-panel header shows `(from Wayback, captured 2023-11-15)` when applicable.
5. **Respectful rate limits:** SEC rule allows 10 req/s per IP. At `max_workers=4`, we're fine. But when we add SEC to the site-rules table, should we also add per-domain throttling? Probably yes — small semaphore on `sec.gov` keyed by host.
6. **Commercial fallback (v7?):** at what point (user-measurable) do we recommend pulling in ZenRows/ScraperAPI? Maybe: "if >10% of refs still fail after Phase A+B+C, show a banner pointing at the commercial tier."
7. **Pre-downloaded files preservation:** Wayback URLs are different from the original (different domain/path). When we store `{key}_pdf.pdf` from Wayback, do we remember the origin? Propose: add `result["files_origin"][file_type] = {tier, url}` so manual refresh knows whether to retry the live URL or the cached one.
8. **`force_tier` per site:** some sites (econstor, EUR-Lex) are known-bot-blocked. Skipping the doomed Tier 0 attempt saves ~2 s per ref. Propose a `force_tier` field in site rules: `{"econstor.eu": {"force_tier": "curl_cffi"}}`. Skipped until we have Phase B shipping to point at.
