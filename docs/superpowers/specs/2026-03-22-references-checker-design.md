# References Checker — Design Spec / PRD

## Overview

A web-based tool that accepts `.bib` (BibTeX) file uploads, parses all references, and automatically searches academic APIs to find full papers or abstracts for each reference. No LLM required — uses Semantic Scholar, CrossRef, Unpaywall, and Google Scholar (via `scholarly`) as data sources.

## Goals

- Parse any valid `.bib` file regardless of size (1 to 500+ entries)
- Find full-text PDFs or abstracts for as many references as possible
- Provide real-time progress feedback during processing
- Allow downloading results as CSV or PDF reports
- Clean, modern UI with no authentication required

## Non-Goals

- User accounts, authentication, or persistent storage
- Downloading/hosting actual PDF files (we link to them)
- LLM-based summarization or analysis
- Editing or modifying .bib files

## Architecture

```
Browser (HTML/JS) <── SSE ──> Flask Backend
                                  │
                        ┌─────────┼──────────┐──────────┐
                        ▼         ▼          ▼          ▼
                    Semantic   CrossRef   Unpaywall   Scholarly
                    Scholar                           (fallback)
```

### Data Flow

1. User uploads `.bib` file via browser (max 2MB)
2. Flask backend parses it with `bibtexparser` (v2.x API)
3. Extracts: title, DOI, authors, year, journal, URL per entry
4. Deduplicates entries by DOI or normalized title
5. `POST /upload` returns JSON `{ "session_id": "...", "total": N }`
6. Frontend opens `EventSource` to `GET /stream/<session_id>`
7. For each reference, a lookup chain runs in a ThreadPool (5 workers):
   - **Step 1:** If DOI exists → CrossRef for metadata + Unpaywall for open-access PDF
   - **Step 2:** Semantic Scholar by title or DOI → abstract, citation count, PDF link
   - **Step 3:** If no DOI and no title → mark as "insufficient data," skip
   - **Step 4:** If still no abstract/PDF → `scholarly` Google Scholar scrape as fallback (opt-in, can be disabled)
8. Progress streamed to browser via SSE (per-reference updates)
9. Results displayed in rich UI with color-coded status + icons (accessible)
10. User can download CSV or PDF summary report

### Lookup Chain Decision Tree

```
Entry has DOI?
├── YES → CrossRef (metadata) + Unpaywall (PDF link)
│         → Semantic Scholar (abstract, citations, PDF)
│         → Got abstract or PDF? → DONE
│         → ELSE → Scholarly fallback (if enabled)
│
└── NO → Entry has title?
         ├── YES → Semantic Scholar by title (abstract, citations, PDF)
         │         → Got DOI from S2? → Unpaywall (PDF link)
         │         → Got abstract or PDF? → DONE
         │         → ELSE → Scholarly fallback (if enabled)
         │
         └── NO → Mark as "insufficient data"
```

### Title Disambiguation

When Semantic Scholar returns multiple results for a title search:
- Normalize both titles (lowercase, strip punctuation)
- Score by: exact title match (weight 3), year match (weight 2), first author last name match (weight 1)
- Take the highest-scoring result above a minimum threshold of 4

## ReferenceResult Data Model

```python
@dataclass
class ReferenceResult:
    bib_key: str              # original .bib entry key
    title: str                # paper title
    authors: list[str]        # list of author names
    year: str | None          # publication year
    journal: str | None       # journal/venue name
    doi: str | None           # DOI if found
    abstract: str | None      # abstract text
    pdf_url: str | None       # link to PDF (open access)
    url: str | None           # link to paper page
    citation_count: int | None # number of citations
    sources: list[str]        # which APIs contributed data ["crossref", "s2", "unpaywall"]
    status: str               # "found_pdf" | "found_abstract" | "not_found" | "parse_error" | "insufficient_data"
    error: str | None         # error message if any
```

## SSE Message Format

Event types sent over the SSE stream:

```
event: progress
data: {"index": 3, "total": 50, "bib_key": "smith2020", "status": "found_pdf", "result": {ReferenceResult as JSON}}

event: error
data: {"index": 5, "total": 50, "bib_key": "jones2019", "message": "All APIs failed"}

event: complete
data: {"total": 50, "found_pdf": 30, "found_abstract": 12, "not_found": 8}

event: heartbeat
data: {}
```

Heartbeat sent every 15s to keep connection alive. Client-side `EventSource` auto-reconnects; on reconnect, the server replays only unsent results from the session.

## Backend Modules

### `app.py` — Flask entry point
- `GET /` — serve the single-page UI
- `POST /upload` — accept .bib file (max 2MB), start processing, return JSON with session ID + count
- `GET /stream/<session_id>` — SSE endpoint for progress + results
- `GET /download/<session_id>/<format>` — download report (CSV or PDF); returns 409 if still processing

### `config.py` — Configuration
- `UNPAYWALL_EMAIL` — email for Unpaywall API (default: `"references-checker@example.com"`)
- `MAX_WORKERS` — thread pool size (default: 5)
- `MAX_UPLOAD_SIZE` — max .bib file size (default: 2MB)
- `SESSION_TTL` — session expiry in seconds (default: 1800)
- `SCHOLARLY_ENABLED` — enable/disable Google Scholar fallback (default: True)
- Loaded from environment variables with fallback defaults

### `bib_parser.py` — Parse .bib files
- Uses `bibtexparser` v2.x library
- Extracts: entry type, title, authors, year, DOI, journal, URL
- Handles malformed entries gracefully (skip + report as parse error)
- Deduplicates by DOI or normalized title
- Handles non-ASCII characters in titles and author names

### `lookup_engine.py` — Orchestrates lookup chain
- `ThreadPoolExecutor` with configurable workers (default 5)
- **Global** rate limiting per API via `threading.Lock` + timestamps (not per-worker)
- Per-reference lookup chain follows the decision tree above
- Returns unified `ReferenceResult` dataclass per entry
- Callbacks for SSE progress streaming

### `api_clients/` — Individual API wrappers

#### `semantic_scholar.py`
- Search by title or DOI
- Returns: abstract, PDF link, citation count, external IDs
- Global rate limit: 0.5s between calls (enforced via shared lock)
- Endpoint: `https://api.semanticscholar.org/graph/v1/paper/search`

#### `crossref.py`
- Resolve DOI to full metadata
- Returns: title, authors, journal, publisher, year, URL
- Global rate limit: 0.2s between calls
- Endpoint: `https://api.crossref.org/works/{doi}`

#### `unpaywall.py`
- Find open-access PDF by DOI
- Requires email parameter (from config)
- Returns: PDF URL, open-access status
- Global rate limit: 0.2s between calls
- Endpoint: `https://api.unpaywall.org/v2/{doi}`

#### `scholarly_client.py`
- Google Scholar fallback via `scholarly` Python lib
- Search by title, get abstract and links
- Global rate limit: 1s between calls
- Wrapped in try/except for graceful degradation
- **Known fragility:** Google may block automated access. If blocked, the module logs a warning and disables itself for the remainder of the session. Results from other APIs are unaffected.
- Can be disabled entirely via `SCHOLARLY_ENABLED=false`

### `report_exporter.py` — Generate downloadable reports

**CSV columns:** bib_key, title, authors, year, journal, doi, abstract, pdf_url, url, citation_count, sources, status

**PDF layout:** Title page with summary stats, then one row per reference in a table: #, Title, Authors, Year, Status, PDF Link. Full abstracts listed in an appendix section.

### `session_store.py` — In-memory session management
- Dict mapping session_id → {status, results, progress_index, created_at}
- Session states: `created` → `processing` → `completed` → `expired`
- 30-minute TTL per session
- Background cleanup thread every 5 minutes
- **Known limitation:** sessions do not survive server restarts

## Frontend

Single-page app with three states:

### State 1 — Upload
- App title, brief description
- Drag-and-drop zone + file picker for .bib files
- Upload button
- File size validation client-side (max 2MB)

### State 2 — Processing
- Progress bar: X / N references processed
- Live feed: reference cards appear as they're resolved
- Each card: title, status indicator, source

### State 3 — Results
- Stats summary: total, found with PDF, abstract only, not found
- Filter/search bar
- Reference cards in a grid/list:
  - Title, authors, year, journal
  - Expandable abstract
  - PDF link (if available)
  - Source badges showing all contributing APIs (e.g., "CrossRef + S2 + Unpaywall")
  - Color-coded status WITH icons for accessibility: green checkmark (PDF found), yellow doc icon (abstract only), red X (not found)
- Download buttons: CSV and PDF

### UI Design
- Designed with ui-ux-pro-max skill for professional styling
- Magic MCP components for polished interactive elements
- Responsive layout, works on desktop and tablet
- Accessible: ARIA labels on status indicators, not relying solely on color

## Error Handling

- **Malformed .bib entries:** skip, include in results as "parse error" with raw text
- **API timeouts:** 10s per request (15s for Scholarly), move to next API in chain
- **Rate limiting (429):** exponential backoff, retry up to 3 times
- **All retries fail:** mark reference as "not found," continue processing others
- **Invalid/empty .bib:** immediate user-friendly error message
- **Large files (500+):** warn user about expected processing time
- **Scholarly blocked:** log warning, disable scholarly for session, continue with other APIs
- **Entries with no title and no DOI:** mark as "insufficient data"

## Rate Limiting Strategy

All rate limits are **global** (shared across all workers via threading locks), not per-worker.

| API              | Delay Between Calls | Max Retries | Timeout | Effective Max Rate |
|------------------|---------------------|-------------|---------|-------------------|
| Semantic Scholar | 0.5s                | 3           | 10s     | 2 req/s           |
| CrossRef         | 0.2s                | 3           | 10s     | 5 req/s           |
| Unpaywall        | 0.2s                | 3           | 10s     | 5 req/s           |
| Scholarly        | 1.0s                | 2           | 15s     | 1 req/s           |

## Configuration

All settings via environment variables with sensible defaults:

| Variable             | Default                          | Description                    |
|----------------------|----------------------------------|--------------------------------|
| `UNPAYWALL_EMAIL`    | `references-checker@example.com` | Email for Unpaywall API        |
| `MAX_WORKERS`        | `5`                              | Thread pool size               |
| `MAX_UPLOAD_SIZE`    | `2097152` (2MB)                  | Max .bib file size in bytes    |
| `SESSION_TTL`        | `1800` (30 min)                  | Session expiry in seconds      |
| `SCHOLARLY_ENABLED`  | `true`                           | Enable Google Scholar fallback |
| `FLASK_PORT`         | `5000`                           | Port to run on                 |

## Project Structure

```
references-checker/
├── app.py
├── config.py
├── bib_parser.py
├── lookup_engine.py
├── session_store.py
├── report_exporter.py
├── api_clients/
│   ├── __init__.py
│   ├── semantic_scholar.py
│   ├── crossref.py
│   ├── unpaywall.py
│   └── scholarly_client.py
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
├── templates/
│   └── index.html
└── requirements.txt
```

## Dependencies

- `flask` — web framework
- `bibtexparser>=2.0` — .bib file parsing (v2 API)
- `requests` — HTTP calls to APIs
- `scholarly` — Google Scholar fallback
- `fpdf2` — PDF report generation
- `uuid` (stdlib) — session IDs
- `concurrent.futures` (stdlib) — thread pool
- `threading` (stdlib) — global rate limit locks

## Running

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Known Limitations

- **In-memory sessions:** do not survive server restarts
- **Scholarly fragility:** Google Scholar may block automated access; module self-disables on block
- **No persistent storage:** results are lost after session expires (30 min)
- **Single-server:** not designed for horizontal scaling

## Decisions Made

- **No LLM:** all lookups via structured APIs and scholarly scraping
- **No database:** session-based, in-memory storage with TTL
- **No auth:** open tool, anyone with the URL can use it
- **SSE over WebSockets:** simpler, one-directional is sufficient for progress
- **5 concurrent workers:** balances speed vs. API rate limits
- **Global rate limiting:** shared locks prevent exceeding API limits regardless of worker count
- **Lookup order:** CrossRef/Unpaywall first (fast, structured), then Semantic Scholar (rich data), then Scholarly (fallback, slower)
- **bibtexparser v2:** modern API, better handling of edge cases
- **Scholarly opt-in:** can be disabled if problematic in deployment environment
