# References Checker v2 — Project-Based Reference Management

## Context

Currently the app is stateless: upload a `.bib`, process it, view results, and everything disappears after 30 minutes or server restart. There is no way to persist results, download papers locally, or revisit previous checks.

**Goal:** Add project-based workflow where references are organized into named projects, results persist on disk, and found artifacts (PDFs, abstracts, web pages) are automatically downloaded and stored locally. Users can also refresh individual references on demand.

---

## 1. Project Storage Structure

```
projects/
  my-thesis/
    project.json                    <-- metadata + all results + original parsed refs
    smith2020_pdf.pdf               <-- downloaded PDF
    smith2020_abstract.txt          <-- abstract as plain text
    smith2020_page.html             <-- saved web page HTML
    jones2019_pdf.pdf
    jones2019_abstract.txt
    ...
```

### `project.json` schema

```json
{
  "name": "My Thesis",
  "slug": "my-thesis",
  "created_at": "2026-04-06T12:00:00",
  "updated_at": "2026-04-06T12:30:00",
  "bib_filename": "thesis.bib",
  "status": "completed|processing|created",
  "total": 42,
  "results": [
    {
      "bib_key": "smith2020",
      "title": "...",
      "authors": ["..."],
      "year": "2020",
      "journal": "...",
      "doi": "...",
      "abstract": "...",
      "pdf_url": "...",
      "url": "...",
      "citation_count": 15,
      "sources": ["crossref", "semantic_scholar"],
      "status": "found_pdf",
      "error": null,
      "files": {
        "pdf": "smith2020_pdf.pdf",
        "abstract": "smith2020_abstract.txt",
        "page": "smith2020_page.html"
      }
    }
  ],
  "parsed_refs": [
    {
      "bib_key": "smith2020",
      "entry_type": "article",
      "title": "...",
      "authors": "...",
      "year": "2020",
      "doi": "10.1234/...",
      "url": null,
      "arxiv_id": null
    }
  ]
}
```

- `parsed_refs` stores original bib entries so individual references can be re-processed without the `.bib` file
- `files` dict tracks which artifacts were successfully downloaded (only keys for files that exist)
- `slug` is a filesystem-safe version of the project name

---

## 2. File Naming Convention

| Artifact | Filename | When saved |
|----------|----------|------------|
| PDF | `<bib_key>_pdf.pdf` | When `pdf_url` is available |
| Abstract | `<bib_key>_abstract.txt` | When `abstract` text is available |
| Web page | `<bib_key>_page.html` | When `url` is available |

`bib_key` is sanitized for Windows filenames (replace `<>:"/\|?*` with `_`, truncate to 80 chars).

---

## 3. New Backend Modules

### 3.1 `project_store.py` (NEW)

File-based project persistence using JSON + folders.

**Functions:**
- `create_project(name, bib_filename, parsed_refs)` — create folder + initial `project.json`
- `get_project(slug)` — read and return `project.json`
- `list_projects()` — return summaries of all projects (name, slug, date, status, counts)
- `delete_project(slug)` — remove folder and all contents
- `update_project(slug, **kwargs)` — update top-level fields (status, updated_at)
- `save_result(slug, result)` — add or update a single result (matched by bib_key)
- `get_parsed_ref(slug, bib_key)` — return original parsed ref for refresh

**Thread safety:** Per-project locks (`dict[slug, Lock]`) for concurrent writes.
**Atomic writes:** Write to `.tmp` file, then `os.replace()` to prevent corruption.
**Batch optimization:** During bulk processing, accumulate results in memory and flush every 10 results (not on every single result) to avoid excessive disk I/O for large bib files.

### 3.2 `file_downloader.py` (NEW)

Downloads and saves reference artifacts to project folders.

**Function:**
```python
def download_reference_files(project_dir, bib_key, result, force=False):
    """Returns dict like {"pdf": "key_pdf.pdf", "abstract": "key_abstract.txt", "page": "key_page.html"}"""
```

**Download rules:**

| File | Source field | Timeout | Max size | Validation |
|------|-------------|---------|----------|------------|
| PDF | `result["pdf_url"]` | 30s | 50MB | Check first bytes are `%PDF` |
| Abstract | `result["abstract"]` | N/A | N/A | Direct text write (UTF-8) |
| Web page | `result["url"]` | 20s | 5MB | Save raw HTML |

- Skip download if file already exists and `force=False`
- On refresh (`force=True`), delete existing files first, then re-download
- All download failures are logged but never block the pipeline
- Use same User-Agent as scholarly client for web page downloads

---

## 4. API Routes

### New routes

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/projects` | List all projects |
| `POST` | `/api/projects` | Create new project `{"name": "..."}` |
| `GET` | `/api/projects/<slug>` | Get full project data |
| `DELETE` | `/api/projects/<slug>` | Delete project + all files |
| `POST` | `/api/projects/<slug>/upload` | Upload `.bib` and start processing |
| `POST` | `/api/projects/<slug>/refresh/<bib_key>` | Re-process single reference |
| `GET` | `/api/projects/<slug>/files/<filename>` | Serve downloaded file |
| `GET` | `/stream/<slug>` | SSE stream for project processing |
| `GET` | `/download/<slug>/<fmt>` | Export CSV/PDF from project |

### Processing flow (within project)

```
POST /api/projects/<slug>/upload
  -> parse .bib file
  -> save parsed_refs to project.json
  -> create in-memory session (for SSE streaming)
  -> spawn background thread:
       for each reference:
         1. process_reference(ref)        # existing lookup chain
         2. download_reference_files()    # NEW: save PDF/abstract/page
         3. save_result(slug, result)     # persist to project.json
         4. store.add_result(sid, result) # feed SSE stream
       mark project as completed
  -> return {"slug": "...", "total": N, "session_id": "..."}
```

### Per-reference refresh flow

```
POST /api/projects/<slug>/refresh/<bib_key>
  -> load parsed_ref from project.json
  -> spawn background thread:
       1. process_reference(ref)
       2. download_reference_files(force=True)
       3. save_result(slug, result)
  -> return {"status": "refreshing"}

Frontend polls:
  GET /api/projects/<slug>/refresh-status/<bib_key>
  -> returns {"status": "refreshing"} or {"status": "done", "result": {...}}
```

---

## 5. Frontend Changes

### New View: Project List (View 0)

Shown on page load. Contains:
- Header: "Your Projects"
- "Create New Project" — name input + create button
- Project cards grid, each showing:
  - Project name, creation date
  - Reference counts (total, PDF found, abstract, not found)
  - Status badge (processing/completed)
  - Click to open project
  - Delete button (with confirmation)

### Modified View: Upload (View 1)

- Shows project name in header/breadcrumb
- "Back to Projects" link
- Uploads to project-scoped endpoint

### Modified View: Results (View 3)

Each result card gets:
- **Refresh button** (circular arrow icon) — triggers per-reference re-fetch
  - Shows spinner during refresh
  - Re-renders card when done
- **Local file indicators** — small icons showing which files are saved:
  - PDF icon (filled green if downloaded, gray outline if not)
  - TXT icon for abstract
  - HTML icon for web page
- **"Open local file" links** — for each downloaded file, link to `/api/projects/<slug>/files/<filename>`
- "Back to Projects" replaces "New Check"
- Download CSV/PDF uses project-scoped URLs

### View flow

```
Page load
  -> GET /api/projects
  -> Show Project List (View 0)

Create project
  -> POST /api/projects {"name": "..."}
  -> Show Upload view (View 1) for new project

Open existing project
  -> GET /api/projects/<slug>
  -> If status=completed: show Results (View 3)
  -> If status=processing: show Processing (View 2), connect SSE

Upload .bib in project
  -> POST /api/projects/<slug>/upload
  -> Show Processing (View 2), connect SSE

Processing complete
  -> Show Results (View 3)

Back to projects
  -> Show Project List (View 0)
```

---

## 6. Config Changes

Add to `config.py`:
```python
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", os.path.join(os.path.dirname(__file__), "projects"))
```

Add to `.gitignore`:
```
projects/
```

---

## 7. Implementation Order

### Phase 1: Backend infrastructure
1. Add `PROJECTS_DIR` to `config.py`, create directory on app startup
2. Create `project_store.py` — project CRUD + JSON persistence
3. Create `file_downloader.py` — PDF/abstract/page download logic
4. Add new API routes to `app.py` (projects CRUD, upload, refresh, file serving)
5. Wire up processing callback to do dual-write (project store + session store)
6. Add per-reference refresh endpoint

### Phase 2: Frontend
7. Add project list view (View 0) to `index.html`
8. Update `app.js` — project management, project-scoped upload/stream/download
9. Add refresh button + local file indicators to result cards
10. Add CSS for new elements (project cards, refresh spinner, file icons)

### Phase 3: Cleanup
11. Add `projects/` to `.gitignore`
12. Old session-only routes can be kept as fallback or removed

---

## 8. Files to Create/Modify

| File | Action | What changes |
|------|--------|-------------|
| `project_store.py` | NEW | Project CRUD, JSON persistence |
| `file_downloader.py` | NEW | Download PDFs, abstracts, web pages |
| `config.py` | MODIFY | Add `PROJECTS_DIR` |
| `app.py` | MODIFY | Add 8 new API routes, modify processing callback |
| `templates/index.html` | MODIFY | Add project list view, refresh buttons |
| `static/js/app.js` | MODIFY | Project management UI, refresh logic, file indicators |
| `static/css/style.css` | MODIFY | Project cards, refresh button, file icons |
| `.gitignore` | MODIFY | Add `projects/` |

---

## 9. Verification

1. Create a project via the UI
2. Upload a `.bib` file into the project
3. Verify processing completes with SSE streaming
4. Check `projects/<slug>/` folder has `project.json` + downloaded files
5. Restart server — verify project and results persist
6. Open the project again — results load from disk
7. Click refresh on a reference — verify it re-processes and re-downloads
8. Export CSV/PDF from project
9. Delete project — verify folder is removed
10. Test with a large `.bib` (100+ refs) — verify batch writes don't slow down SSE
