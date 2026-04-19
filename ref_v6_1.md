# References Checker v6.1 — Expand download source coverage

**Status:** delta on top of v6. Lists **only new features** — v6's Tier 1A,
the tier-order architecture, and v6's open questions are unchanged except
where noted.

---

## 1. State of play (what v6 shipped vs what it planned)

**From v6 — shipped:**
- ✅ **Tier 0** — baseline `requests` + generic Chrome UA (`_HEADERS`)
- ✅ **Tier 1A** — per-site UA rules (`download_rules.py` with `BUILTIN_RULES`,
  `resolve_headers`, user overrides via `settings.json`, startup banner).
  Only `sec.gov` ships as a built-in rule.

**From v6 — NOT shipped:**
- ❌ Tier 1B (Wayback fallback)
- ❌ Tier 1C (arXiv preprint as a *download* fallback — arXiv is used in the
  metadata lookup pipeline but not triggered when primary download fails)
- ❌ Tier 2 (`curl_cffi`)
- ❌ Tier 3 (Playwright)
- ❌ `file_downloader_fallback.py` orchestrator module
- ❌ `files_origin` provenance dict
- ❌ `force_tier` per-site field
- ❌ v6 Phase-D throttling / rate-limit

**Post-v6 improvements (new, not in v6 spec):**
- **`_normalize_bib_url`** — rewrites `arxiv.org/abs/<id>` and
  `arxiv.org/html/<id>` to `/pdf/<id>` before download
- **`bib_url_unreachable` status** — when a *bib-entry* URL 4xx/5xx's or times
  out, the reference is flagged broken instead of silently falling through.
  This changes v6's plan: bib URL failures should NOT auto-fall-through to
  Wayback/arXiv — the author needs to know the URL is broken and fix it.
  *Fallback tiers apply only to URLs the lookup pipeline found, not to bib URLs.*
- **`_FRAGILE_PDF_DOMAINS`** — Wiley / SSRN / econstor / ScienceDirect /
  Springer / JSTOR / tandfonline / academic.oup.com. When Unpaywall or
  OpenAlex returns a PDF on one of these, we fire Google Search for a
  non-fragile alternate (university mirror, author homepage, arXiv).
- **Multi-pass Google Search** — surname-qualified, DOI-qualified, title-only,
  colon-split relaxed, doc-id, `filetype:pdf`. Recovers many citations the
  lookup pipeline would otherwise miss.
- **arXiv year-mismatch guard** — rejects arXiv title-search hits when the
  bib year and the arXiv submission year differ by more than 3 years.
- **Google Scholar title-overlap filter** — 60% word-overlap threshold to
  prevent wrong-paper matches on broad retries.
- **Ref-identity match (v6-adjacent)** — LLM verifies downloaded text against
  the bib's title/authors. Catches wrong-paper downloads after the fact;
  complements, does not replace, upstream relevance filtering.

---

## 2. Design goal of v6.1

Expand the set of free, open-access sources the app consults **before giving
up and marking a reference "not found"**, without adding heavy dependencies
(no Playwright, no curl_cffi in this version — those stay as v6 Phase B/C).

All new sources are API-reachable over plain `requests` — same dependency
footprint as today.

---

## 3. New download sources

### 3.1 Unpaywall alternate locations (currently we use only the first)

`GET https://api.unpaywall.org/v2/<doi>` already returns multiple
`oa_locations` — we pick only `best_oa_location`. Many papers have a
working mirror in `oa_locations[1+]` after the top pick 403s.

**Plan:** keep `best_oa_location` as the primary PDF URL, but expose
`oa_locations` as an ordered fallback list on the result dict:

```python
result["pdf_url_fallbacks"] = [loc["url_for_pdf"]
                                for loc in data.get("oa_locations", [])
                                if loc.get("url_for_pdf")]
```

When `_download_pdf(primary)` fails, walk this list. Cost: one extra API
field to extract, no new API call.

### 3.2 OpenAlex alternate locations

Same pattern: `primary_location` and `best_oa_location` are already used;
`locations[]` array contains the full list. Extract PDF URLs from every
location whose `pdf_url` is non-null, dedupe against `pdf_url_fallbacks`.

### 3.3 Semantic Scholar `openAccessPdf` as explicit fallback

Currently `lookup_semantic_scholar` returns `openAccessPdf.url` only when
`isOpenAccess` is true. Some papers list an `openAccessPdf` URL even when
not flagged OA — surface it too, append to `pdf_url_fallbacks`.

### 3.4 PubMed Central (PMC)

`pmc.ncbi.nlm.nih.gov` hosts free full-text for open-access biomed papers.
When the bib has:
- a `pubmed_id` / PMID / PMC ID in `note`, `eprint`, or URL, or
- a DOI, and Unpaywall/OpenAlex returns a PMC link in `oa_locations`

we can hit `https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{id}/pdf/` directly.
No API key needed.

**New helper:** `api_clients/pmc.py::lookup_pmc(pmc_id=None, doi=None)`.

### 3.5 OpenReview (OpenReview.net)

Peer-review archive for NeurIPS / ICML / ICLR / COLM / TMLR. Many AI/ML
references (especially v6.1-era projects with arXiv-adjacent conference
papers) have an `openreview.net/forum?id=<id>` URL or can be found by
title via their REST API:

```
GET https://api.openreview.net/notes/search?term=<title>
→ [{ id, content.title, content.pdf, ... }]
```

The `pdf` field is the direct download URL. Add to `pdf_url_fallbacks` when
the arXiv fallback finds no match for a conference paper.

**New helper:** `api_clients/openreview.py::lookup_openreview(title, venue_hint=None)`.

### 3.6 biorxiv / medrxiv / chemrxiv preprint servers

Per-domain APIs return PDFs by DOI:

```
https://api.biorxiv.org/details/biorxiv/<doi>
https://api.biorxiv.org/details/medrxiv/<doi>
```

Response includes `jatsxml` + `pdf` URLs. Covers bio/med preprints that
aren't on arXiv.

**New helper:** `api_clients/preprints.py` — unified shim for biorxiv,
medrxiv, chemrxiv (share the same API). Called only when the bib's
DOI prefix matches a known preprint-server pattern.

### 3.7 CORE (core.ac.uk)

CORE aggregates ~200M open-access papers from institutional repositories
worldwide. Free tier: 1000 req/day, no key for search. Covers thousands
of long-tail journals that aren't in Unpaywall.

```
GET https://api.core.ac.uk/v3/search/works?q=title:"<title>"
→ results[].downloadUrl   (direct PDF)
```

Requires free API key for full features; we can start without a key on
the public endpoints.

**New helper:** `api_clients/core.py::lookup_core(title, doi=None)`.

### 3.8 Zenodo / figshare (opportunistic)

General research archives often host author-uploaded PDFs. Zenodo API:

```
GET https://zenodo.org/api/records/?q=title:"<title>"&size=3
→ hits.hits[].files[].links.self  (direct download)
```

Lower hit rate than CORE but useful for recent datasets + supplementary
materials. Zero-configuration, no key.

### 3.9 DOI content-negotiation (direct PDF)

Many publishers support DOI content-negotiation — `doi.org` returns the PDF
directly when asked politely:

```
GET https://doi.org/<doi>
Accept: application/pdf
```

When this works (~20% of DOIs), we skip the whole Unpaywall/OpenAlex dance.
Tier 0-adjacent — try it as a fast-path once per DOI before running the
lookup chain. No new dep.

### 3.10 Wayback Machine (v6's Tier 1B — carried over)

Restated here because it still isn't shipped. Per-source spec unchanged
from v6 §2 Tier 1B. **Scope clarification for v6.1:** Wayback applies
only to `pdf_url` / `url` values the lookup pipeline discovered, NOT to
bib-entry URLs (those use `bib_url_unreachable`).

### 3.11 University / author homepage discovery (already partial)

Currently Google Search's `filetype:pdf` pass finds many author homepages
as a side effect. v6.1 makes this explicit:

- Extract the first author's affiliation from OpenAlex
  (`authorships[0].institutions[0].ror` → homepage domain)
- Targeted Google Search `site:<institution-domain> filetype:pdf "<title>"`
- Hit rate boost for older papers without arXiv preprints

Cheap addition — 1 extra Google CSE call per ref (quota-light).

### 3.12 SSRN — mirror discovery (direct download is Tier 2)

SSRN (`papers.ssrn.com`) is the primary source for economics / finance /
law working papers. It's already on `_FRAGILE_PDF_DOMAINS` because direct
`requests.get()` returns 403 — downloads require a session cookie and the
UI's "Download This Paper" button POST, and recent changes have added a
CAPTCHA gate on cold fetches.

**Direct download:** only feasible via Tier 2 (`curl_cffi`) or Tier 3
(Playwright with an established cookie jar). Documented here for
completeness; not added to Phase-A tiers.

**Phase-A strategy — find a non-SSRN mirror:**

1. Resolve the SSRN paper's DOI. SSRN papers have a DOI of the form
   `10.2139/ssrn.<abstract_id>`, extractable from the URL itself without
   fetching the page.
2. Feed the DOI to Unpaywall and OpenAlex. They usually know about the
   same paper via its journal-published version (if one exists) or a
   repository copy (NBER, RePEc, author homepage, university mirror).
3. If Unpaywall/OpenAlex find no mirror, fall through to RePEc (§3.13)
   and NBER (§3.14), which specifically index economics working papers.
4. As a last resort (still Phase-A), use Google Search with
   `"<title>" -site:ssrn.com filetype:pdf` to force an alternate domain.

**New helper:** `api_clients/ssrn.py` — `ssrn_doi_from_url(url)` and
`ssrn_abstract_id_from_url(url)`. No network calls; pure URL parsing.
Used by the orchestrator to enrich the fallback chain before walking
the other tiers.

### 3.13 RePEc / IDEAS — economics working-paper mirror discovery

RePEc (`ideas.repec.org`) is the canonical index for economics papers.
Each paper has a RePEc handle (e.g. `RePEc:nbr:nberwo:20592`) and an
IDEAS page listing *every* mirror: NBER, CEPR, author homepage, journal
publisher, SSRN. A single RePEc lookup can yield 3-10 download
candidates for one paper.

Unofficial API via the HTTP frontend:
```
https://ideas.repec.org/cgi-bin/htsearch?q=<title>
```
Returns paper pages that we scrape for the "Download" panel links.

**New helper:** `api_clients/repec.py::lookup_repec(title, doi=None)`.
Returns `{"mirrors": [<url>, ...]}` ordered by host reputation
(non-fragile domains first).

### 3.14 NBER — direct working-paper PDFs

NBER working papers have a predictable URL:
```
https://www.nber.org/system/files/working_papers/w<N>/w<N>.pdf
```

When the bib title matches an NBER working paper (detected via RePEc
§3.13 or by `\cite` key pattern), fetch directly. NBER serves these
freely without bot blocks.

NBER also has a REST API:
```
GET https://www.nber.org/api/v1/working_papers?search_query=<title>
```

**New helper:** `api_clients/nber.py::lookup_nber(title, doi=None)`.

### 3.15 HAL — French research archive

HAL (`hal.science`) hosts millions of papers from French institutions.
Many non-French papers too (the archive accepts any OA deposit).
Simple DOI-based API:
```
GET https://api.archives-ouvertes.fr/search/?q=doi_s:"<doi>"
  &fl=files_s,title_s,docType_s&wt=json
→ response.docs[].files_s   (list of PDF URLs)
```

**New helper:** `api_clients/hal.py::lookup_hal(doi=None, title=None)`.

### 3.16 OSF Preprints (osf.io/preprints)

OSF aggregates social-science, psychology, engineering preprints
(`PsyArXiv`, `SocArXiv`, `EngrXiv`, `MetaArXiv`, `LawArXiv`, etc — all
hosted on OSF infrastructure). One API covers all of them:
```
GET https://api.osf.io/v2/preprints/?filter[title]=<title>
→ data[].relationships.primary_file.links.related.href  → PDF
```

**New helper:** `api_clients/osf.py::lookup_osf(title, doi=None)`.

### 3.17 ResearchGate (Tier 2-only)

Many papers have a publicly-readable PDF on their author's ResearchGate
profile. Detection is easy (`researchgate.net/publication/<id>`), download
is hard: ResearchGate bot-blocks aggressively and requires a logged-in
session for the "Download full-text PDF" link.

**Same situation as SSRN:** mark as Tier 2 (`curl_cffi`), no Phase-A
action. Documented here so users understand why their ResearchGate
links don't auto-download.

### 3.18 Tier 2 — `curl_cffi` (OPT-IN, v6.1 Phase B)

`curl_cffi` impersonates a real browser's TLS ClientHello fingerprint —
the specific byte sequence that Cloudflare / Akamai / AWS WAF inspect
before even looking at the request headers. This defeats ~80% of WAF
products *without running a browser*.

**Key targets:**
- SSRN direct downloads (session cookies + WAF)
- ResearchGate publication PDFs
- econstor.eu (bot-check interstitial — the `fischer2018deep` case from v6 §9)
- Wiley / OUP / ScienceDirect / Springer cold fetches
- Cloudflare-fronted publisher sites

**Cost:** ~200 MB install (bundled curl + NSS TLS libs). Lazily imported
so users who don't enable it don't pay the dependency.

#### Implementation

```python
# file_downloader_fallback.py

def _tier_curl_cffi(url, target_path, *, is_pdf, bib_key, **_):
    """Tier 2: curl_cffi with Chrome TLS impersonation + session cookies."""
    try:
        from curl_cffi import requests as cf
    except ImportError:
        logger.warning("curl_cffi not installed; skipping Tier 2")
        return None

    settings = get_download_settings()
    impersonate = settings.get("curl_cffi_impersonate", "chrome120")
    timeout = settings.get("curl_cffi_timeout_s", 30)

    # Use a session so SSRN/ResearchGate cookie handshakes work across
    # the redirect chain (abstract page → set-cookie → download link).
    try:
        with cf.Session(impersonate=impersonate) as s:
            r = s.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code != 200:
                logger.debug("curl_cffi %s: status=%d", url, r.status_code)
                return None
            content = r.content
            if is_pdf and not content[:5].startswith(b"%PDF"):
                logger.debug("curl_cffi %s: not a PDF (first bytes=%s)",
                             url, content[:20])
                return None
            if len(content) > MAX_PDF_SIZE:
                return None
            with open(target_path, "wb") as f:
                f.write(content)
            return r.url  # final URL after redirects
    except Exception as e:
        logger.debug("curl_cffi error: %s", e)
        return None
```

#### Per-site session helpers (SSRN, ResearchGate)

Some sites need a two-step flow: visit the landing page first to acquire
the session cookie, then request the PDF URL. Wrap in per-host helpers
in `api_clients/ssrn.py` and `api_clients/researchgate.py`:

```python
# api_clients/ssrn.py
def download_ssrn_via_curl_cffi(abstract_id, target_path):
    from curl_cffi import requests as cf
    with cf.Session(impersonate="chrome120") as s:
        # Step 1: warm the session on the abstract page
        s.get(f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={abstract_id}",
              timeout=20)
        # Step 2: hit the delivery endpoint with the fresh cookies
        pdf_url = (f"https://papers.ssrn.com/sol3/Delivery.cfm/"
                   f"SSRN_ID{abstract_id}_code.pdf?abstractid={abstract_id}&mirid=1")
        r = s.get(pdf_url, timeout=30, allow_redirects=True)
        if r.status_code == 200 and r.content[:5].startswith(b"%PDF"):
            with open(target_path, "wb") as f:
                f.write(r.content)
            return pdf_url
    return None
```

#### Settings

```jsonc
{
  "download": {
    "use_curl_cffi_fallback":   false,       // disabled by default — opt-in after install
    "curl_cffi_impersonate":    "chrome120", // or "chrome119", "firefox133", "safari17_2"
    "curl_cffi_timeout_s":      30
  }
}
```

#### Startup banner

```
  --- Download fallback ---
  Tier 2 (curl_cffi):   enabled, curl_cffi 0.7.4 available
                        impersonate: chrome120
```

#### Force-tier for known WAF'd sites

`download_rules.py` gets a `force_tier` field so known WAF'd domains
skip the doomed Tier 0 attempt (saves ~2 s per ref):

```python
BUILTIN_RULES = {
    "sec.gov":      { ... },
    "econstor.eu":  {"force_tier": "curl_cffi",
                     "notes": "bot-check interstitial; needs TLS impersonation"},
    "papers.ssrn.com": {"force_tier": "curl_cffi",
                        "notes": "WAF + session cookies; see api_clients/ssrn.py"},
    "researchgate.net": {"force_tier": "curl_cffi",
                         "notes": "bot-blocked; needs TLS impersonation + session"},
}
```

The orchestrator honours `force_tier` by jumping straight to that tier
(instead of walking Tier 0 → fail → Tier 1.x). If the forced tier is
disabled in settings, the walk proceeds normally as a fallback.

---

### 3.19 Tier 3 — Playwright headless (OPT-IN, v6.1 Phase C)

Real Chromium with full JS execution. Last-resort tier for sites that
even `curl_cffi` can't crack: reCAPTCHA-gated pages, JS-challenge
interstitials (EUR-Lex, Elsevier), single-page-app publisher sites
that render content client-side.

**Costs:**
- ~400 MB install (Chromium + Node runtime; `playwright install chromium`
  is a user-run post-install step)
- 3–8 s per download (vs <1 s for `requests`/`curl_cffi`)
- High memory per concurrent instance (~150 MB)
- Must be queued to avoid spawning N browsers

**When to use:** projects heavy on EUR-Lex, Elsevier portal, modern
SPA-rendered publisher pages.
**When NOT to use:** academic-paper-heavy projects where arXiv +
Unpaywall + `curl_cffi` already cover most needs.

#### Browser pool

A shared `BrowserPool` with a capped number of long-lived Chromium
instances. Each download **acquires → navigates → extracts → releases**.
Prevents launching N Chromiums in parallel even when the rest of the
pipeline is running many workers.

```python
# browser_pool.py  (NEW)

import queue, threading

class BrowserPool:
    """Singleton pool of Playwright browser instances.
    Default size 1 — Playwright is memory-hungry and most users don't
    need more; bump via settings when throughput matters."""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls, size=1):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(size)
        return cls._instance

    def __init__(self, size):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._queue = queue.Queue()
        for _ in range(size):
            browser = self._pw.chromium.launch(headless=True)
            self._queue.put(browser)

    def acquire(self, timeout=60):
        return self._queue.get(timeout=timeout)

    def release(self, browser):
        self._queue.put(browser)

    def shutdown(self):
        while not self._queue.empty():
            try: self._queue.get_nowait().close()
            except queue.Empty: break
        self._pw.stop()
```

#### Tier implementation

```python
def _tier_playwright(url, target_path, *, is_pdf, bib_key, **_):
    try:
        from playwright.sync_api import TimeoutError as PwTimeout
    except ImportError:
        logger.warning("playwright not installed; skipping Tier 3")
        return None

    pool = BrowserPool.instance(size=get_download_settings().get("playwright_pool_size", 1))
    browser = pool.acquire(timeout=60)
    try:
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            accept_downloads=True,
        )
        page = ctx.new_page()
        try:
            if is_pdf:
                # Expect a download: browser triggers save-as flow when the
                # response is application/pdf.
                with page.expect_download(timeout=20_000) as dl_info:
                    try:
                        page.goto(url, wait_until="commit", timeout=15_000)
                    except PwTimeout:
                        pass  # goto may "fail" once the PDF save kicks in
                dl_info.value.save_as(target_path)
                return url
            else:
                # HTML: wait for the content, then snapshot.
                page.goto(url, wait_until="networkidle", timeout=20_000)
                html = page.content()
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(html)
                return url
        finally:
            ctx.close()
    except Exception as e:
        logger.debug("Playwright failed: %s", e)
        return None
    finally:
        pool.release(browser)
```

#### HTML → PDF last-resort capture

For pages that insist on being HTML (SPA publisher portals), convert
the rendered page to PDF so the rest of the pipeline (PDF extractor +
claim checker) has something to read:

```python
# When the request expected a PDF but only HTML is served:
pdf_bytes = page.pdf(format="A4", print_background=True)
with open(target_path, "wb") as f:
    f.write(pdf_bytes)
```

Uglier than an original PDF (lost original pagination, repositioned
figures) but readable — and better than dropping the reference.

#### Settings

```jsonc
{
  "download": {
    "use_playwright_fallback":  false,  // disabled by default — opt-in
    "playwright_pool_size":     1,      // bump for parallelism (~150 MB per slot)
    "playwright_timeout_s":     30,
    "playwright_html_to_pdf":   true    // §above — HTML → PDF when needed
  }
}
```

#### Shutdown hygiene

`BrowserPool.shutdown()` must run on app exit — stale Chromium processes
linger otherwise. Hook into Flask's `atexit` or app shutdown signal.

---

## 4. Priority / tier order

The orchestrator `file_downloader_fallback.py` (still to be built per v6 §4.1)
tries sources in the order below. Each tier short-circuits on success.

```
Tier 0     Direct requests + Tier 1A per-site UA                     [shipped]
Tier 0.5   DOI content-negotiation (Accept: application/pdf)         [NEW §3.9]
Tier 1     Primary pdf_url from lookup chain                         [shipped]
Tier 1.5   pdf_url_fallbacks (Unpaywall/OpenAlex/S2 alt locations)   [NEW §§3.1–3.3]
Tier 1B    Wayback Machine                                           [v6, unshipped]
Tier 1C    arXiv preprint by title+year+authors                      [v6, unshipped]
Tier 1D    OpenReview by title (NeurIPS / ICML / ICLR / TMLR / …)    [NEW §3.5]
Tier 1E    PubMed Central (if PMID/PMC/doi-to-pmc)                   [NEW §3.4]
Tier 1F    biorxiv/medrxiv/chemrxiv by DOI                           [NEW §3.6]
Tier 1G    OSF Preprints (PsyArXiv, SocArXiv, EngrXiv, …)            [NEW §3.16]
Tier 1H    RePEc / IDEAS mirror discovery (economics)                [NEW §3.13]
Tier 1I    NBER working papers (direct PDF)                          [NEW §3.14]
Tier 1J    CORE aggregator by title                                  [NEW §3.7]
Tier 1K    HAL (French archive)                                      [NEW §3.15]
Tier 1L    Zenodo / figshare by title                                [NEW §3.8]
Tier 1M    Affiliation-scoped Google Search                          [NEW §3.11]
Tier 2     curl_cffi (WAF TLS impersonation) — SSRN, ResearchGate    [v6 Phase B]
Tier 3     Playwright (full browser) — WAF+JS+CAPTCHA                [v6 Phase C]
Manual     Set Link / Upload PDF / Paste Content                     [shipped]
```

All Tier-1 variants are free + API-reachable over plain `requests`. v6.1's
Phase-A scope is Tiers 0.5, 1.5, 1D–1M plus the v6 carry-over tiers (1B, 1C).
SSRN and ResearchGate direct downloads remain Phase-B only (see §§3.12, 3.17).

---

## 5. Settings additions

```jsonc
{
  "download": {
    "use_doi_content_negotiation": true,   // §3.9
    "use_oa_fallbacks":            true,   // §§3.1–3.3
    "use_openreview_fallback":     true,   // §3.5
    "use_pmc_fallback":            true,   // §3.4
    "use_preprint_servers":        true,   // §3.6
    "use_osf_fallback":            true,   // §3.16
    "use_repec_fallback":          true,   // §3.13 — SSRN/econ mirror discovery
    "use_nber_fallback":           true,   // §3.14
    "use_core_fallback":           true,   // §3.7
    "use_hal_fallback":            true,   // §3.15
    "use_zenodo_fallback":         false,  // §3.8 — lower precision, opt-in
    "use_affiliation_search":      false,  // §3.11 — extra CSE quota, opt-in
    "core_api_key":                "",     // optional
    // Phase B — heavy deps, opt-in after install
    "use_curl_cffi_fallback":      false,  // §3.18 — ~200 MB
    "curl_cffi_impersonate":       "chrome120",
    "curl_cffi_timeout_s":         30,
    // Phase C — heaviest, opt-in after install + `playwright install chromium`
    "use_playwright_fallback":     false,  // §3.19 — ~400 MB + Chromium
    "playwright_pool_size":        1,
    "playwright_timeout_s":        30,
    "playwright_html_to_pdf":      true,
    // Global safety net
    "max_fallback_seconds":        15      // §10 Q3 — global tier-walk budget per ref
  }
}
```

Each tier is individually toggle-able. Latency-sensitive users can disable
tiers they don't need.

---

## 6. Provenance (`files_origin`)

Revived from v6 open-question #7 — essential now that so many tiers can
deliver the same `{bib_key}_pdf.pdf`:

```python
result["files_origin"] = {
    "pdf":  {"tier": "openreview", "url": "https://openreview.net/pdf?id=...",
             "captured_at": "2026-04-19T03:10:00+00:00"},
    "page": {"tier": "direct",     "url": "https://...", ...},
}
```

Surfaced in:
- Validity report per-citation block ("Source: PDF · via OpenReview")
- Right-panel PDF footer (replaces the existing `[LOCAL]/[REMOTE]` badge)
- `sources` list gets the tier name prepended so dashboard breakdowns
  pick it up automatically

---

## 7. Implementation skeleton

```python
# file_downloader_fallback.py  (NEW)

def download_with_fallback(url, target_path, *, is_pdf, bib_key, ref=None, title=None):
    """Returns {"tier": str, "final_url": str} on success, None on failure.
    `ref` is the full result dict — we can read pdf_url_fallbacks, doi,
    pmc_id, openreview_id, etc. from it."""
    s = get_download_settings()

    for tier_name, tier_fn, enabled in _tier_plan(is_pdf, ref, s):
        if not enabled:
            continue
        outcome = tier_fn(url, target_path, is_pdf=is_pdf,
                           bib_key=bib_key, ref=ref, title=title)
        if outcome:
            return {"tier": tier_name, "final_url": outcome}
    return None

def _tier_plan(is_pdf, ref, settings):
    yield "direct",                  _tier_direct,         True
    yield "doi_negotiation",         _tier_doi_negotiate,  settings["use_doi_content_negotiation"]
    yield "oa_fallbacks",            _tier_oa_alt_locs,    settings["use_oa_fallbacks"]
    yield "wayback",                 _tier_wayback,        settings["use_wayback_fallback"]
    yield "arxiv_preprint",          _tier_arxiv_preprint, settings["use_arxiv_fallback"] and is_pdf
    yield "openreview",              _tier_openreview,     settings["use_openreview_fallback"]
    yield "pmc",                     _tier_pmc,            settings["use_pmc_fallback"]
    yield "preprint_servers",        _tier_preprints,      settings["use_preprint_servers"]
    yield "osf",                     _tier_osf,            settings["use_osf_fallback"]
    yield "repec",                   _tier_repec,          settings["use_repec_fallback"]
    yield "nber",                    _tier_nber,           settings["use_nber_fallback"]
    yield "core",                    _tier_core,           settings["use_core_fallback"]
    yield "hal",                     _tier_hal,            settings["use_hal_fallback"]
    yield "zenodo",                  _tier_zenodo,         settings["use_zenodo_fallback"]
    yield "affiliation_search",      _tier_affiliation,    settings["use_affiliation_search"]
    yield "curl_cffi",               _tier_curl_cffi,      settings.get("use_curl_cffi_fallback", False)
    yield "playwright",              _tier_playwright,     settings.get("use_playwright_fallback", False)
```

Each tier is a pure function: `(url, target_path, **ctx) → final_url_str | None`.
New helpers live in `api_clients/`: `pmc.py`, `openreview.py`, `core.py`,
`preprints.py`, `osf.py`, `repec.py`, `nber.py`, `hal.py`, `ssrn.py`,
`zenodo.py`.

---

## 8. Test coverage

`tests/test_download_fallback.py` (NEW, per v6 §4.5 — extend with):

Per-tier unit tests, all with HTTP mocked:

- `test_doi_negotiation_returns_pdf`
- `test_oa_fallbacks_walked_in_order`  (primary 403 → second OA location works)
- `test_pmc_by_pmc_id` / `test_pmc_by_doi`
- `test_openreview_by_title`
- `test_biorxiv_by_doi` / `test_medrxiv_by_doi`
- `test_core_by_title` (no key)
- `test_zenodo_by_title`
- `test_affiliation_search_uses_ror_domain`
- `test_files_origin_recorded`  (tier name + captured_at stored)
- `test_tier_disabled_in_settings_is_skipped`
- `test_order_short_circuits_on_first_success`
- `test_force_tier_jumps_directly_to_named_tier` (§3.18 `force_tier` in rules)
- `test_force_tier_falls_through_when_tier_disabled`
- `test_global_budget_short_circuits_tier_walk` (§5 `max_fallback_seconds`)

**Tier 2 (curl_cffi) tests** — skipped when `curl_cffi` not installed
(via `@pytest.mark.skipif(_has_curl_cffi is False)`):

- `test_curl_cffi_returns_none_when_not_installed` (import-error path)
- `test_curl_cffi_session_warms_before_download` (two-step: warmup GET then PDF GET)
- `test_ssrn_via_curl_cffi_visits_abstract_first` (SSRN helper — mocked session)
- `test_curl_cffi_rejects_non_pdf_response` (HTML served under PDF URL)
- `test_curl_cffi_impersonate_setting_passed_through`

**Tier 3 (Playwright) tests** — same skip gate:

- `test_playwright_returns_none_when_not_installed`
- `test_browser_pool_reuses_instances` (5 acquires/releases → 1 launch)
- `test_browser_pool_shutdown_closes_all`
- `test_playwright_pdf_save_as_flow` (mocked page.expect_download)
- `test_playwright_html_to_pdf_fallback` (HTML-only response + setting on)
- `test_playwright_acquire_timeout_returns_none` (pool exhausted)

Plus one integration test per new tier to make sure the full
`download_with_fallback` orchestrator plumbs through correctly.

---

## 9. Impact estimate

Expected reach on a typical project (academic / mixed-field, ~100 refs):

| Source type | Current coverage | +v6.1 |
|---|---|---|
| Recent arXiv papers                        | ~95% | ~97% |
| Open-access journal articles (via DOI)     | ~70% | ~92% *(DOI-negot + OA fallbacks + CORE + HAL)* |
| Bot-blocked publishers (Wiley, OUP, …)     | ~40% *(fragile-domain → Google)* | ~55% *(+ Wayback + CORE + HAL)* |
| Conference papers (NeurIPS/ICML/ICLR)      | ~70% | ~95% *(OpenReview)* |
| Biomed papers (PubMed / biorxiv)           | ~30% | ~85% *(PMC + biorxiv)* |
| Social-science / psychology preprints      | ~40% | ~85% *(OSF: PsyArXiv, SocArXiv)* |
| Economics working papers (SSRN, NBER, CEPR)| ~30% *(fragile → Google)* | ~75% *(RePEc + NBER mirrors, SSRN direct still Phase B)* |
| SEC / IMF / govt docs                      | ~70% *(Tier 1A)* | ~85% *(+ Wayback)* |

Overall baseline expectation: move from ~75% coverage to ~90%+ coverage
on mixed-field projects, without installing any new Python packages.
SSRN/ResearchGate direct downloads require Phase B (`curl_cffi`); Phase A
recovers most SSRN papers via their NBER/CEPR/journal mirrors.

### 9.1 With Phase B (`curl_cffi`) enabled

Adding `curl_cffi` (opt-in, ~200 MB) pushes coverage further:

| Source | Phase A only | + Phase B |
|---|---|---|
| SSRN direct                         | ~0% *(always 403)*     | ~85% *(session + TLS impersonation)* |
| ResearchGate publication PDFs       | ~0%                    | ~70% |
| econstor.eu *(fischer2018deep case)* | ~0% *(bot-check)*     | ~95% |
| Wiley / OUP / ScienceDirect cold    | ~10% *(via Google)*   | ~60% |
| Cloudflare-fronted OA publishers    | ~40%                   | ~85% |

Overall: ~90% → ~95% on mixed-field projects.

### 9.2 With Phase C (Playwright) enabled

Adding Playwright (opt-in, ~400 MB + Chromium) covers the long tail:

| Source | Phase A+B | + Phase C |
|---|---|---|
| EUR-Lex *(JS-challenge interstitial)* | ~5%  | ~85% |
| Elsevier portal SPAs                  | ~30% | ~80% |
| reCAPTCHA-gated pages                 | ~0%  | ~40% *(best-effort: often solves automatically)* |
| SPA publisher portals (html→pdf)      | ~0%  | ~75% *(snapshot via `page.pdf()`)* |

Overall: ~95% → ~98% on mixed-field projects, at the cost of 3–8 s per
Tier 3 download.

---

## 10. Open questions

1. **Ordering within Tier 1 variants** — should OA-alternates (§1.5) come
   before Wayback, or after? Alt OA locations are current; Wayback is
   historic. Proposed: alt OA first (current content is preferable).
2. **Dedup across tiers** — OpenAlex `locations[]` and Unpaywall
   `oa_locations` often overlap. Dedupe by normalized URL host+path before
   walking.
3. **Time budget** — with 10+ tiers each at ~2 s timeout, worst-case a
   single ref can block for 20 s. Propose a global `max_fallback_seconds`
   setting (default 15 s) that short-circuits the tier walk.
4. **Ref-match re-check on tier change** — when a tier other than the
   primary succeeds, should we automatically re-run the LLM identity check?
   Arguably yes — an alt source could be a different paper. Proposed: yes
   when `result.files_origin.pdf.tier != "direct"`.
5. **biorxiv date-filter** — biorxiv API accepts a date. When the bib has
   a year, restrict the search to ±1 year for precision.
6. **OpenReview venue hint** — if the bib `journal` / `booktitle` contains
   "NeurIPS", "ICML", "ICLR", pre-filter OpenReview search to that venue
   for ~10x precision.
7. **Should `pdf_url_fallbacks` live on `result` or on `result.files_origin`?**
   Former is more convenient; latter is cleaner. Proposed: `result`, with
   the chosen one copied into `files_origin` after success.

---

## 11. Optimization / refactor plan — do this *before* adding tiers

A code audit of the shipped v5/v6 downloader surfaces friction points
that will compound the moment we start wiring in 10+ new tiers. Addressing
these first ("Phase A0") makes the tier work a mechanical extension
instead of a rewrite.

### 11.1 Connection reuse — biggest win

**Finding:** every API call and every download is `requests.get(...)` with
no `Session`. With ~15 network calls per reference (6 API clients + up to
9 tiers × download attempts), each paying TLS handshake + TCP setup, we
waste ~200–600 s on a 100-reference project.

**Action:** single `http.Session` registry in a new module `http_client.py`:

```python
# http_client.py (NEW)
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = None
_lock = threading.Lock()

def get_session():
    """Process-wide requests.Session with HTTPS keepalive + pooled connections."""
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                s = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=32, pool_maxsize=32,
                    max_retries=Retry(total=2, backoff_factor=0.5,
                                       status_forcelist=[502, 503, 504],
                                       allowed_methods=["GET", "HEAD"]))
                s.mount("https://", adapter); s.mount("http://", adapter)
                _session = s
    return _session
```

All `requests.get(...)` sites migrate to `get_session().get(...)`. One
low-risk refactor, measurable win (expect ~40–60% end-to-end speedup
on projects with many references).

### 11.2 Unified fetch primitive — remove duplication

**Finding:** `_download_pdf` and `_download_page` duplicate ~40 lines of
status classification + failure recording. Adding Tier 2 (curl_cffi) and
Tier 3 (Playwright) would triplicate it.

**Action:** one `Fetcher` protocol + one validator:

```python
# file_downloader_fallback.py (NEW)

class FetchResult(NamedTuple):
    ok: bool
    content: bytes | None        # None on failure
    final_url: str | None
    http_status: int | None
    kind: str | None             # "http_4xx" | "http_5xx" | "network" | "validation" | None

def validate_pdf(content: bytes) -> tuple[bool, str]:
    if not content[:5].startswith(b"%PDF"):
        return False, "not_a_pdf"
    if len(content) > MAX_PDF_SIZE:
        return False, "exceeds_max_size"
    return True, ""

# Every tier implements: fetch(url, *, is_pdf, ref, timeout) -> FetchResult
```

`_download_pdf`/`_download_page` become one-liners that call
`Tier0Fetcher().fetch(url, ...)` and persist the bytes. Each tier
(curl_cffi, wayback, openreview, ...) is its own Fetcher subclass
implementing a single method. ~60 lines deleted from `file_downloader.py`,
no behaviour change.

### 11.3 Stream PDFs to disk instead of buffering

**Finding:** `_download_pdf` accumulates `chunks = []` and joins into a
50 MB `bytes` object before writing — 2× memory peak, needless GC pressure.

**Action:** write each chunk directly to a temp file, rename on success:

```python
tmp = path + ".partial"
with open(tmp, "wb") as f:
    first = next(resp.iter_content(chunk_size=8192), b"")
    if not first[:5].startswith(b"%PDF"):
        return FetchResult(ok=False, ..., kind="validation")
    f.write(first)
    total = len(first)
    for chunk in resp.iter_content(chunk_size=65536):  # larger chunks from here
        total += len(chunk)
        if total > MAX_PDF_SIZE:
            os.remove(tmp); return FetchResult(ok=False, ..., kind="validation")
        f.write(chunk)
os.replace(tmp, path)
```

Validates magic bytes on the first chunk (fails fast), streams the rest,
and atomically renames — crash-safe by construction.

### 11.4 Single source of truth for fragile / noncontent domains

**Finding:** `_FRAGILE_PDF_DOMAINS` is declared *twice* —
`lookup_engine.py:67` and `api_clients/google_search.py:33` — with a
comment "keep in sync." `_NONCONTENT_DOMAINS` likewise lives in
`google_search.py` only but is consulted by the lookup logic indirectly.

**Action:** both lists move to `download_rules.py`:

```python
# download_rules.py
FRAGILE_PDF_DOMAINS    = (...)
NONCONTENT_DOMAINS     = (...)

def is_fragile(url):    ...
def is_noncontent(url): ...
```

Existing callers import from the single source. Deletes ~20 lines, kills
a class of drift bugs.

### 11.5 `_normalize_bib_url` registry

**Finding:** `_normalize_bib_url` is a hardcoded arxiv-abs/arxiv-html
regex. Every new landing-page type requires editing the function.

**Action:** pluggable registry:

```python
# url_normalizers.py (NEW)
NORMALIZERS = [
    (_arxiv_abs_re,     lambda m: f"https://arxiv.org/pdf/{m.group(1)}"),
    (_arxiv_html_re,    lambda m: f"https://arxiv.org/pdf/{m.group(1)}"),
    (_doi_org_re,       _resolve_doi_to_content),      # §3.9 lands here
    (_pubmed_re,        _pubmed_to_pmc),                # §3.4
    (_openreview_re,    _openreview_forum_to_pdf),     # §3.5
    ...
]
```

New tiers register their normalizer at import time. `_normalize_bib_url`
walks the registry until one matches.

### 11.6 Timeouts + retries from settings, not hardcoded

**Finding:** `timeout=30` / `timeout=20` baked in. No retry on transient
failures.

**Action:** inherit from `_session`'s retry adapter (§11.1) for 5xx/
timeouts. Per-tier timeout comes from settings:

```jsonc
{ "download": {
    "timeouts": { "default_s": 20, "pdf_s": 30, "wayback_s": 15, "playwright_s": 45 }
}}
```

### 11.7 Per-host rate limiter — use the dormant setting

**Finding:** `download_rules.BUILTIN_RULES["sec.gov"]["rate_limit_per_sec"] = 10`
is declared but never enforced. With `max_workers=4` and a project heavy
on SEC refs, we're fine today; with Phase B we'll hit more sites that
expect rate-limiting.

**Action:** token-bucket in `download_rules.py`:

```python
# download_rules.py
_buckets = {}        # host -> TokenBucket
_buckets_lock = threading.Lock()

def acquire_for(url):
    rule = _match_rule(url)
    rate = (rule or {}).get("rate_limit_per_sec")
    if rate is None: return
    host = urlparse(url).hostname
    with _buckets_lock:
        b = _buckets.setdefault(host, TokenBucket(rate))
    b.acquire()   # blocks if over budget
```

Called from the fetcher primitive, transparent to tier code.

### 11.8 Per-host "last successful tier" cache

**Finding:** for a host where Tier 0 always fails (econstor.eu,
papers.ssrn.com), walking Tier 0 first on every refresh wastes 2–5 s.
`force_tier` (§3.18) handles this *statically* (registry entry); but
on a project with many refs from the *same* WAF'd host, dynamic learning
would skip 9/10 Tier 0 attempts too.

**Action:** in-memory LRU `{host: (best_tier, learned_at)}` with 1-hour
TTL. On first ref from a host, walk normally; cache the successful tier.
Subsequent refs from the same host skip straight to that tier (walking
lower tiers only if the cached tier fails).

Purely a latency optimization — the cache is advisory, never blocks
correctness.

### 11.9 URL dedup across tiers

**Finding:** Unpaywall `oa_locations` and OpenAlex `locations[]` commonly
overlap. If both return `https://example.edu/paper.pdf`, we try it twice.

**Action:** the orchestrator collects *all* candidate URLs across Tier 1
variants up front, deduplicates on `(host, path, query_sans_tracking)`,
then walks the deduped list:

```python
candidates = list(dict.fromkeys(  # preserve order, dedup
    [primary] + pdf_url_fallbacks + wayback_candidates + ...
))
```

Saves redundant 403 round-trips. Free win once the orchestrator exists.

### 11.10 Provenance (`files_origin`) is scaffolding, not an afterthought

**Finding:** v6.1 §6 proposes `files_origin` but the code has no hook
for it. When a new tier succeeds, it has nowhere to stamp its identity.

**Action:** add the field to `result` in `lookup_engine.py:process_reference`
at init (`result["files_origin"] = {}`), and a single helper:

```python
def record_origin(result, filetype, tier, url):
    result.setdefault("files_origin", {})[filetype] = {
        "tier": tier, "url": url,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
```

Every tier calls `record_origin(result, "pdf", "openreview", final_url)`
on success. Mechanical; adds provenance without extra state.

### 11.11 Download-log on the result (for the validity report)

**Finding:** today, when a ref ends up `not_found`, the author has no
way to see *what was tried*. With 10+ tiers, they'd want to know
"Wayback: no snapshot; arXiv: no match; CORE: no match" so they can
decide whether to wait for Phase B or manually upload.

**Action:** append a per-tier trace on the result:

```python
result["download_log"] = [
    {"tier": "direct",    "url": "https://...",  "ok": False, "kind": "http_403"},
    {"tier": "wayback",   "url": "https://...",  "ok": False, "kind": "no_snapshot"},
    {"tier": "arxiv",     "url": None,            "ok": False, "kind": "no_match"},
    {"tier": "openreview","url": "https://...",  "ok": True,  "kind": None},
]
```

Surfaced in the validity report's "Downloaded source" block as a
collapsed `<details>` when the author wants to audit. Kept compact
(≤ 10 entries) so project.json doesn't bloat.

### 11.12 Tier-outcome telemetry on the dashboard

**Finding:** users have no empirical basis to decide whether to enable
Phase B (curl_cffi) or Phase C (Playwright). "It might help you" is
not actionable.

**Action:** a rolling per-project counter in `project.json`:

```jsonc
{
  "download_stats": {
    "total_attempts": 312,
    "per_tier": { "direct": 198, "openreview": 18, "wayback": 9, ... },
    "failed_by_host": { "papers.ssrn.com": 7, "econstor.eu": 3 }
  }
}
```

A small dashboard card surfaces the top failed-by-host and a hint:
*"7 refs blocked on SSRN — enable curl_cffi fallback to recover most."*

### 11.13 Ref-match re-check hook (answers §10 Q4)

**Finding:** §10 Q4 is open; §11.10 + §11.11 make it trivial.

**Action:** in the auto-ref-match trigger (`_maybe_auto_check_ref_match`
in `app.py`), check whether `result.files_origin.pdf.tier` differs from
the previously-recorded tier. If it changed (alt source, Wayback,
preprint), force-rerun the identity check regardless of
`auto_check_on_download`:

```python
previous = (previous_result or {}).get("files_origin", {}).get("pdf", {}).get("tier")
current  = (result.get("files_origin") or {}).get("pdf", {}).get("tier")
if previous != current:
    check_and_save(slug, bib_key, force=False)  # non-manual only
```

### 11.14 `bib_url_unreachable` is the ONLY bib-URL path

**Finding:** the fallback tiers (Wayback, arXiv preprint, etc.) address
*lookup-derived URLs* — URLs the app discovered. Bib URLs (author-supplied)
have opposite semantics per the shipped `bib_url_unreachable` flow: if
the bib URL fails, the reference is *marked broken*, not silently replaced.

**Action:** make this invariant explicit in the orchestrator by branching
on entry point:

```python
def download_with_fallback(url, *, is_bib_url, ...):
    if is_bib_url:
        # No fallbacks. Either Tier 0 succeeds or we surface bib_url_unreachable.
        return _tier_direct_only(url, ...)
    return _walk_all_tiers(url, ...)
```

Prevents a future contributor from accidentally wiring a Wayback fallback
into the bib-URL path.

### 11.15 Refactor-first phase, inserted at the top of §7

| Phase | Scope | Duration |
|---|---|---|
| **A0 (refactor)** | §§11.1–11.5, 11.10, 11.14. No new tiers. Measurable: connection-reuse perf test, dedup of fragile-domain constants. Ship as its own commit. | 1 day |
| **A1** | Tier 1.5 (OA fallbacks), Tier 1B Wayback, Tier 1D–1M. Each tier is ~40 LOC thanks to A0. | 1 day |
| **A2** | §§11.7–11.9 (rate limiter, per-host cache, URL dedup). | 0.5 day |
| **A3** | §§11.11–11.13 (download_log, telemetry, re-check hook). | 0.5 day |
| **B** | Tier 2 `curl_cffi` (§3.18) + per-host helpers. | 0.5 day |
| **C** | Tier 3 Playwright (§3.19) + BrowserPool. | 1 day |
| **D** | Dashboard tier-count cards + validity-report trace UI. | 0.5 day |

Each phase is individually shippable; A0 unblocks everything else.

### 11.16 Test-first checklist for the refactor

Before A0 lands, these tests must pass so correctness is preserved
across the refactor:

- All of `tests/test_set_link.py` (already pins behaviour)
- All of `tests/test_bib_url_download.py` (already pins behaviour)
- NEW: `tests/test_http_client.py` — `get_session()` returns a
  singleton; retries work on 502/503/504; pool size respected.
- NEW: `tests/test_fetch_primitive.py` — `FetchResult` shape, content
  validator, streamed write with magic-byte early-reject.
- NEW: `tests/test_url_normalizers.py` — registry dispatch, first-match-wins.
- NEW: `tests/test_download_rate_limit.py` — token bucket respects
  `rate_limit_per_sec`, doesn't block when host has no rule.

---

## 12. User-facing visibility — surfacing the tier in the UI

The tier chain has to be *visible* to be useful. When a paper came from
Wayback, from an NBER mirror, or from Playwright, the author should see
that at a glance — and when a long operation is running, they should see
*what is happening* (not a spinner). This section specifies the UI work
that lands alongside the backend tiers.

### 12.1 Live progress during bulk lookup — tier events on SSE

The existing `/stream/<session_id>` SSE stream emits one progress event
per reference-completion. Extend the protocol with intermediate events
so the UI can narrate what the downloader is currently trying:

**Current event:**
```json
event: progress
data: {"index": 12, "total": 37, "bib_key": "foo2024", "status": "found_pdf"}
```

**New intermediate event types (emitted during a single ref's tier walk):**
```json
event: tier_attempt
data: {"bib_key": "foo2024", "tier": "direct",    "url": "https://..."}

event: tier_attempt
data: {"bib_key": "foo2024", "tier": "wayback",   "url": "https://web.archive.org/..."}

event: tier_attempt
data: {"bib_key": "foo2024", "tier": "openreview","url": "https://openreview.net/..."}

event: tier_ok
data: {"bib_key": "foo2024", "tier": "openreview","url": "https://openreview.net/pdf?id=..."}
```

**Processing view (View 2) changes:**
- Below the main progress bar, a `<div class="processing__tier-trace">`
  shows the live tier attempts for the currently-active ref as a small
  chip list:
  ```
  Checking foo2024  ▸  direct (403)  ▸  wayback (miss)  ▸  openreview ✓
  ```
- Completed refs' traces scroll off; only the last ~3 are visible so
  the user sees activity without overwhelming.

**Backend hook:**
```python
# file_downloader_fallback.py
def download_with_fallback(url, *, progress=None, ...):
    for tier_name, tier_fn, enabled in _tier_plan(...):
        if not enabled: continue
        if progress: progress({"tier": tier_name, "state": "try", "url": url})
        result = tier_fn(url, ...)
        if progress: progress({"tier": tier_name,
                               "state": "ok" if result else "miss",
                               "url": result or url})
        if result: return {"tier": tier_name, "final_url": result}
```

The `progress` callable is wired by the SSE producer in `app.py`.

### 12.2 Persistent source-origin badges (after download completes)

`files_origin` (§6, §11.10) is the data layer. The UI adds three surfaces:

#### Results-view card
A new badge next to the existing status badge:

```
[PDF Available ✓]  [via OpenReview]   arxiv · openalex · semantic_scholar
```

Color-coded by tier category:
- green: `direct`, `arxiv_preprint`, `openreview`, `pmc`, `nber`
- blue:  `wayback`, `core`, `hal`, `zenodo`, `osf`, `repec`
- amber: `curl_cffi`, `playwright`, `doi_negotiation`
- red:   n/a (failures don't render a tier badge)

#### Left review panel card
Small tier pill between the source-type pill and the match indicator:

```
L142  cite_key  [html]  [OpenReview]  [✓]
```

#### Right review panel — PDF tab footer
The existing footer currently shows `[LOCAL]` or `[REMOTE]`. Replace with:

```
Downloaded via OpenReview · 2026-04-19 03:10 UTC · openreview.net/pdf?id=ABC
                                                   ^^ clickable, opens new tab
```

Same footer pattern for the HTML tab. When the tier is `wayback`, the
footer reads `Content from Web Archive · captured 2023-11-15` so the
author knows they're looking at a historic snapshot.

### 12.3 Failure visibility — actionable telemetry

When all Phase-A tiers fail, the user should know *which* tiers were
tried and *why* each failed, and what to do next.

**Per-reference:** inside the right-panel block, when `status = not_found`
or `files.md` is missing, render a collapsed `<details>` summarizing the
download log (backed by `result.download_log` from §11.11):

```
▸ 7 download attempts tried (click to expand)
  ────────────────────────────────────────────
  ✗ direct            https://...          HTTP 403
  ✗ oa_fallbacks      https://...          HTTP 403 (2 alt URLs)
  ✗ wayback           —                    no snapshot for URL
  ✗ arxiv_preprint    —                    no matching title
  ✗ openreview        —                    no matching title
  ✗ core              —                    no match
  ✗ affiliation       —                    no match
  ────────────────────────────────────────────
  💡 Next steps: (a) Set Link to an alternate URL, (b) Upload PDF
     manually, or (c) enable Phase-B curl_cffi in settings — this
     reference is on papers.ssrn.com which needs TLS impersonation.
```

The "Next steps" line is generated deterministically from the failure
pattern (which hosts dominated the failures).

**Per-project (dashboard):** new card "Top blocked hosts":

```
┌─ Top blocked hosts ─────────────────────────────────┐
│  papers.ssrn.com    7 refs     Enable curl_cffi    │
│  econstor.eu        3 refs     Enable curl_cffi    │
│  eur-lex.europa.eu  2 refs     Enable Playwright   │
└─────────────────────────────────────────────────────┘
```

Button links navigate to `/settings` or open the relevant settings
modal pre-focused on the toggle. Backed by the `download_stats` field
(§11.12).

### 12.4 Long-running operations — consistent progress patterns

The app has four long-running operations today, each with its own progress
widget. v6.1 standardizes them under one component so the user gets the
same mental model everywhere:

| Operation | Existing widget | v6.1 improvement |
|---|---|---|
| Bulk lookup (processing view) | Progress bar + current ref | + live tier-trace chip row (§12.1) |
| Rebuild .md (dashboard)       | Progress bar + current ref | + per-ref backend choice visible ("pymupdf_text / 41 pages") |
| Check all claim verdicts      | Progress bar + current ref | + per-ref verdict as it streams in |
| Check all ref-matches         | Progress bar + current ref | + per-ref verdict as it streams in |
| Build validity report         | Button spinner             | Progress bar + "Copying references... 12/37 / Zipping..." |

All five share one Vue-free vanilla-JS component:

```js
// static/js/progress.js (NEW, ~80 LOC)
function createProgressWidget(container, { totalLabel, onCancel }) {
  // Returns {setTotal(n), tick(label), setStatus(msg), done(summary), error(msg)}
}
```

Consistent look: progress bar, current-item label, latest-event log
underneath (last 3 events), cancel button when applicable. Existing
widgets migrate one at a time; no flag day.

### 12.5 Source-origin exposed in the validity report

§6 already mentions `files_origin` in the report; make it concrete:

In each per-citation block's "Downloaded source" line:

```
Source: PDF · via OpenReview · 2026-04-19 03:10
        ↳ https://openreview.net/pdf?id=ABC    (original lookup URL)
        ↳ Local: references/foo2024_pdf.pdf
```

And if the tier isn't `direct`, add a small explainer:

> ℹ Fetched via OpenReview preprint rather than the publisher URL.
> This is the same paper, but preprint version may differ from the
> final published text.

Per-tier explainer dict:
```python
TIER_EXPLAINERS = {
  "wayback":         "historic Web Archive snapshot — may be outdated",
  "arxiv_preprint":  "arXiv preprint — may differ from the final published version",
  "openreview":      "OpenReview accepted submission — may differ from camera-ready",
  "oa_fallbacks":    "alternate open-access mirror listed by Unpaywall/OpenAlex",
  "core":            "institutional-repository copy via CORE aggregator",
  "curl_cffi":       "fetched with browser TLS impersonation (site bot-blocked default fetch)",
  "playwright":      "captured via headless browser (site needs JS rendering)",
  "direct":          None,   # no banner
}
```

### 12.6 Settings UI — toggle Phase B / C with context

Today settings live in `settings.json` only. v6.1 adds a Settings view
(or a settings modal) exposing the download tier toggles with:
- A one-line description per tier
- An "availability" light: `curl_cffi available ✓` / `curl_cffi not installed — run pip install curl_cffi`
- A "recommended" badge next to tiers that would have helped based on
  `download_stats` (§11.12) — i.e. the same data the dashboard banner uses.

Wire the existing `/api/settings` PUT endpoint; no new backend needed.

### 12.7 Summary of new UI files

| File | Action | Notes |
|---|---|---|
| `static/js/progress.js` | NEW | Shared progress-widget component for all 5 long ops (§12.4) |
| `static/js/download_trace.js` | NEW | Renders the live tier-trace chips (§12.1) and the collapsed failure log (§12.3) |
| `static/js/app.js` | MODIFY | `buildResultCard`: render tier badge next to status badge (§12.2). Right-panel PDF/HTML footer: replace `[LOCAL/REMOTE]` with tier+provenance line. Processing view: render tier trace. Dashboard: new "Top blocked hosts" card. |
| `static/css/style.css` | MODIFY | `.tier-badge--*` color swatches, trace-chip styling, blocked-hosts card layout |
| `templates/index.html` | MODIFY | New containers: `#processing-tier-trace`, `#dash-blocked-hosts-card`, settings modal (or view) |
| `validity_report.py` | MODIFY | Render tier + explainer in "Downloaded source" line; collapsed download-log `<details>` (§12.5) |
| `app.py` | MODIFY | Emit `tier_attempt` / `tier_ok` SSE events; `/api/projects/<slug>/settings` returns availability flags; endpoint for top-blocked-hosts aggregation |

### 12.8 Impact on the `download_log` and `files_origin` shapes

Both fields (§11.10, §11.11) must carry enough for the UI:

```jsonc
{
  "files_origin": {
    "pdf": {
      "tier":        "openreview",
      "url":         "https://openreview.net/pdf?id=ABC",
      "captured_at": "2026-04-19T03:10:00+00:00",
      "host":        "openreview.net"
    }
  },
  "download_log": [
    {"tier": "direct",   "url": "https://...", "ok": false, "kind": "http_403",   "elapsed_ms": 812},
    {"tier": "wayback",  "url": null,           "ok": false, "kind": "no_snapshot","elapsed_ms": 1104},
    {"tier": "openreview","url":"https://...", "ok": true,  "kind": null,          "elapsed_ms": 670}
  ]
}
```

`elapsed_ms` per entry lets the dashboard surface "slow tiers" — useful
diagnostic for users weighing Phase C (Playwright at 3–8 s/ref).

### 12.9 Test coverage for UI additions

- `test_sse_emits_tier_events` — orchestrator with 3 tiers mocked; SSE
  stream contains the expected `tier_attempt`/`tier_ok` sequence.
- `test_results_card_renders_tier_badge` — jsdom snapshot on `buildResultCard`.
- `test_validity_report_includes_tier_explainer` — wayback tier triggers
  the "historic snapshot" explainer; `direct` does not.
- `test_blocked_hosts_aggregation` — given `download_stats` with mixed
  hosts, returns top 5 sorted with recommended tier.
- `test_progress_widget_api` — `createProgressWidget` returns an object
  with the documented methods; cancel fires the callback.

---

## 13. Real-world regression test cases (from `projects/`)

Scanning the four live projects (`finai-ch4`, `finai-ch5-1`, `finai-ch6`,
`finai-ch6-new`) surfaces **39 manual-intervention cases** — every place
the author had to Upload PDF or Paste Content because the automatic
pipeline failed. These are the ground-truth regression cases v6.1 must
be measured against.

**Overall counts:** 13 pasted · 26 manual-PDF uploads · 39 total.
Per-project: `finai-ch4` 3, `finai-ch5-1` 13, `finai-ch6` 1,
`finai-ch6-new` 22.

Each case below is listed as `<slug>/<bib_key>` with the lookup-found
URL (if any), the root cause, and the v6.1 tier that should recover it.

### 13.1 Bucket A — arXiv pdf_url found but download failed (5 cases)

**Bug category, not missing-source.** The lookup pipeline identified the
correct arXiv PDF URL, but `_download_pdf` didn't stick the file. Most
likely explanations: redirect chain not fully followed, rate-limiting,
transient 5xx, or a validation mismatch we're not surfacing.

| Case | pdf_url | Expected fix |
|---|---|---|
| `finai-ch5-1/park2023generative` | `arxiv.org/pdf/2304.03442` | A0 — unified fetcher + retry on 5xx (§11.2, §11.6) |
| `finai-ch5-1/yao2022react`       | `arxiv.org/pdf/2210.03629` | same |
| `finai-ch5-1/hou2025model`       | `arxiv.org/pdf/2503.23278` | same |
| `finai-ch5-1/brown2020language`  | `arxiv.org/pdf/2005.14165` | same |
| `finai-ch5-1/wei2022chain`       | `arxiv.org/pdf/2201.11903` | same |

**Success criterion:** all 5 auto-download after A0 ships. If any still
fail, the `download_log` (§11.11) must show *why*.

### 13.2 Bucket B — DOI present, free-text mirror not discovered (11 cases)

DOI → publisher paywall or no OA link in Unpaywall/OpenAlex `best`
location. v6.1 expands the lookup with OA `oa_locations` fallbacks,
CORE, PMC, HAL, RePEc/NBER, OpenReview — most should find a mirror.

| Case | DOI / host | Expected fix |
|---|---|---|
| `finai-ch5-1/shavit2023practices`    | DOI → paywall; OpenAI host the PDF at cdn.openai.com | §3.1 OA fallbacks or §3.11 affiliation search |
| `finai-ch5-1/rizinski2026ai`         | DOI 10.32604/… (Tech Science Press) | §3.7 CORE / §3.15 HAL |
| `finai-ch5-1/baddeley2020working`    | DOI 10.1016/s0079-7421(08)60452-1 (Elsevier old) | §3.1 OA fallbacks / §3.7 CORE |
| `finai-ch5-1/baddeley2025working`    | DOI 10.32388/l39w1f (Qeios) | §3.1 OA fallbacks |
| `finai-ch6-new/cortes1995support`    | DOI 10.1007/bf00994018 (Springer) — on author homepages everywhere | §3.11 affiliation search |
| `finai-ch6-new/breiman2001random`    | DOI 10.1023/a:1010933404324 (v6 motivating case) | §3.1 OA fallbacks / §3.7 CORE |
| `finai-ch6-new/fischer2018deep`      | 10.1016/j.ejor.2017.11.054 (econstor host) | §3.18 Tier 2 `curl_cffi` — **Phase B required** |
| `finai-ch6-new/dixon2020`            | 10.1080/14697688.2020.1828609 (T&F) | §3.1 OA fallbacks / §3.7 CORE |
| `finai-ch6-new/Hansen2005SPA`        | 10.1198/073500105000000063 (ASA)   | §3.1 OA fallbacks / §3.11 affiliation |
| `finai-ch6-new/BudishCramtonShim2015ArmsRace` | 10.1093/qje/qjv027 (OUP QJE) | §3.1 OA / §3.11 affiliation / §3.18 Phase B |
| `finai-ch6-new/HoStoll1981`          | 10.1016/0304-405X(81)90020-9 (Elsevier JFE) | §3.1 OA fallbacks / §3.13 RePEc |
| `finai-ch6-new/GuKellyXiu2020`       | 10.1093/rfs/hhaa009 (OUP RFS) | §3.1 OA / §3.13 RePEc (NBER twin) |
| `finai-ch6-new/GenAIFinanceReplicability2025` | 10.1016/j.frl.2025.108797 (Elsevier FRL) | §3.1 OA fallbacks |

**Success criterion:** ≥ 8 of 13 auto-download after A1 ships.

### 13.3 Bucket C — SSRN / econstor / WAF-blocked (1 case)

| Case | URL | Expected fix |
|---|---|---|
| `finai-ch6-new/BaileyBorweinLopezdePradoZhu2014` | DOI 10.2139/ssrn.2568435 (v6 motivating case) | §3.13 RePEc mirror discovery, else §3.18 Tier 2 `curl_cffi` |

**Success criterion:** A1 (RePEc lookup) finds an NBER/CEPR mirror; if
none, Phase B recovers.

### 13.4 Bucket D — Author-hosted / Wikipedia-linked PDFs (2 cases)

| Case | URL | Expected fix |
|---|---|---|
| `finai-ch5-1/wooldridge1995intelligent` | `cs.cmu.edu/~motionplanning/...pdf` | A0 — direct fetch with connection reuse + 5xx retry |
| `finai-ch5-1/wooldridge2009introduction` | `uranos.ch/research/references/...pdf` + Wikipedia URL | A0 + §3.11 affiliation search as fallback |

**Success criterion:** both auto-download after A0.

### 13.5 Bucket E — Government / regulatory documents (5 cases)

| Case | Source | Expected fix |
|---|---|---|
| `finai-ch6-new/SEC15c3_5Final`      | sec.gov     | Tier 1A (SEC UA rule — **already shipped**) |
| `finai-ch6-new/SECRegNMS`           | sec.gov     | Tier 1A (**already shipped**) |
| `finai-ch6-new/imf2024gfsr`         | imf.org     | §3.10 Wayback (v6 Tier 1B) |
| `finai-ch5-1/act2024eu`             | eur-lex     | §3.19 Tier 3 Playwright (JS interstitial) |
| `finai-ch6-new/MacKenzie2008MaterialMarkets` | OUP book | §3.11 affiliation or remains a book (see §13.7) |

**Success criterion:** 2 SEC cases already auto-download today (would be
a ✓ against Tier 1A if we re-ran them). IMF case auto-downloads after
A1 Wayback ships. EU AI Act needs Phase C.

### 13.6 Bucket F — News / blog articles, no canonical PDF exists (11 cases)

These are *legitimate* uses of Paste Content — there's no better
automated outcome because the source is an article page, not a PDF.
The improvement opportunity is in **HTML capture + readability extraction**
(what the page-download path does today) rather than inventing a PDF.

| Case | Host | Notes |
|---|---|---|
| `finai-ch4/openai_morgan_stanley` | openai.com blog | HTML path should capture this cleanly |
| `finai-ch4/finra_ai_guidance`     | finra.org       | HTML + regulatory text |
| `finai-ch4/jpmorgan_coin2017`     | bloomberg.com   | paywalled news article |
| `finai-ch5-1/klover_hsbc2025`     | business blog   | |
| `finai-ch6/crs2024`               | crsreports.gov  | sometimes a PDF, sometimes HTML |
| `finai-ch6-new/crs2024`           | crsreports.gov  | duplicate of above |
| `finai-ch6-new/TwoSigmaHarvardCase` | hbsp.harvard.edu | **commercial case study — unrecoverable** |
| `finai-ch6-new/nyse1976`          | historic piece  | likely no digital copy |
| `finai-ch6-new/forbes2025` / `forbes2025algo` | forbes.com  | article pages (duplicate cites) |
| `finai-ch6-new/walbi2025`         | blog            | |
| `finai-ch6-new/Finextra_UBSAvatarAnalysts` | finextra.com | news |

**Success criterion:** after A0 improves HTML capture robustness, most
of these end up as `found_web_page` with a `.md` built from the HTML —
no paste required. Commercial case studies (HBS) correctly remain at
Paste Content.

### 13.7 Bucket G — Books (commercial publishers) (2 cases)

| Case | Publisher | Realistic outcome |
|---|---|---|
| `finai-ch6-new/Hasbrouck2007EmpiricalMicrostructure` | OUP book | abstract-only; manual PDF only if the author has a licensed copy |
| `finai-ch6-new/MacKenzie2008MaterialMarkets` | OUP book | same |

**Realistic expectation:** not all manual-upload cases should go away —
books are the legitimate residue. v6.1 success means the paste/upload
UI is unchanged (still available) and the percentage of refs needing it
drops from ~15% (current) to ≤ 5% (books + niche sources only).

### 13.8 Bucket H — Dataset (for the test suite itself)

A new test file `tests/test_v6_1_regression.py` codifies the above as an
integration suite. Structure:

```python
REGRESSION_CASES = [
    {"bucket": "arxiv_failed",
     "project": "finai-ch5-1", "bib_key": "park2023generative",
     "pdf_url": "https://arxiv.org/pdf/2304.03442",
     "expected_tier": "direct", "expected_phase": "A0"},
    ...  # 39 entries
]

@pytest.mark.parametrize("case", REGRESSION_CASES, ids=_case_id)
def test_v6_1_case_resolves(case, requests_mock):
    """Each case is a known-failing reference. After v6.1, the orchestrator
    should either auto-resolve it or land in an acceptable terminal state
    (e.g. Bucket F → found_web_page, Bucket G → abstract-only)."""
    ...
```

Tests use `requests_mock` to avoid hitting live networks; a separate
manual-run suite (`pytest -m live`) hits the real URLs for periodic
validation.

### 13.9 Overall success metric

Drop the manual-intervention count from **39 / 171 refs (~23%)** to
**≤ 10 / 171 (~6%)** across the four projects, after all v6.1 phases
ship. The remaining ~6% are books (Bucket G) and a handful of legitimate
news/blog pastes (Bucket F) where no automation is correct.

Phase-by-phase recovery projection:

| Phase | Cases recovered | Running total |
|---|---|---|
| A0 (refactor)         | Bucket A (5) + Bucket D (2)                                                    | 7 / 39 |
| A1 (new Tier-1 sources) | Bucket B (≥ 8) + Bucket C (0-1 via RePEc) + Bucket E imf2024gfsr (1)        | 16–17 / 39 |
| Phase B (curl_cffi)   | Bucket B fischer2018deep, oup stragglers (≥ 3) + Bucket C BBLP2014 (1)         | 20–21 / 39 |
| Phase C (Playwright)  | Bucket E act2024eu (1) + EUR-Lex/Elsevier stragglers                           | 22–23 / 39 |
| HTML capture polish (A0) | Bucket F → found_web_page for ~7 of 11                                       | 29–30 / 39 |
| Remains manual        | Bucket G books (2) + HBS case (1) + unrecoverable news (~6)                    | 9–10 / 39 |
