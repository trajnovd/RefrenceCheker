# References Checker v4 — LLM-Based Citation Claim Verification

## Context

After v3, the user can browse each `\cite{}` in their LaTeX side-by-side with the referenced paper's content. v3 helps a human reviewer verify citations manually. v4 adds an automated layer: for every citation, send the **surrounding paragraph** (the claim being made) and the **reference's extracted text** (the `{key}.md` produced in v2.5) to an LLM, and ask whether the reference actually supports the claim.

**Goal:** After uploading a `.tex` file, the user clicks "Check Citations". The system iterates each `\cite{}`, extracts the local paragraph + sentence around it, loads the matched reference's `.md`, calls OpenAI with a structured prompt, and stores a verdict (supported / partial / not-supported / unknown) plus a short explanation and an evidence quote. Verdicts surface as badges in the v3 review view.

**Why .md (not abstract or raw PDF):**
- The `{key}.md` files (built by `_build_reference_md` in v2.5) already contain a clean header + abstract + extracted body — this is the canonical "what the reference says" that the LLM should read.
- One file per reference → one prompt input. No tab-switching logic in the LLM path.

---

## 1. User Flow

There are **two entry points** for running the claim check. Both call the same backend; they differ only in how results are presented.

### 1a. From the Review view (in-context, deep-dive)

```
Citation Review View (v3)
  -> "Check All Citations" button (top of nav bar)
  -> Confirmation modal: shows estimated cost (N citations × ~$0.0X = ~$Y)
  -> User confirms
  -> Background job runs; SSE feed updates progress: "12 / 47 checked"
  -> Each citation in the LaTeX panel gets a small verdict badge
       ✓ supported   ⚠ partial   ✗ not supported   ? unknown
  -> Right panel gains a new "Verdict" section above the tabs:
       - Verdict label + confidence
       - One-line explanation
       - Evidence quote pulled from the reference (collapsible)
       - "Recheck" button to re-run a single citation
```

Per-citation single check is also available (right panel button) without batch run.

### 1b. From the Verification Table view (overview, scannable)

A new top-level view focused on bulk inspection — designed for "I want to see every weak/unsupported citation in my paper at a glance".

```
Project Results View                  Citation Review View (v3)
  -> "Verification Table" button         -> "Open Verification Table" button
              \                           /
               v                         v
        Verification Table View (View 5)

  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │ [Run All] [Stop]   23 / 47 checked  ████████░░░░  est. cost $0.05                       │
  │ Filter: [All ▾] [Supported ▾] [Partial ▾] [Not supported ▾] [Unknown ▾]                 │
  │ Search: [_______________]   [Export CSV]   [Back to Review]                             │
  ├────────────────────────────────────────────────────────────────────────────────────────┤
  │ # │Line│Key       │Claim sentence            │Reference        │Verdict │Evidence       │
  ├────────────────────────────────────────────────────────────────────────────────────────┤
  │ 1 │ 47 │smith2020 │We show SOTA on ImageNet  │Smith 2020       │ ✓ 0.85 │"87.3% top-1..."│
  │ 2 │ 52 │jones2019 │Prior work on transformers│Jones 2019       │ ⚠ 0.55 │"a related..."  │
  │ 3 │ 60 │lee2018   │Lee et al. proved that... │Lee 2018         │ ✗ 0.78 │ —              │
  │ 4 │ 73 │wu2021    │Wu's framework was used   │(no .md content) │ ?      │ —              │
  │ ...                                                                                      │
  └────────────────────────────────────────────────────────────────────────────────────────┘

Row click expands a detail panel:
  - Full paragraph (raw LaTeX, with cite highlighted)
  - LLM explanation (1–2 sentences)
  - Evidence quote (verbatim from reference)
  - [Recheck] [Open in Review] [Open Reference PDF] buttons
```

Behavior:
- **Live updates:** if a batch job started from either view is running, this view's rows light up as verdicts arrive (same SSE feed).
- **No duplication:** "Run All" here triggers the same `POST /check-citations` endpoint as the Review view's button.
- **Stable URL:** `/#/projects/<slug>/verify` so the user can bookmark or refresh during a long run.
- **Sortable columns** (verdict, confidence, line). Default sort: by line number (document order).
- **Default filter on first open after a batch:** show only `partial` + `not_supported` + `unknown` so the user immediately sees what needs attention.

---

## 2. Extraction: paragraph + sentence per citation

Extend `tex_parser.py` with a new function. It augments each citation entry with the local context the LLM actually needs.

```python
def extract_claim_context(tex_content, citation):
    """For one citation entry, return its containing sentence and paragraph (LaTeX-stripped)."""
    # Returns:
    # {
    #   "sentence": "We show that the model achieves SOTA on ImageNet.",
    #   "paragraph": "Recent work has explored ... \cite{smith2020} ... applied to ImageNet.",
    #   "sentence_clean": "...",   # LaTeX commands stripped, comments removed
    #   "paragraph_clean": "...",
    # }
```

**Paragraph boundary rules** (in priority order):
1. Blank line (one or more `\n\s*\n`).
2. Section/subsection commands: `\section{...}`, `\subsection{...}`, `\paragraph{...}`, `\chapter{...}`.
3. Environment delimiters: `\begin{...}` / `\end{...}` (treat as hard breaks for `figure`, `table`, `equation`, `align`, `itemize`, `enumerate`, `abstract`, `quote`).
4. Hard cap: max 4000 characters of context. If the paragraph is longer, truncate to a window centered on the citation.

**Sentence boundary rules:**
- Walk left from the citation position to the previous `.`, `?`, `!` not followed by a digit/lowercase (cheap heuristic to avoid breaking on `e.g.`, `i.e.`, `Fig.`, abbreviations).
- Walk right from the citation's end to the next sentence terminator.
- If sentence ends up empty or > 1500 chars, fall back to a 500-char window centered on the citation.

**LaTeX stripping** (light, not perfect):
- Remove comments: lines starting with `%` (handle escaped `\%`).
- Replace common formatting commands with their content: `\textbf{x}` → `x`, `\emph{x}` → `x`, `\textit{x}` → `x`.
- Replace cite commands with a placeholder: `\cite{smith2020}` → `[CITE:smith2020]` (so the LLM knows where the citation appears in the sentence).
- Replace ref commands with `[REF]`: `\ref{...}`, `\autoref{...}`, `\eqref{...}`.
- Strip remaining unknown `\command{arg}` to `arg` (best-effort).
- Collapse whitespace.

The cleaned versions are what we send to the LLM; the raw versions are kept for UI display.

---

## 3. LLM call

### Prompt design

System prompt (constant):
```
You are an expert academic reviewer. The user will give you:
  1. A claim from a paper (paragraph + the specific sentence containing a citation).
  2. The text of the referenced work.

Decide whether the referenced work supports the claim. Be strict: support means the
reference contains evidence, results, or assertions that directly back the claim.
If the reference is only tangentially related, mark it "partial". If it contradicts or
does not address the claim, mark it "not_supported". If the reference text is too
short or off-topic to judge, mark it "unknown".

Respond with valid JSON only, matching this schema:
{
  "verdict": "supported" | "partial" | "not_supported" | "unknown",
  "confidence": 0.0-1.0,
  "explanation": "<= 2 sentences. Explain the verdict.",
  "evidence_quote": "<= 300 chars verbatim from the reference, or empty string if none."
}
```

User message (per citation):
```
CLAIM PARAGRAPH:
<paragraph_clean — up to 4000 chars>

CLAIM SENTENCE (the cite is marked [CITE:<key>]):
<sentence_clean — up to 1500 chars>

REFERENCED WORK (bib_key=<key>, title="<title>"):
<reference .md content — truncated, see below>
```

### Truncation strategy for the reference

Most papers' extracted .md is much larger than the LLM context budget allows cheaply. Strategy:
- **Always include:** the metadata header + abstract from the .md (the top section, up to first `## Full text`).
- **Body:** truncate to `claim_check_max_ref_chars` (default **100000 chars** ≈ 20k tokens). Take from the start of the body — abstracts + intro + early sections usually contain the claim-relevant content for typical citations.
- Future improvement: cheap retrieval (BM25 or embedding similarity) over body chunks to pick the most relevant slice for each claim. Out of scope for v4.

### Model + parameters

- Default model: `gpt-5-mini` (cheap, fast, good enough for this task).
- Optional: `gpt-5` for higher quality (configurable via settings.json).
- `response_format={"type": "json_object"}` to force JSON.
- `temperature=0.1` (mostly deterministic).
- `max_tokens=400` (response is small).
- Retry on transient errors (rate limit, 5xx) with exponential backoff, max 3 retries.

### Cost guardrails

- Pre-flight estimate shown to user before batch: `N_citations × avg_input_tokens × $/token`.
- Hard limit: refuse to start a batch if estimated cost > `claim_check_max_batch_usd` (default $5.00, configurable).
- Per-call timeout (default 60s).

---

## 4. Caching

Each LLM call is keyed by a hash of the inputs so re-checking is free unless something changed.

```python
def claim_check_cache_key(paragraph_clean, sentence_clean, ref_md_content, model):
    blob = f"{model}|{paragraph_clean}|{sentence_clean}|{ref_md_content}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

Cached results live in `project.json`:
```json
{
  "claim_checks": {
    "<sha256>": {
      "verdict": "supported",
      "confidence": 0.85,
      "explanation": "...",
      "evidence_quote": "...",
      "model": "gpt-5-mini",
      "checked_at": "2026-04-16T12:34:56Z",
      "input_tokens": 5234,
      "output_tokens": 187
    }
  }
}
```

And per-citation references to those keys:
```json
{
  "citations": [
    {
      "bib_key": "smith2020",
      "position": 1423,
      "line": 47,
      "context_before": "...",
      "context_after": "...",
      "claim_check_key": "<sha256>"
    }
  ]
}
```

This separation keeps the citations list small while letting many citations share a cached verdict (e.g. when a paper cites the same `(paragraph, ref)` pair from `\cite{a,b}`).

A "Recheck" action invalidates the existing entry and re-calls.

---

## 5. Settings

Extend `settings.json` with a `claim_check` block. Secrets (API keys) prefer env vars but fall back to settings.json for local convenience.

```json
{
  "pdf_converter": "pymupdf4llm",
  "claim_check": {
    "enabled": true,
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "max_ref_chars": 100000,
    "max_paragraph_chars": 4000,
    "max_sentence_chars": 1500,
    "max_batch_usd": 5.00,
    "request_timeout_s": 60,
    "max_retries": 3
  }
}
```

Precedence: env (`OPENAI_API_KEY`) > settings.json > empty (feature disabled).

`config.py` exports:
- `get_claim_check_settings()` — returns the live block (re-reads file).
- `get_openai_api_key()` — env var first, then settings, then `""`.

If no API key is configured, the "Check Citations" button is hidden in the UI and the API returns a 400 with a clear error.

---

## 6. Backend

### New module: `claim_checker.py`

```python
def extract_claim_context(tex_content, citation):
    """Return {sentence, paragraph, sentence_clean, paragraph_clean} for one citation."""

def load_reference_md(project_dir, bib_key):
    """Read {safe_key}.md from the project directory. Returns full string or None."""

def truncate_reference_md(md_content, max_chars):
    """Keep header + abstract intact; truncate body to fit budget."""

def check_citation(paragraph_clean, sentence_clean, reference_md, *,
                   bib_key, title, model, api_key, settings):
    """Make one OpenAI call. Returns {verdict, confidence, explanation, evidence_quote, ...}."""

def check_all_citations(project_slug, *, on_progress=None):
    """Iterate citations, call check_citation, store results. Used by background job."""
```

The OpenAI call uses the official `openai` Python SDK; add `openai>=1.0` to `requirements.txt`.

### New API routes

| Method | Route | Purpose |
|--------|-------|---------|
| `POST` | `/api/projects/<slug>/check-citations` | Kick off batch claim-check; returns `session_id` |
| `POST` | `/api/projects/<slug>/check-citations/<sid>/stop` | Signal cancel for a running batch (used by Verification Table's Stop button) |
| `POST` | `/api/projects/<slug>/check-citation/<idx>` | Run check for one citation (by index in `citations`); returns the verdict |
| `GET`  | `/api/projects/<slug>/check-status/<session_id>` | SSE feed of per-citation progress + verdicts |
| `GET`  | `/api/projects/<slug>/citations-with-verdicts` | List of citations with their cached verdicts inlined (powers Verification Table on load) |
| `GET`  | `/api/settings/claim-check` | Returns whether the feature is configured (key present, model selected) — no secrets |

**`POST /check-citations`:**
- Body: `{ "model": "gpt-4o-mini" (optional override), "force": false }` — `force=true` ignores cache.
- Computes pre-flight cost estimate; rejects with `409` if over `max_batch_usd`.
- Spawns a background thread (same pattern as bib lookup).
- Returns `{ "session_id": "...", "estimated_cost_usd": 0.12, "n_citations": 47 }`.

**`POST /check-citation/<idx>`:**
- Synchronous; intended for the right-panel "Recheck" button. Single call.

### Storage helpers

Add to `project_store.py`:
- `save_claim_check(slug, cache_key, verdict_dict)` — atomic update of `project.json["claim_checks"][cache_key]`.
- `set_citation_check_key(slug, citation_index, cache_key)` — point a citation at its cached verdict.

---

## 7. Frontend

### 7a. Additions to the v3 Review view (in-context)

The v3 Review view is restructured from 2 panels to **3 panels**:

```
┌────────────────────────────────────────────────────────────────────────────────────┐
│ [< Prev] Cite 3/47 (smith2020) [Next >]  [Check All]  [Open Verify]  [Back]        │
├────────────┬─────────────────────────────────┬────────────────────────────────────┤
│ References │ LaTeX Editor (pristine)         │ ┌────────────────────────────────┐ │
│ (narrow)   │                                 │ │ Verdict header                 │ │
│            │                                 │ │  ✓ Supported  conf 0.85        │ │
│ ┌────────┐ │  ... text text text             │ │  Section 4 reports SOTA...     │ │
│ │smith20 │ │  as shown in \cite{smith2020}   │ │  > "87.3% top-1 on ImageNet"   │ │
│ │Smith,  │ │  ^^^^^^^^^^^^^^^^^^^^^^^^^^^    │ │  [Recheck]                     │ │
│ │et al.  │ │  more text continues here       │ ├────────────────────────────────┤ │
│ │ ✓✓⚠   3│ │  with additional context        │ │ [PDF] [HTML] [Abstract]        │ │
│ └────────┘ │                                 │ │ ┌────────────────────────────┐ │ │
│ ┌────────┐ │  another paragraph with         │ │ │                            │ │ │
│ │jones19 │ │  \cite{jones2019} here          │ │ │   PDF viewer (iframe)      │ │ │
│ │Jones et│ │                                 │ │ │   for the currently-       │ │ │
│ │al.     │ │                                 │ │ │   selected reference       │ │ │
│ │ ⚠⚠⚠⚠  4│ │                                 │ │ │                            │ │ │
│ └────────┘ │                                 │ │ │                            │ │ │
│ ┌────────┐ │                                 │ │ │                            │ │ │
│ │lee2018 │ │                                 │ │ │                            │ │ │
│ │Lee Y.  │ │                                 │ │ │                            │ │ │
│ │ ✗     1│ │                                 │ │ │                            │ │ │
│ └────────┘ │                                 │ │ └────────────────────────────┘ │ │
│ ...        │                                 │ └────────────────────────────────┘ │
└────────────┴─────────────────────────────────┴────────────────────────────────────┘
   ~220px              flexible                              ~50% of remaining
```

**Top nav bar:**
- `[< Prev]` / `[Next >]` — navigate between citation occurrences in document order.
- Citation counter: `"Cite 3 / 47 (smith2020)"`.
- `[Check All Citations]` — disabled if no API key configured (tooltip: "Configure OpenAI API key in settings.json").
- Status pill: `"23 / 47 checked"` while a batch is running; turns into `"All checked"` when done. Progress updates live as checks complete.
- `[Open Verification Table]` — switches to View 5.
- `[Back to Results]`.

**Left panel — References list (NEW, narrow ~220px):**

A vertically scrollable list of compact reference cards, one per unique `bib_key` that appears in the LaTeX. Sourced from `citations` (deduped by `bib_key`) joined with project `results` for titles and verdicts.

Card content (compact, ~3 lines):
```
┌──────────────────────────────┐
│ smith2020              [→]   │   ← bib_key (bold) + open-PDF link icon
│ Smith, Jones (2020)          │   ← formatted citation (authors + year), truncated
│ Deep learning for vision...  │   ← title (single line, truncated, full title in tooltip)
│ ✓✓⚠   3 cites                │   ← verdict bar + occurrence count
└──────────────────────────────┘
```

**Verdict bar visualization** (the "how good is this reference" indicator):
- One small colored segment per citation occurrence of this reference, in document order.
- Colors match the inline badge palette: green (✓ supported), yellow (⚠ partial), red (✗ not_supported), gray (? unknown), light-gray outline (not yet checked).
- Aggregate count shown to the right (`"3 cites"`).
- Hover the bar → tooltip lists each cite: `"Line 47: ✓"`, `"Line 92: ⚠"`, `"Line 130: ✓"`.
- Visual examples: `✓✓✓` all-green = solid reference. `⚠⚠⚠⚠` all-yellow = consistently weak. Mixed `✓✗⚠?` = inconsistent (worth investigating).

**Card states:**
- **Default:** subtle border.
- **Hover:** highlighted background.
- **Selected** (= the bib_key of the currently displayed citation): bright accent border + background tint. Auto-scrolls into view when the user navigates Prev/Next.
- **Click on a card:** jumps to the **first citation occurrence** of that reference (updates the LaTeX panel highlight + the right-panel PDF). Subsequent Prev/Next continues globally; a small `[next cite of this ref →]` chevron on the selected card jumps to the next occurrence of the same reference.

**Sort/filter (top of left panel):**
- Sort: by document order (default), by worst-verdict first, by `bib_key` alphabetical.
- Filter chips: `[All]` `[Has issues]` (= any partial/not_supported/unknown). Default `[All]`.
- Tiny search box (filter by `bib_key` or title text).

**Middle panel — LaTeX Editor (was the v3 left panel):**
- Same as v3: line-numbered LaTeX source with the current `\cite{}` highlighted.
- **The editor content stays pristine** — no inline badges, decorations, widgets, or any other characters injected into the text. The user must be able to save the editor's content back to a `.tex` file and paste/open it in Overleaf without any cleanup.
- The only visual aid in the editor is the existing v3 highlight on the *current* cite (a `<mark>` background, not a character insertion) and standard LaTeX syntax highlighting from CodeMirror.
- Auto-scrolls so the current cite is centered when navigating.
- All verdict information lives **outside** the editor: in the References panel (left) and the Verdict header (right). The editor is text-only.

**Right panel — Reference content (was the v3 right panel) with NEW Verdict header on top:**

The right panel is split vertically into two zones. The **upper zone** is the verdict header for the current citation; the **lower zone** is the existing tab viewer (PDF / HTML / Abstract).

Verdict header (upper zone, compact ~120px tall when populated):
```
┌────────────────────────────────────────────────┐
│ ✓ Supported     confidence 0.85    [Recheck]   │
│ ─────────────────────────────────────────────  │
│ The reference's section 4 reports SOTA results │
│ on ImageNet matching the claim.                │
│                                                │
│ Evidence (from reference):                     │
│ > "Our model achieves 87.3% top-1 on ImageNet, │
│ >  outperforming all prior methods..."         │
└────────────────────────────────────────────────┘
```

The verdict shown corresponds to the **currently navigated citation** (not the reference card click — clicks just navigate to a citation, which updates everything). Verdict updates live as SSE events arrive during a batch run.

If the citation has not been checked yet, the header shows a single `[Check this citation]` button instead.

Reference content (lower zone, fills remaining height): same as v3 — `[PDF]` `[HTML]` `[Abstract]` tabs + viewer area. Auto-selects the best available tab per navigation.

### 7b. New Verification Table view (View 5, overview)

A standalone `id="view-verify"` section in `index.html`, reachable from both the Results view and the Review view.

**Layout:** full-width single-column page. Sticky toolbar on top, scrollable table below, expandable detail panel inline per row.

**Toolbar (sticky top):**
- `[Run All]` — same `POST /check-citations` call. Disabled if no API key.
- `[Stop]` — visible only while running; calls a new `POST /check-citations/<sid>/stop` route to signal cancellation.
- Progress: `"23 / 47 checked"` + thin progress bar + estimated remaining cost.
- Filter chips: `[All]` `[Supported]` `[Partial]` `[Not supported]` `[Unknown]` `[Not yet checked]` (multi-select; default after a batch = Partial + Not supported + Unknown).
- Search box: filters by `bib_key`, sentence text, or reference title.
- `[Export CSV]` — downloads `verification.csv` with one row per citation (columns described below).
- `[Back to Review]` — returns to View 4 at the same citation index the user last viewed.

**Table columns:**

| # | Col | Source | Notes |
|---|-----|--------|-------|
| 1 | `#` | citation index | sortable; default sort |
| 2 | `Line` | `citation.line` | sortable |
| 3 | `Key` | `citation.bib_key` | clickable → opens that ref in Review view |
| 4 | `Claim sentence` | `extract_claim_context().sentence_clean`, truncated to ~120 chars with ellipsis | tooltip on hover shows the full sentence |
| 5 | `Reference` | result title (or `bib_key` if no result) | shows `(no .md content)` italic if no `.md` exists |
| 6 | `Verdict` | `verdict_object.verdict` | colored badge identical to inline badges; `—` if not yet checked |
| 7 | `Confidence` | `verdict_object.confidence` | numeric; sortable |
| 8 | `Evidence` | `verdict_object.evidence_quote`, truncated to ~80 chars with ellipsis | shown in italic quotes; `—` if empty or not yet checked; tooltip on hover shows the full quote; click expands the row |
| 9 | (actions) | — | `[▾]` expand row, `[↻]` recheck, `[→]` open in Review |

**Expanded row (detail panel):**
- Full paragraph (raw, with the citation marked `[CITE:key]` highlighted).
- LLM explanation (full text).
- Evidence quote (full text, in a quoted block).
- `Model: gpt-5-mini  ·  Tokens: 5234 in / 187 out  ·  Checked: 2026-04-16 12:34 UTC`.
- Buttons: `[Recheck]` `[Open in Review]` `[Open reference PDF]` (if exists).

**Empty/loading states:**
- Before any check has run: row shows `verdict = "—"` and the actions only show `[Check]` (per-row single check) and `[→ Open in Review]`.
- During a batch run: rows update in place via SSE; a tiny spinner replaces the verdict cell on the row currently being checked.
- If no `.md` content: row pre-marked `?` with reason; `[Run All]` skips it (no LLM call) so the user isn't charged.

### JavaScript additions to `app.js`

**New state:**
```javascript
let claimChecks = {};         // { citation_index: verdict_object }
let checkSessionId = null;    // SSE session id for batch run (shared across views)
let verifyFilters = {...};    // active filter chips for View 5
let verifySort = {...};       // current sort column + direction for View 5
let verifyExpanded = new Set(); // expanded row indices
```

**New functions:**
```javascript
// Shared (both views)
function checkAllCitations()           // POST /check-citations, opens SSE
function checkSingleCitation(idx)      // POST /check-citation/<idx>
function streamCheckProgress(sid)      // EventSource — broadcasts to whichever views are mounted
function showCostEstimateModal(estimate, n, onConfirm)

// Review view (7a)
function renderVerdictPanel(idx)
function renderCitationBadge(idx)

// Verification Table view (7b)
function openVerifyView()              // GET /citations-with-verdicts → render table
function renderVerifyTable()           // re-render rows applying filters + sort
function toggleVerifyRow(idx)          // expand/collapse detail panel
function applyVerifyFilters()          // re-render on chip/search change
function exportVerifyCsv()             // build CSV client-side from current state
function stopBatch()                   // POST /check-citations/<sid>/stop
```

The SSE handler is **shared**: when a batch is running, it dispatches each verdict update to (a) update `claimChecks`, (b) call `renderCitationBadge` if the Review view is mounted, (c) update the affected row in the Verification Table if that view is mounted. Whichever view the user is on stays in sync.

---

## 8. Implementation Order

### Phase 1 — Extraction + LLM core
1. Add `extract_claim_context()` to `tex_parser.py` (paragraph + sentence + LaTeX-stripping).
2. Create `claim_checker.py` with `check_citation()` and OpenAI client.
3. Add `claim_check` block to `settings.json` defaults in `config.py`; add helpers `get_claim_check_settings()`, `get_openai_api_key()`.
4. Add `openai>=1.0` to `requirements.txt`.
5. Unit-test `check_citation()` against a few hand-crafted (paragraph, ref) pairs.

### Phase 2 — Storage + single-citation API
6. Add `save_claim_check`, `set_citation_check_key` helpers to `project_store.py`.
7. Implement `POST /check-citation/<idx>` (synchronous).
8. Implement `GET /citations-with-verdicts`.
9. Implement `GET /api/settings/claim-check`.

### Phase 3 — Batch + SSE + cancellation
10. Implement `check_all_citations()` orchestrator with progress callback + cancellation flag.
11. Implement `POST /check-citations`, `POST /check-citations/<sid>/stop`, `GET /check-status/<sid>` (SSE, mirrors bib-lookup pattern).
12. Pre-flight cost estimate + `max_batch_usd` guardrail.

### Phase 4 — UI in Review view (in-context)
13. Add `[Check All Citations]` button + status pill to v3 Review nav bar.
14. Add inline badges to the highlighted-cite rendering in the left panel.
15. Add Verdict section to the right panel.
16. Wire SSE → live badge updates.
17. Cost estimate modal + recheck button.

### Phase 5 — Verification Table view (overview)
18. Add `id="view-verify"` to `index.html`: sticky toolbar + table skeleton + expandable row template.
19. Implement `openVerifyView()` + `renderVerifyTable()` reading from `claimChecks` state.
20. Filter chips (default partial+not_supported+unknown after batch), search box, sortable columns.
21. Wire shared SSE handler: row updates light up live during a batch.
22. `[Stop]` button → cancellation route.
23. `[Export CSV]` (client-side build).
24. Add `[Verification Table]` entry buttons in Results view and Review view.

### Phase 6 — Polish
25. Caching invalidation on `force=true`.
26. Surface `unmatched` keys (cite has no .md to check against) with status `unknown` + reason "no reference content".
27. Per-row inline `[Check]` button in the Verification Table for ad-hoc single checks (no need to switch views).

---

## 9. Files to Create/Modify

| File | Action | What changes |
|------|--------|-------------|
| `claim_checker.py` | NEW | OpenAI call, prompt assembly, batch orchestrator, cancellation flag |
| `tex_parser.py` | MODIFY | Add `extract_claim_context()` + LaTeX-stripping helpers |
| `config.py` | MODIFY | New settings block + `get_claim_check_settings()`, `get_openai_api_key()` |
| `settings.json` | MODIFY | Default `claim_check` block (auto-written on first run) |
| `project_store.py` | MODIFY | `save_claim_check`, `set_citation_check_key` helpers |
| `app.py` | MODIFY | 6 new routes (single check, batch start, batch stop, SSE status, citations-with-verdicts, settings probe) |
| `templates/index.html` | MODIFY | Verdict section in Review view (7a) + new `view-verify` Verification Table view (7b) |
| `static/js/app.js` | MODIFY | Shared check flows, shared SSE handler, Review-view badges, Verification Table render/filter/sort/export, cost modal |
| `static/css/style.css` | MODIFY | Badge colors, verdict panel layout, Verification Table layout (sticky toolbar, expandable rows, filter chips) |
| `requirements.txt` | MODIFY | Add `openai>=1.0` |

---

## 10. Edge cases & open questions

**Edge cases handled:**
- Citation key not in project results → verdict `unknown`, reason `"no reference matched in project"`. No LLM call.
- Reference matched but no `.md` file (e.g. only metadata, no PDF/HTML found) → verdict `unknown`, reason `"no extracted reference content"`. No LLM call.
- Multi-key cite `\cite{a,b}` → run one check per key (each gets its own verdict). Same paragraph used for both.
- Citation inside a figure caption / footnote → treated as its own paragraph (boundary at `\caption{...}` / `\footnote{...}`).
- Very long paragraph (> max_paragraph_chars) → window centered on the citation.
- OpenAI API down or returns malformed JSON → verdict `unknown`, store error message; do **not** cache the failure (so retry is cheap).

**Open questions to settle before building:**
1. **Multi-cite behavior:** check each key separately (chosen), or send all referenced .md's in one call? (Separate is simpler and lets the user see per-key verdicts.)
2. **UI: surface verdicts in the bib-results view too** (not just review view), so a quick "are my citations okay?" overview is possible? Probably yes, but post-MVP.
3. **Embeddings retrieval over the .md** (RAG instead of head-truncation) — better quality on long papers, but adds an embedding step + storage. Defer to v5.
4. **Per-paragraph batching:** if 5 different `\cite{}` are in the same paragraph for the same reference, we currently make 5 calls. Could de-dup by `(paragraph_hash, ref_hash)` — already covered by the cache key, so effectively free after the first call. Good.
5. **Should "partial" be configurable** (some users may want strict supported/not-supported only)? Defer; default 4-way verdict is fine.

---

## 11. Cost reference (back-of-envelope)

For `gpt-5-mini` (Apr 2026 pricing snapshot — verify before shipping):
- Input: ~$0.15 / 1M tokens.
- Output: ~$0.60 / 1M tokens.

Per citation, typical:
- Input: ~6k tokens (paragraph + truncated reference) → ~$0.0009.
- Output: ~200 tokens → ~$0.0001.
- **~$0.001 per citation.**

A 100-citation paper: ~$0.10. A 500-citation review article: ~$0.50. The `max_batch_usd` guardrail mostly catches misconfiguration (e.g. running on a 5000-citation corpus), not normal use.
