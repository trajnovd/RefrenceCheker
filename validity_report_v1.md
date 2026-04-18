# Citation Validity Report — v1 specification

**Purpose:** an actionable export the author opens, scans top-to-bottom, and uses
to fix the problematic citations in their `.tex` source. The report is written
*for one human, doing one task* — not for archival, not for sharing.

---

## 1. Format

**HTML** — single self-contained file, printable to PDF via the browser.

Rationale:
- Renders fast in any browser; no extra tooling needed
- Color-coded badges (red / amber / green) survive the print
- Click-through anchors: jump from the summary list to a specific citation block
- Author can copy the offending paragraph straight out of the report
- Local file links (PDF / HTML / Markdown of each downloaded reference) open
  in a new tab so the author can verify the source while reading the report

**Not in v1:**
- JSON sidecar — deferred, not needed for the author-facing workflow.
- Native PDF via `fpdf` — current exporter is fixed-width and doesn't surface
  the new diagnostic fields well. "Save as PDF from browser" gets us there.
- CSV — flattens the per-citation context (paragraph + two LLM verdicts) into
  unreadable rows. Existing CSV export stays for spreadsheet use.

---

## 2. File location and delivery

**Folder layout (self-contained — can be zipped and shared):**

```
projects/<slug>/validity-report/
├── <slug>_report.html              ← the report
├── references.zip                  ← zip of the references folder (downloadable)
└── references/                     ← copies of the cited source files
    ├── findpo2024_pdf.pdf
    ├── findpo2024.md
    ├── findpo2024_page.html
    ├── hochreiter1997long_pdf.pdf
    ├── hochreiter1997long.md
    ├── ...
    └── (one set of files per cited reference)
```

The `references/` subfolder is **created and populated on every report
generation** — files are *copied* (not symlinked) from the project dir so
the validity-report folder is self-contained. Immediately after copying,
the same files are zipped into `references.zip` (sitting next to the HTML
report) so the author can download the bundle in one click and run the
report on their laptop.

Both the report HTML, the `references/` subfolder, and `references.zip`
are wiped and rebuilt on each generation — only the most recent report
is kept.

**Which files get copied / zipped:** every artifact in `result.files` (PDF,
HTML page, MD, pasted, abstract) for every reference that appears in the
**Problematic** or **Partial** sections. Clean citations don't need their
files copied because the report doesn't link to them (one-liner format).
This keeps both the disk footprint and the zip download small even for
projects with hundreds of references.

**Zip layout:** `references.zip` contains a top-level `references/`
directory so extracting it next to the downloaded HTML produces the
exact same folder structure as on the server — i.e. relative links
`references/<file>` in the HTML keep working without any further setup.

```
references.zip
└── references/
    ├── findpo2024_pdf.pdf
    ├── findpo2024.md
    ├── ...
```

**Run-on-laptop workflow (the primary motivation for the zip):**
1. Click **Download report (HTML)** in the dashboard → saves `<slug>_report.html`
2. Click **Download references (ZIP)** in the dashboard, or the prominent
   "Download references bundle" link inside the report itself → saves
   `references.zip`
3. Place both files in the same folder on the laptop, extract the zip
4. Open the HTML — every `references/<file>` link resolves locally,
   no Flask required

**Delivery to the user:**
1. **Saved to disk** in `projects/<slug>/validity-report/` on every
   generation, so the report travels with the project directory and is
   git-able (the on-disk references/ subfolder is the canonical view).
2. **Downloaded by browser:** two separate downloads —
   - `<slug>_report.html` — the standalone report HTML
   - `references.zip` — the source-file bundle
   Each is a one-click download with `Content-Disposition: attachment`.

**Source-file linking:** every "Downloaded source" line in the report is a
clickable `<a target="_blank" rel="noopener">` that opens the local artifact
in a new browser tab. Links use **relative paths into the sibling subfolder**:
`references/<safe_key>_pdf.pdf`. The links resolve correctly whether:
- the report is opened locally on the laptop (after extracting `references.zip`
  into the same folder as the HTML)
- the report is opened locally directly from the on-disk
  `projects/<slug>/validity-report/` folder (where the `references/` folder
  already exists, no zip-extract needed)
- or served by Flask via the static route over the validity-report dir

The Flask side adds one static-style route serving the entire
validity-report directory tree:

```
/projects/<slug>/validity-report/<path:filename>
  → send_from_directory(projects/<slug>/validity-report/, filename)
```

So `/projects/<slug>/validity-report/<slug>_report.html` serves the report
and the report's `<a href="references/foo_pdf.pdf">` links resolve to
`/projects/<slug>/validity-report/references/foo_pdf.pdf`.

---

## 3. Top-level structure

```
┌─────────────────────────────────────────────────────────────────┐
│  HEADER                                                         │
│  Project name · .bib filename · .tex filename · date generated  │
│  Models used: ref-match=gpt-5-mini · claim-check=gpt-5-mini     │
├─────────────────────────────────────────────────────────────────┤
│  SUMMARY (small block — 6 lines max)                            │
│  • References:  N total · X with PDF · Y broken · Z not found   │
│  • Identity:    A matched · B NOT matched · C unverifiable      │
│  • Claims:      D supported · E partial · F not supported       │
│  • Issues to fix:  ⚠ K citations need attention                  │
├─────────────────────────────────────────────────────────────────┤
│  PROBLEMATIC CITATIONS  ← bulk of the document                  │
│  Sorted by severity (worst first), then by .tex line number.    │
│  One block per citation OCCURRENCE — if \cite{x} appears 5×,    │
│  there are 5 separate blocks (each is a fix-action).            │
├─────────────────────────────────────────────────────────────────┤
│  PARTIAL CITATIONS  ⚠ (separate yellow-tagged section)          │
│  Same per-occurrence block format as Problematic.               │
│  Author should re-read each and decide: strengthen or remove.   │
├─────────────────────────────────────────────────────────────────┤
│  CLEAN CITATIONS — collapsed list                               │
│  One-line each: [L42] cite_key — supported, identity ✓          │
│  (Author skips this. Just for completeness.)                    │
├─────────────────────────────────────────────────────────────────┤
│  METHODOLOGY (footer)                                           │
│  Models, settings, when each check last ran, known limits       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Severity buckets (sort order, top of "Problematic Citations")

A citation is **problematic** if any of these is true. The report groups them
in this order — worst first, so the author hits the showstoppers immediately:

1. **🚫 Bib parse error / insufficient data** — bib entry malformed, or has
   no title/DOI. The citation can't be looked up at all.
2. **🚫 Citation key not in .bib** — the `\cite{x}` references a key that
   doesn't exist in the bibliography.
3. **🚫 Broken bib URL** — `status: bib_url_unreachable` (HTTP 4xx/5xx/network).
4. **❌ Identity NOT matched** — `ref_match.verdict in {not_matched,
   manual_not_matched}`. The downloaded text doesn't match the bib's
   title/authors. Likely wrong arXiv ID, wrong DOI, or hallucinated reference.
5. **❌ Claim NOT supported** — `claim_check.verdict == not_supported`.
   The reference doesn't back the claim made in the paper.
6. **⚠️ No `.md` content** — `files.md` missing. Identity + claim checks
   couldn't run. Author needs to provide a source (Set Link / Upload PDF /
   Paste Content).
7. **❓ Identity unverifiable** — `ref_match.verdict in {unverifiable}` AND
   `.md` exists. The LLM couldn't decide. Author should glance and confirm.

`partial` claims (`claim_check.verdict == partial`) get their own section
(see §3 top-level structure) — yellow ⚠️ — between Problematic and Clean,
because they need attention but are not showstoppers.

Within each bucket, sort by **`.tex` line number ascending** so the author
can work through the document linearly.

---

## 5. Per-citation block (the heart of the report)

Each problematic (or partial) citation gets a self-contained block. The
author should be able to read one block, jump to the line in their editor,
and fix it. **One block per citation occurrence** — a `\cite{x}` cited 5×
gets 5 blocks, each with its own paragraph and claim verdict.

```
─────────────────────────────────────────────────────────────────
[Severity badge]  L142  \cite{findpo2024}                #1 of 12
─────────────────────────────────────────────────────────────────

CITATION CONTEXT  (paragraph from main.tex around line 142)
┌───────────────────────────────────────────────────────────────┐
│  ...recent advances in financial language modeling. Zhang and │
│  Chen (2024) propose a preference-aligned finance LLM using   │
│  Direct Preference Optimization (DPO) to improve sentiment    │
│  classification and then maps sentiment outputs into          │
│  portfolio ranking signals via a "logit-to-score" method      │
│  [CITE:findpo2024]. Liu and Miller (2024) propose a hybrid... │
└───────────────────────────────────────────────────────────────┘

BIB ENTRY  (verbatim from .bib)
┌───────────────────────────────────────────────────────────────┐
│  @article{findpo2024,                                         │
│    title  = {FinDPO: Aligning Financial LLMs with Direct      │
│              Preference Optimization},                        │
│    author = {Zhang, Y. and Chen, X.},                         │
│    year   = {2025},                                           │
│    note   = {arXiv:2507.18417},                               │
│  }                                                            │
└───────────────────────────────────────────────────────────────┘

DOWNLOADED SOURCE
  Type:   PDF  ·  https://arxiv.org/pdf/2507.06345
  Local:  → references/findpo2024_pdf.pdf (917 KB)    ← clickable, opens new tab
          → references/findpo2024.md (49 KB)          ← extracted markdown
          → references/findpo2024_page.html (47 KB)   ← saved HTML rendition
  Sources used:  arxiv

❌ IDENTITY CHECK   (model: gpt-5-mini · 2026-04-18 03:09)
  Verdict:        NOT MATCHED  (manually flagged — sticky)
  Title found:    ✓     Authors found:    ✗
  LLM evidence:
    "The excerpt's title and content reference FinDPO and preference
    optimization of LLMs, matching the claimed title. However, the
    authors in the excerpt (Iacovides, Zhou, Mandic) do not match
    the claimed authors (Zhang, Y. and Chen, X.)."
  Excerpt sent to LLM (first 6,000 chars of the .md body):
  ┌───────────────────────────────────────────────────────────────┐
  │  FinDPO: Financial Sentiment Analysis for Algorithmic Trading │
  │  through Preference Optimization of LLMs                      │
  │                                                               │
  │  Giorgos Iacovides¹  Wuyang Zhou¹  Danilo Mandic¹             │
  │  ¹Imperial College London                                     │
  │  Abstract — We introduce FinDPO, a financial sentiment ...    │
  │  [scrollable / collapsible <details> in the HTML]             │
  └───────────────────────────────────────────────────────────────┘

❌ CLAIM CHECK   (model: gpt-5-mini · 2026-04-17 23:59)
  Verdict:        NOT_SUPPORTED   (confidence: 0.82)
  Claim sentence:
    "Zhang and Chen (2024) propose a preference-aligned finance
    LLM using Direct Preference Optimization (DPO) to improve
    sentiment classification..."
  Explanation:
    "The downloaded paper (Iacovides et al. 2025) is about FinDPO
    but the claim attributes specific results to Zhang and Chen
    that do not appear in the actual referenced work."
  Evidence quote from paper (≤300 chars):
    "We introduce FinDPO, a financial sentiment analysis framework
    based on direct preference optimization..."

➤ SUGGESTED FIX
  The bib's note arXiv ID points to a different paper than the
  claimed authors. Either (a) correct the arXiv ID in the bib, or
  (b) if no Zhang & Chen FinDPO paper exists, remove this citation.
```

### Field-level rules

- **Severity badge**: emoji + colored label matching the bucket
  (🚫 / ❌ / ⚠️ / ❓).
- **L142**: line number in the *current* `.tex` file. Clickable in HTML —
  copies `main.tex:142` to the clipboard so the author can paste into their
  editor's "go to line" prompt. If multiple `.tex` files, prefix with filename.
- **#1 of 12**: position in the problematic-citations list, for navigation.
- **Citation context**: ±N words around the cite (settings, default ~600 chars).
  Render the `[CITE:key]` marker exactly where the cite appears so the author
  can locate it quickly inside the paragraph.
- **Bib entry**: rendered from `result.raw_bib`. Monospace.
- **Downloaded source**: type + remote URL + clickable local-file links.
  Each local file (PDF, HTML, MD, pasted, abstract — whatever exists in
  `result.files`) is *copied* into `references/` and rendered as a
  `<a target="_blank" href="references/<filename>">` opening in a new tab.
  If `bib_url_failure` is set, show the HTTP code instead of the URL.
- **Identity check**: omit the whole block if `ref_match` is missing AND no
  identity issue applies. When shown, lead with the verdict, then the LLM's
  reasoning, then the title/authors-found booleans, then the LLM excerpt.
- **LLM excerpt block**: the same first ~6,000 chars of the `.md` body that
  was sent to the identity-check LLM. Wrapped in a collapsed `<details>` so
  the report stays scannable; the author expands when they want to audit
  the LLM's reasoning. ~200 chars added per problematic citation when
  collapsed (just the `<summary>` text).
- **Claim check**: one block per occurrence of this `\cite{key}` in the .tex.
  If the same key is cited 3× in the document, show 3 claim blocks.
- **Suggested fix**: short heuristic, derived deterministically from the
  severity bucket — not LLM-generated. Keeps the report reproducible across
  runs and avoids extra API cost.

---

## 6. Summary block (top of report)

Compact and skim-friendly. **Don't** repeat the full dashboard from the app —
just the numbers the author needs to gauge the scope of work.

```
SUMMARY
  37 references · 64 citations · checked 2026-04-18

  References by source:    PDF 28 · HTML 4 · Abstract 2 · None 3
  Identity verification:   ✓ matched 25 · ✗ NOT matched 4 · ? unverifiable 8
  Claim support:           ✓ supported 51 · ⚠ partial 6 · ✗ not supported 3 · ? unknown 4
                                              ─────
  ⚠  CITATIONS NEEDING ATTENTION:  17           jump to first problematic →

  📦 Download references bundle (references.zip — 42 MB)   ← so the report
                                                              can be opened
                                                              on a laptop
                                                              with all linked
                                                              files included
```

The "jump to first problematic" link goes to the first per-citation block.
The "Download references bundle" link points to `references.zip` next to
the report (relative href: `references.zip`); extracting it into the same
folder as the report HTML on the laptop makes every per-citation source
link clickable offline.

---

## 7. Partial section (yellow ⚠️)

For `claim_check.verdict == partial` only. Same per-occurrence block format
as the Problematic section, but headed with a yellow banner explaining the
distinction:

> **Partial citations:** the LLM judged the reference as tangentially related
> to the claim, not directly supporting nor contradicting it. Author should
> re-read each and decide whether to **strengthen** the claim wording or
> **remove** the cite.

Default state: **expanded** (it's part of the actionable workflow).

---

## 8. Clean citations (collapsed)

For citations where everything passes (`claim_check.verdict == supported`
AND `ref_match.verdict in {matched, manual_matched}` AND no source issues),
one-liner:

```
✓ supported · identity ✓
  L42  \cite{hochreiter1997long}  — Long short-term memory  (Hochreiter & Schmidhuber 1997)
✓ supported · identity ✓
  L48  \cite{bahdanau2014neural}  — Neural machine translation by jointly...
…
```

Default state: **collapsed** (`<details>` element). Author opens only if
they want to spot-check.

---

## 9. Methodology footer

Small text at the bottom — needed for reproducibility / due diligence:

```
METHODOLOGY
  Lookup pipeline:    CrossRef → Unpaywall → OpenAlex → Semantic Scholar
                      → arXiv → Google Search → Google Scholar
  Identity model:     gpt-5-mini   (excerpt = first 6,000 chars of body text)
  Claim model:        gpt-5-mini   (max 100,000 chars of reference)
  Identity checked:   2026-04-18 (37/37 references)
  Claims checked:     2026-04-18 (64/64 citations)

  KNOWN LIMITATIONS
  • LLM-based checks are heuristic — false positives and false negatives occur.
  • A "matched" identity does not certify factual correctness of the claim.
  • Manually-flagged verdicts (✎) reflect the author's judgment, not the LLM.
```

---

## 10. Sorting & filtering

Default ordering:
- Section "Problematic Citations": **severity bucket → .tex line number**
- Section "Partial Citations": **.tex line number** (matches reading order)
- Section "Clean Citations": **.tex line number** (matches reading order)

Optional UI controls in the HTML (small toolbar at the top of the problem
section):
- Filter by severity (chips: 🚫 / ❌ / ⚠️ / ❓)
- Filter by check type (identity issue / claim issue / both)
- Search box (filters by bib_key or paragraph text)

These are JS-only enhancements; the report works fully without them (e.g.
when printed).

---

## 11. Implementation sketch

Single new module `validity_report.py` that reads `project.json` + the
saved `.tex` content and renders the HTML:

```python
import os, shutil, zipfile

def build_validity_report(slug):
    """Generate the report + references bundle (folder + zip).
    Returns (html: str, html_path: str, zip_path: str)."""
    proj = project_store.get_project(slug)
    project_dir = os.path.join(PROJECTS_DIR, slug)

    # 1. Identify which references appear in Problematic + Partial sections.
    problematic_keys = _problematic_bib_keys(proj)   # set[str]
    partial_keys     = _partial_bib_keys(proj)        # set[str]
    keys_with_files  = problematic_keys | partial_keys

    # 2. Wipe + recreate the validity-report folder (always start clean).
    out_dir = os.path.join(project_dir, "validity-report")
    refs_dir = os.path.join(out_dir, "references")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(refs_dir, exist_ok=True)

    # 3. Copy each linked file from the project dir into references/.
    copied = []  # (arcname, abs_src) for the zip step
    for r in proj.get("results") or []:
        if r.get("bib_key") not in keys_with_files:
            continue
        for fkey, fname in (r.get("files") or {}).items():
            src = os.path.join(project_dir, fname)
            if os.path.isfile(src):
                dst = os.path.join(refs_dir, fname)
                shutil.copy2(src, dst)
                copied.append((f"references/{fname}", dst))

    # 4. Build references.zip — a top-level "references/" prefix in the archive
    #    so extracting next to the HTML reproduces the on-server folder layout.
    zip_path = os.path.join(out_dir, "references.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, abs_src in copied:
            zf.write(abs_src, arcname)

    # 5. Render HTML with relative links of the form `references/<filename>`
    #    and a top-of-page "Download references bundle" link to references.zip.
    sections = {
        "header":      _build_header(proj),
        "summary":     _build_summary(proj, zip_size=os.path.getsize(zip_path)),
        "problematic": _build_problematic_blocks(proj),  # per-occurrence
        "partial":     _build_partial_blocks(proj),       # per-occurrence
        "clean":       _build_clean_list(proj),           # one-liners
        "methodology": _build_methodology(proj),
    }
    html = _render_html(sections)   # plain f-strings or Jinja2
    html_path = os.path.join(out_dir, f"{slug}_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html, html_path, zip_path

# in app.py:
@app.route("/api/projects/<slug>/validity-report", methods=["POST"])
def api_build_validity_report(slug):
    html, html_path, zip_path = build_validity_report(slug)
    return jsonify({
        "ok": True,
        "html_path": os.path.relpath(html_path, PROJECTS_DIR),
        "zip_path":  os.path.relpath(zip_path, PROJECTS_DIR),
        "zip_bytes": os.path.getsize(zip_path),
    })

@app.route("/api/projects/<slug>/validity-report/download", methods=["GET"])
def api_download_validity_report(slug):
    """Download just the report HTML. References travel via the zip endpoint."""
    html, _, _ = build_validity_report(slug)
    return Response(
        html, mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{slug}_report.html"'},
    )

@app.route("/api/projects/<slug>/validity-report/references.zip", methods=["GET"])
def api_download_references_zip(slug):
    """Download the references bundle. Extracted next to the report HTML on
    the laptop, the relative references/<file> links inside the report
    resolve offline — no Flask required."""
    out_dir = os.path.join(PROJECTS_DIR, slug, "validity-report")
    zip_path = os.path.join(out_dir, "references.zip")
    if not os.path.isfile(zip_path):
        # Auto-build if the report hasn't been generated yet
        build_validity_report(slug)
    return send_from_directory(
        out_dir, "references.zip", as_attachment=True,
        download_name="references.zip")

# Static-style serving for the entire validity-report folder tree
# (so the in-app "Open report" button works without download):
@app.route("/projects/<slug>/validity-report/<path:filename>")
def projects_validity_static(slug, filename):
    return send_from_directory(
        os.path.join(PROJECTS_DIR, slug, "validity-report"), filename)
```

Local file links inside the HTML use **relative paths** of the form
`references/<safe_key>_pdf.pdf`, which resolve to the sibling `references/`
folder whether the report is opened locally (`file://...`) or served via
Flask (`/projects/<slug>/validity-report/<slug>_report.html` → browser
requests `/projects/<slug>/validity-report/references/<file>`, served by
the static route above).

Dashboard button: **Validity Report** (next to CSV / PDF). Clicking it
posts to `/validity-report` to (re)build the file, then opens
`/projects/<slug>/validity-report/<slug>_report.html` in a new tab.
A secondary "Download" link does an attachment-style download.

Tests: snapshot a small synthetic `project.json` with all severity buckets
represented (broken URL, identity not_matched, claim not_supported, claim
partial, no .md, missing key, clean), render, assert key strings appear in
each section and the per-occurrence count is correct (so a 3× cited key
yields 3 blocks).
