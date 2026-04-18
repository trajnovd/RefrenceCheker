# References Checker v5 — Project Dashboard Redesign + Settings Centralization

## Context

After v4, the project dashboard is a four-card menu (Upload .bib / Upload .tex / Rebuild .md / Export) with a stats strip at the bottom. It tells the user *what they can do*, not *what's going on*. After uploads, the most useful information (progress, issues, next step) is the least prominent. The v4 claim-check pipeline has zero presence on the dashboard at all.

**Goal:** redesign the dashboard so a user landing on it instantly sees pipeline status, problems that need attention, and a one-click way to resume their last task. Operations (upload, export, rebuild) move to a secondary row.

---

## 1. Design goals

1. **Status first.** Walking onto the dashboard should answer "where am I in the pipeline?" in one second.
2. **Surface problems, not just counts.** A list of "12 references with no PDF" is more actionable than a stat saying "12".
3. **Resume, don't restart.** Most visits are mid-task — the primary CTA should be "continue where you left off."
4. **Operations are secondary.** Upload / export / rebuild / settings → bottom strip, not headline.
5. **Two-pipeline aware.** Bibliography lookup AND claim-check both deserve presence, with their own progress and issues.

---

## 2. Proposed layout

```
┌────────────────────────────────────────────────────────────────────┬───────────────────┐
│ ← All projects          finai-ch5-1                       [Settings] [⋮]                │
├────────────────────────────────────────────────────────────────────┴───────────────────┤
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │  STATUS — pipeline at a glance                                                   │   │
│  │  ─────────────────────────────────────────────────────────────────────────────   │   │
│  │   References     ●●●●●●●●○○  42 / 47 found   (5 missing)                         │   │
│  │   Reference .md  ●●●●●●●●●●  47 / 47 built                                       │   │
│  │   Citations      ●●●●●●●●●●  47 parsed                                           │   │
│  │   Claim check    ●●●○○○○○○○  12 / 47 checked  (3 issues, 2 unknown)              │   │
│  │                                                                                  │   │
│  │   [▶ Resume Citation Review]   [📋 Open Verification Table]                      │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────┬─────────────────────────────────────────┐    │
│  │  ISSUES — needs attention (5)       │  RECENTLY VERIFIED                       │    │
│  │  ─────────────────────────────────  │  ───────────────────────────────────     │    │
│  │  ✗ smith2020      not supported     │  ✓ jones2019     supported (manual)      │    │
│  │  ⚠ lee2018        partial           │  ✓ wu2021        supported   2h ago      │    │
│  │  ?  wooldridge1995 no .md content    │  ✓ chen2022      supported   2h ago      │    │
│  │  ?  act2024eu      no reference     │                                           │    │
│  │  ✗ bookstaber2007  not supported     │  [show all 12 →]                          │    │
│  │  [open in Review →]                                                                │    │
│  └─────────────────────────────────────┴─────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────┬─────────────────────────────────────────┐    │
│  │  REFERENCE BREAKDOWN                │  CITATION BREAKDOWN                      │    │
│  │  ─────────────────────────────────  │  ───────────────────────────────────     │    │
│  │  Found PDF              28          │  Supported          7 (15%)              │    │
│  │  Abstract only           9          │  Partial            2 (4%)               │    │
│  │  Web page only           5          │  Not supported      1 (2%)               │    │
│  │  Not found               5          │  Unknown            2 (4%)               │    │
│  │                                     │  Manual override    0                    │    │
│  │  [filter by status →]               │  Not yet checked   35 (75%)              │    │
│  │                                     │  [run full check →]                       │    │
│  └─────────────────────────────────────┴─────────────────────────────────────────┘    │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │  ACTIVITY (last 5)                                                               │   │
│  │  ─────────────────────────────────────────────────────────────────────────────   │   │
│  │  2h ago    Manual verdict set on smith2020 → not supported                      │   │
│  │  2h ago    Pasted content for merriam-webster                                   │   │
│  │  3h ago    Uploaded PDF for wooldridge1995intelligent (1.2 MB)                  │   │
│  │  yesterday Citation review opened (.tex: chapter5.tex)                          │   │
│  │  yesterday .bib uploaded (47 references)                                        │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌──── Operations (compact strip) ──────────────────────────────────────────────────┐   │
│  │  [Upload .bib]  [Upload .tex]  [Rebuild .md]  [Download CSV]  [Download PDF]    │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component spec

### 3.1 Status block (hero)

The single most important block on the page. Four pipeline rows, each one line:

| Row | Numerator / denominator | Detail string |
|---|---|---|
| References | results found / parsed_refs total | `42 / 47 found (5 missing)` |
| Reference .md | results with `files.md` / total | `47 / 47 built` |
| Citations | citations parsed (zero or N) | `47 parsed` (no progress bar — it's binary) |
| Claim check | citations with verdict / total | `12 / 47 checked (3 issues, 2 unknown)` |

Progress bar style: 10 dot-cells filled by ratio, monospace. Reuses existing `.progress-bar` look from upload view.

Two CTAs underneath:
- **Resume Citation Review** — opens the v3 review at the last-viewed citation (if known) or citation 0. Disabled if no `.tex` uploaded.
- **Open Verification Table** — opens v4 view 5. Disabled if no citations.

### 3.2 Issues panel

Pulls everything that needs attention from both pipelines into one ranked list. Items in display priority:

1. Citations with `verdict = "not_supported"` (red)
2. Citations with `verdict = "partial"` (yellow)
3. Citations whose reference has **no `.md` content** (gray)
4. Citations whose `bib_key` has **no parsed_ref** (gray)
5. References where lookup returned `not_found` and no manual source has been set

Each row: `[badge] [bib_key] [reason or verdict]`. Click → jumps to that citation in Review (or, for ref-level issues, opens the results view filtered to that ref).

Show top 5; "Open all in Review →" link below.

### 3.3 Recently verified panel

Mirror of Issues. Last 5 citations whose verdict is `supported`, sorted by `verdict.checked_at` desc. Reassuring + confirms manual verdicts landed. Optional — could be dropped in favor of just "[show all checked →]".

### 3.4 Reference breakdown

Current stats strip, elevated. Clicking a row opens the results view filtered by that status (replaces the manual filter dropdown).

### 3.5 Citation breakdown (NEW)

Verdict distribution from `claim_checks`. Counts per category + percent of total. "Not yet checked" reflects citations with no `claim_check_key`. CTA `[run full check →]` triggers the same `POST /check-citations` as the Review view.

### 3.6 Activity log (NEW)

Append-only log of user-driven events stored in `project.json["activity"][]`. Each entry: `{ts, type, message, target?}`. Types we record:

- `bib_uploaded`
- `tex_uploaded`
- `lookup_completed`
- `manual_verdict` (with key + verdict)
- `pasted_content` / `uploaded_pdf` / `set_link`
- `add_reference`
- `claim_check_batch` (start + finish)
- `rebuild_md`

Display: relative time + one-line message. Capped to last 50 entries to keep `project.json` manageable.

### 3.7 Operations strip

Single horizontal row of small outline buttons. Replaces the four big action cards. Items:

- **Upload .bib** (re-upload — replaces existing)
- **Upload .tex** (replaces existing)
- **Rebuild all .md** (already async + SSE)
- **Download CSV** / **Download PDF**
- **Settings ⚙** (top-right, opens dropdown)

### 3.8 Settings dropdown (top-right)

Quick access to the most-changed knobs:
- PDF converter (`pymupdf4llm` / `docling`)
- Claim-check model (`gpt-5-mini` / `gpt-5` / custom string)
- Max batch USD cap

Reads/writes via `GET/PUT /api/settings` (PUT route is new). Backend already has `get_settings()` / `get_settings_path()`.

---

## 4. Backend additions

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/projects/<slug>/dashboard` | One-shot aggregator: status counts, issues list (top N), recent verdicts, activity, breakdowns. Saves the frontend from doing 4+ calls + N×O(citations) loops. |
| `GET` | `/api/projects/<slug>/last-viewed` | Returns `{citation_index}` for "Resume Review" CTA. |
| `POST` | `/api/projects/<slug>/last-viewed` | Updates it (called by review view on navigate). |
| `PUT` | `/api/settings` | Update settings.json (model, converter, cost cap). |

The dashboard endpoint is the load-bearing one. Without it, the frontend would fan out to `/citations-with-verdicts`, `/api/projects/<slug>`, `/tex`, `/api/settings/claim-check` and assemble — slow and chatty.

### Storage additions to `project.json`

```json
{
  ...existing fields,
  "activity": [
    {"ts": "2026-04-16T14:35:12Z", "type": "manual_verdict",
     "message": "Set verdict on smith2020 → not_supported", "target": "smith2020"},
    ...up to 50 entries
  ],
  "last_viewed_citation": 3
}
```

---

## 5. Frontend additions

### Functions
```javascript
function loadDashboard(slug)            // GET /dashboard, caches in module state
function renderStatusBlock(data)        // 4 progress rows + 2 CTAs
function renderIssuesPanel(items)       // top 5 issues, click → review
function renderRecentVerifiedPanel(items)
function renderBreakdowns(refStats, citStats)
function renderActivityLog(entries)
function openSettingsDropdown()
function resumeReview()                  // navigate to last_viewed_citation
```

### State (module-level)
```javascript
let dashboardData = null;   // last fetched dashboard payload
let lastViewedCitation = 0;
```

### Polling
The dashboard re-fetches `/dashboard` every 5 s while a batch SSE is active (so claim-check progress is visible without leaving the page). When SSE closes, polling stops.

---

## 6. Implementation phases

### Phase A — Layout + status block + breakdowns (smallest safe slice)
~1–2 hours. **No new endpoints.** All data already in `project.json`.

1. Restructure `view-dashboard` HTML: status block on top, two-column breakdowns, operations strip at bottom.
2. CSS for new layout (single-column on narrow viewports).
3. Update `showDashboard(proj)` to render the new structure from existing data:
   - References progress: `parsed_refs.length` vs `results.filter(r => r.status !== "not_found").length`
   - .md progress: `results.filter(r => r.files?.md).length` vs total
   - Citations: `citations.length`
   - Claim-check: count `citations.filter(c => c.claim_check_key).length` and bucket verdicts via `claim_checks[c.claim_check_key].verdict`
4. Wire two big CTAs (Resume Review opens citation 0 for now; "Open Verification Table" already wired).
5. Move existing buttons into operations strip.

**Result:** the dashboard already looks redesigned and answers "where am I" without any backend changes. ~80% of the value.

### Phase B — Issues panel + Recently verified
~2–3 hours. **One new endpoint.**

6. Add `GET /api/projects/<slug>/dashboard` aggregator (citations × verdicts joined server-side; ranked issues list).
7. Render Issues + Recently verified panels.
8. Click handlers: jump to review at the right citation index.

### Phase C — Activity log
~3–4 hours. **Schema change + write hooks.**

9. Add `add_activity(slug, type, message, target=None)` to `project_store.py`. Cap at 50.
10. Insert calls in: bib upload, tex upload, set_link, upload-pdf, paste-content, set-verdict, add-reference, check-citations (start + complete), rebuild-md.
11. Render activity log in dashboard. Surface in `/dashboard` aggregator.

### Phase D — Settings dropdown + last-viewed citation
~2 hours.

12. `GET/PUT /api/settings` routes (read/write `settings.json` minus secrets).
13. Settings dropdown UI in top-right.
14. `last_viewed_citation` storage + Resume Review CTA wired.

### Phase E — Polish
- Empty-state designs (no .bib uploaded, no .tex uploaded, no citations, claim-check disabled)
- Mobile/narrow-window stacked layout
- Optional: live polling during batch claim-check
- Optional: drag-drop bib/tex onto operations strip

---

## 7. Files to create / modify

| File | Action | What changes |
|---|---|---|
| `templates/index.html` | MODIFY | Restructure `view-dashboard` section: status, issues, breakdowns, activity, operations |
| `static/css/style.css` | MODIFY | New dashboard grid layout, status-block styles, panel cards, activity entries |
| `static/js/app.js` | MODIFY | Replace `showDashboard()` with new renderers; add `loadDashboard()`, `resumeReview()`, etc. |
| `app.py` | MODIFY | New `/dashboard`, `/last-viewed` (GET+POST), `/api/settings` (PUT) routes |
| `project_store.py` | MODIFY | `add_activity()`, `set_last_viewed_citation()`, `get_dashboard_data()` aggregator |
| `config.py` | MODIFY | `update_settings(partial)` helper for the PUT route |
| `tests/test_dashboard.py` | NEW | Tests for the aggregator + activity log |

---

## 8. Settings centralization (DONE)

All non-secret settings are now in `settings.json`. API keys remain in environment variables only.

### 8.1 `settings.json` schema (full)

```json
{
  "flask_port": 5000,
  "projects_dir": "projects",
  "max_upload_size_mb": 50,
  "session_ttl": 1800,
  "max_workers": 1,
  "unpaywall_email": "dimitar.trajanov@finki.ukim.mk",
  "scholarly_enabled": true,
  "pdf_converter": "pymupdf4llm",
  "claim_check": {
    "enabled": true,
    "openai_model": "gpt-5-mini",
    "max_ref_chars": 100000,
    "max_paragraph_chars": 4000,
    "max_sentence_chars": 1500,
    "max_batch_usd": 5.00,
    "request_timeout_s": 60,
    "max_retries": 3
  }
}
```

### 8.2 Precedence

For each setting: **env var > settings.json > hardcoded default**.

| Setting | settings.json key | Env var override | Notes |
|---|---|---|---|
| Flask port | `flask_port` | `FLASK_PORT` | |
| Projects directory | `projects_dir` | `PROJECTS_DIR` | Relative to repo root unless env overrides with absolute path |
| Max upload size | `max_upload_size_mb` | `MAX_UPLOAD_SIZE_MB` | In megabytes (converted to bytes internally) |
| Session TTL | `session_ttl` | `SESSION_TTL` | Seconds |
| Max workers | `max_workers` | `MAX_WORKERS` | Thread pool size for parallel ref processing |
| Unpaywall email | `unpaywall_email` | `UNPAYWALL_EMAIL` | Required for Unpaywall API |
| Scholarly enabled | `scholarly_enabled` | `SCHOLARLY_ENABLED` | Toggle Google Scholar scraping |
| PDF converter | `pdf_converter` | `PDF_CONVERTER` | `"pymupdf4llm"` or `"docling"` |
| Claim-check model | `claim_check.openai_model` | — | |
| Claim-check max ref chars | `claim_check.max_ref_chars` | — | |
| Claim-check max batch cost | `claim_check.max_batch_usd` | — | |
| Claim-check timeout | `claim_check.request_timeout_s` | — | |
| Claim-check retries | `claim_check.max_retries` | — | |
| Claim-check enabled | `claim_check.enabled` | — | |

### 8.3 API keys (env vars only)

Secrets must NOT appear in `settings.json` (which is a plain-text file in the repo directory). They come exclusively from environment variables:

| Key | Env var | Required? |
|---|---|---|
| Semantic Scholar | `SEMANTIC_SCHOLAR_API_KEY` | Recommended (higher rate limits) |
| Google API | `GOOGLE_API_KEY` | For Google Custom Search fallback |
| Google CSE ID | `GOOGLE_CSE_ID` | For Google Custom Search fallback |
| OpenAlex | `OPENALEX_API_KEY` | Optional |
| OpenAI | `OPENAI_API_KEY` | Required for claim checking |

### 8.4 Startup banner

On every app launch, the console prints a unified banner showing all resolved values and availability checks:

```
============================================================
  References Checker - Configuration
============================================================

  Settings file:           /path/to/settings.json

  --- Server ---
  Flask port:              5000
  Projects dir:            /path/to/projects
  Max upload size:         50 MB
  Session TTL:             1800s
  Max workers:             1

  --- Lookup pipeline ---
  Unpaywall email:         dimitar.trajanov@finki.ukim.mk
  Scholarly (G.Scholar):   enabled
  Semantic Scholar key:    set
  Google API key:          set
  Google CSE ID:           set
  OpenAlex key:            set (optional)

  --- PDF to Markdown ---
  Converter:               pymupdf4llm  (available)

  --- LLM claim check ---
  Enabled:                 True
  OpenAI API key:          set
  openai package:          available
  Model:                   gpt-5-mini
  Max reference chars:     100000
  Max batch cost:          $5.00
  Request timeout:         60s
  Max retries:             3

============================================================
```

This replaces the previous two separate print blocks (API key status + settings status).

### 8.5 Settings dashboard integration (future Phase D)

The dashboard settings dropdown (Section 3.8) reads/writes `settings.json` via `GET/PUT /api/settings`. Since the file schema is now comprehensive, the dropdown can expose all non-secret knobs: converter, model, max batch cost, max workers, etc. API keys show as "set" / "MISSING" (read-only in the UI; set via env vars).

---

## 9. Open questions

1. **Activity log scope:** worth the schema change (Phase C is the most invasive), or is git-style file-timestamp browsing enough? Could be cut.
2. **Recently verified panel:** keep it, or drop and link to "[show all checked →]"? Nice but optional.
3. **Settings dropdown location:** top-right of the dashboard (per-project) or a separate `/settings` page reachable from projects list (global)? Settings.json is global today.
4. **Pipeline row 2 (Reference .md):** is .md status worth a row, or is it implementation detail? Argument for keeping: rebuilding is a recurring action when switching PDF converters.
5. **Default landing for empty project:** show a friendly hero "Upload your .bib to start" instead of all-empty progress bars?
6. **Live polling vs SSE:** dashboard could subscribe to the existing claim-check SSE stream instead of polling. Slight extra wiring; cleaner UX.
