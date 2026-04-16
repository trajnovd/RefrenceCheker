# References Checker v3 — Citation Context Verification

## Context

After v2, the system finds references and downloads their content (PDFs, abstracts, web pages) into project folders. The next step is to help the user verify that each citation in their paper actually corresponds to the content of the referenced work. This is a manual review process assisted by a split-view interface.

**Goal:** The user uploads a LaTeX document into a project. A new view shows the LaTeX on the left and the referenced paper's content on the right. The user navigates between `\cite{}` tags to review each citation in context.

---

## 1. User Flow

```
Project Results View
  -> "Upload LaTeX" button
  -> Upload .tex file
  -> Opens Citation Review View (View 4)

Citation Review View:
[< Previous]  Citation 3 / 47 (smith2020)  [Next >]   [Back to Results]
  ┌─────────────────────────────────┬─────────────────────────────────┐
  │         LaTeX Document          │  Smith, Jones (2020) Nature     │
  │                                 │  DOI: 10.1234/...  [found_pdf] │
  │                                 ├─────────────────────────────────┤
  │  ... text text text             │  [PDF]  [HTML]  [Abstract]      │
  │  as shown in [cite:smith2020]   │ ┌─────────────────────────────┐ │
  │  ^^^^^^^^^^^^^^^^^^^^^^^^       │ │                             │ │
  │  (highlighted, scrolled to)     │ │   PDF viewer (iframe)       │ │
  │                                 │ │   or HTML page (iframe)     │ │
  │  more text continues here       │ │   or Abstract text          │ │
  │  with additional context        │ │                             │ │
  │  for the reviewer to read       │ │   (main content area,      │ │
  │                                 │ │    takes most of the space) │ │
  │                                 │ │                             │ │
  │                                 │ └─────────────────────────────┘ │
  └─────────────────────────────────┴─────────────────────────────────┘

Right panel layout:
  - **Top bar (compact):** Authors, Year, Journal, DOI, status badge, links — 2-3 lines max
  - **Tab buttons:** [PDF] [HTML] [Abstract] — only enabled when that content exists
    - PDF tab: shows local PDF in `<iframe>` (from `/api/projects/<slug>/files/<key>_pdf.pdf`)
    - HTML tab: shows local HTML page in `<iframe>` (from `/api/projects/<slug>/files/<key>_page.html`)
    - Abstract tab: shows abstract as formatted text
  - **Content area:** Takes remaining height. Displays the selected tab content.
  - **Auto-select logic:** When navigating to a citation, auto-select the best available tab:
    PDF if exists > HTML if exists > Abstract if exists
```

---

## 2. LaTeX Parsing

Parse the `.tex` file to extract all `\cite` commands and their positions.

**Supported cite formats:**
- `\cite{key}`
- `\cite{key1,key2,key3}` (multiple keys in one cite)
- `\citep{key}`, `\citet{key}` (natbib)
- `\parencite{key}`, `\textcite{key}` (biblatex)
- `\cite[p.~42]{key}` (with optional arguments)

**Output: list of citation occurrences in document order:**
```python
[
  {"bib_key": "smith2020", "position": 1423, "line": 47, "context_before": "...as demonstrated by", "context_after": "which shows that..."},
  {"bib_key": "jones2019", "position": 1890, "line": 52, ...},
  ...
]
```

Each occurrence stores:
- `bib_key` — the citation key (matched against project results)
- `position` — character offset in the file (for highlighting)
- `line` — line number (1-based)
- `context_before` — ~200 chars before the cite (for context display)
- `context_after` — ~200 chars after the cite

Multi-key cites like `\cite{a,b,c}` produce 3 separate entries (one per key, same position).

---

## 3. Storage

Add to `project.json`:
```json
{
  "tex_filename": "thesis.tex",
  "tex_content": "... full LaTeX source ...",
  "citations": [
    {"bib_key": "smith2020", "position": 1423, "line": 47, "context_before": "...", "context_after": "..."},
    ...
  ]
}
```

The full LaTeX content is stored so the frontend can render it without re-reading the file. Citations list is pre-computed on upload.

---

## 4. Backend Changes

### New module: `tex_parser.py`

```python
def parse_tex_citations(tex_content):
    """Parse LaTeX content, return list of citation occurrences in document order."""
    # Regex for \cite variants with optional args: \cite[...]{key1,key2}
    # Returns list of dicts with bib_key, position, line, context_before, context_after
```

### New API routes

| Method | Route | Purpose |
|--------|-------|---------|
| `POST` | `/api/projects/<slug>/upload-tex` | Upload `.tex` file, parse citations, store |
| `GET` | `/api/projects/<slug>/tex` | Get tex content + citations list |
| `GET` | `/api/projects/<slug>/reference/<bib_key>/content` | Get full reference content (abstract text, PDF text if available) |

**`POST /api/projects/<slug>/upload-tex`:**
- Accepts `.tex` file upload
- Reads content as UTF-8 text
- Calls `parse_tex_citations(content)` to extract citation list
- Saves `tex_filename`, `tex_content`, and `citations` to `project.json`
- Returns `{"total_citations": N, "unique_keys": M, "unmatched_keys": [...]}`

**`GET /api/projects/<slug>/tex`:**
- Returns `{"tex_content": "...", "citations": [...], "tex_filename": "..."}`
- Frontend uses this to render the review view

**`GET /api/projects/<slug>/reference/<bib_key>/content`:**
- Returns the reference result from `project.json` for that bib_key
- Includes abstract text
- If `_abstract.txt` file exists, reads it
- If `_page.html` file exists, includes a link to it

---

## 5. Frontend Changes

### New View: Citation Review (View 4)

Added to `index.html` as a new section `id="view-review"`.

**Layout:** CSS Grid with two columns (50/50 split), full height.

**Left panel — LaTeX document:**
- `<pre>` or `<div>` with the LaTeX source displayed as plain text
- Line numbers shown on the left margin
- Current `\cite{}` tag highlighted with a bright background color
- Auto-scrolls to keep the highlighted cite visible (centered vertically)
- Context around the cite is visually distinct (e.g., surrounding paragraph slightly darker background)

**Right panel — Reference content (two zones):**

- **Info bar (compact, 2-3 lines):**
  - Title (bold, single line, truncated with tooltip)
  - Authors, Year, Journal, DOI — inline, small text
  - Status badge
  - External links: [Open PDF URL] [Open Web URL] — small icon links

- **Content tabs + viewer (takes remaining height):**
  - **Tab bar:** Three buttons — `[PDF]` `[HTML]` `[Abstract]`
    - Each button is only enabled if that content exists for the reference
    - Active tab is visually highlighted
    - Disabled tabs are grayed out
  - **Content area (fills remaining space):**
    - **PDF tab:** `<iframe>` pointing to `/api/projects/<slug>/files/<bib_key>_pdf.pdf`
      - Browser's built-in PDF viewer renders the PDF inline
    - **HTML tab:** `<iframe>` pointing to `/api/projects/<slug>/files/<bib_key>_page.html`
      - Shows the saved web page
    - **Abstract tab:** `<div>` with the abstract text, formatted as readable prose
      - Scrollable if long
      - Shows "No abstract available" if empty
  - **Auto-select on navigation:** When user moves to a new citation, the best tab is auto-selected:
    - PDF if `files.pdf` exists, else HTML if `files.page` exists, else Abstract
  - **No content state:** If reference has no PDF, no HTML, and no abstract, show a message:
    "No content available for this reference. Use the set-link button in the results view to add a URL."

**Navigation bar (bottom or top of the view):**
- `[< Previous]` button — jumps to previous citation occurrence
- Citation counter: `"Citation 3 / 47 (smith2020)"`
- `[Next >]` button — jumps to next citation occurrence
- `[Back to Results]` button — returns to the results view
- Keyboard shortcuts: Left/Right arrow keys for prev/next

**Visual states:**
- Citation highlighted in LaTeX: bright yellow/gold background
- Unmatched citation (bib_key not found in results): red highlight + "Reference not found in project" message on right panel
- Current citation key shown in navigation bar

### JavaScript additions to `app.js`:

**New state:**
```javascript
let texContent = null;       // full LaTeX string
let citations = [];          // parsed citation list
let currentCiteIndex = 0;    // which citation we're viewing
```

**New functions:**
```javascript
function uploadTex()              // POST /api/projects/<slug>/upload-tex
function openReviewView()         // GET /api/projects/<slug>/tex, then render
function renderTexPanel()         // Render LaTeX with line numbers in left panel
function highlightCitation(idx)   // Highlight cite at index, scroll to it
function showReferencePanel(key)  // Update info bar + auto-select best tab
function switchTab(tab)           // Switch between 'pdf', 'html', 'abstract' tabs
function nextCitation()           // currentCiteIndex++, update both panels
function prevCitation()           // currentCiteIndex--, update both panels
```

---

## 6. LaTeX Display Details

The LaTeX source is displayed as **plain text** (not rendered). This is intentional — the user needs to see the raw `\cite{}` commands.

**Line numbers:** Each line gets a number. Use a two-column layout within the left panel:
```
  47 │  as demonstrated by Smith et al. \cite{smith2020},
  48 │  who showed that the model achieves state-of-the-art
```

**Highlighting:** The `\cite{smith2020}` portion gets wrapped in a `<mark>` element with class `cite-highlight` (bright background). Only the current citation is highlighted; others can have a subtle secondary highlight.

**Scrolling:** When navigating to a citation, the left panel scrolls so the highlighted line is vertically centered. Use `element.scrollIntoView({ behavior: 'smooth', block: 'center' })`.

---

## 7. Implementation Order

### Phase 1: Backend
1. Create `tex_parser.py` — regex-based citation extraction
2. Add `upload-tex` route to `app.py`
3. Add `tex` GET route to `app.py`
4. Update `project_store.py` if needed for tex fields

### Phase 2: Frontend
5. Add "Upload LaTeX" button to results view (View 3)
6. Add View 4 section to `index.html` (split layout)
7. Implement `app.js` — tex upload, review view rendering, navigation
8. Add CSS for split layout, line numbers, highlighting, reference panel

### Phase 3: Polish
9. Keyboard navigation (arrow keys)
10. Show unmatched citations (keys not in project results)
11. Show citation count summary after tex upload

---

## 8. Files to Create/Modify

| File | Action | What changes |
|------|--------|-------------|
| `tex_parser.py` | NEW | Parse LaTeX citations with regex |
| `app.py` | MODIFY | Add 2 new routes (upload-tex, get tex) |
| `project_store.py` | MODIFY | Add `save_tex` helper (optional) |
| `templates/index.html` | MODIFY | Add View 4 (citation review split layout) |
| `static/js/app.js` | MODIFY | Tex upload, review view, navigation logic |
| `static/css/style.css` | MODIFY | Split layout, line numbers, highlights, reference panel |

---

## 9. CSS Layout for Review View

```css
.review-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: auto 1fr;
  gap: 0;
  height: calc(100vh - 160px);  /* full height minus header */
}

.review-nav { grid-column: 1 / -1; }  /* navigation spans full width */
.review-tex { overflow-y: auto; }      /* left panel scrolls */
.review-ref { overflow-y: auto; }      /* right panel scrolls */
```

---

## 10. Verification

1. Upload a `.tex` file to a project that has processed references
2. Verify citations are extracted correctly (count, keys)
3. Navigate with Next/Previous — LaTeX scrolls to each cite, right panel updates
4. Verify unmatched keys show warning
5. Verify keyboard navigation (arrow keys) works
6. Test with multi-key cites like `\cite{a,b,c}`
7. Test with natbib/biblatex variants (`\citep`, `\textcite`, etc.)
