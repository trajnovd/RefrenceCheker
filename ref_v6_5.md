# Reference Checker — v6.5: Global Reference Cache

## 0. Summary

Move per-project metadata from `project.json` into a **single SQLite database** sitting at the root of `projects/`. Use that database to maintain a **global reference cache**: before downloading a paper for a new project, look up whether any project (across all users) has already downloaded a verified copy of the same reference. If so, **copy the artifacts and metadata** instead of downloading again.

Files (`*_pdf.pdf`, `*_page.html`, `*.md`, `*_abstract.txt`) stay on disk in each project's folder. Only metadata moves into the DB.

The data access layer is wrapped in a `ReferenceStore` abstraction (ABC + concrete `SqliteReferenceStore`) so the database can later be swapped for Postgres or another backend without rewriting business logic.

The schema is **multi-user from day one** (every project belongs to a user) even though the app currently runs single-user; the cache search is global across all users.

This update is orthogonal to the v7 folder restructure — it changes WHERE metadata lives, not where files live. It should land BEFORE v7 so the v7 source-provider work writes against the new abstraction.

---

## 1. Decisions answered up front

1. **Database**: SQLite, file at `projects/refchecker.sqlite`, opened in WAL mode for concurrent reads.
2. **ORM**: `sqlite3` stdlib module + raw SQL with parameter binding. No SQLAlchemy — the schema is small and the abstraction lives one layer up.
3. **Abstraction**: `ReferenceStore` ABC with one concrete implementation (`SqliteReferenceStore`). Future Postgres = new subclass, swap at construction.
4. **Files on disk**: unchanged. Each project still has its own folder with its artifact files.
5. **Cache lookup keys** (priority order): `doi` → `arxiv_id` → `(normalized_title, sorted_first_authors)`.
6. **Cache eligibility**: only references with `status ∈ {found_pdf, found_web_page}` AND `ref_match.verdict ∈ {matched, manual_matched}`. Wrong-paper artifacts must never be served from cache.
7. **Multi-user schema**: `users` and `projects.user_id` from day one. One default user (`local`) is inserted by migration; auth wiring comes later.
8. **Cache scope**: cache lookup queries ALL users' projects, regardless of which user owns the destination project.
9. **Migration**: one-time script `scripts/migrate_to_v6_5.py` walks `projects/*/project.json`, inserts everything into the DB, marks each project as migrated. App refuses to load projects without the migration flag (hard gate).
10. **Activity log**: stays per-project (kept in DB but as a separate table — not a hot-path field).

---

## 2. Database schema

### 2.1 Tables

```sql
CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE,
  name TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  status TEXT,                      -- created / processing / completed
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  bib_filename TEXT,
  tex_filename TEXT,
  tex_content TEXT,                 -- the full .tex (kept here for now; could move to file)
  layout_version INTEGER DEFAULT 1, -- pre-flag for v7 layout migration
  last_viewed_citation INTEGER DEFAULT 0
);
CREATE INDEX idx_projects_user ON projects(user_id);

-- One row per parsed bib entry, prior to lookup.
CREATE TABLE parsed_refs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  bib_key TEXT NOT NULL,
  entry_type TEXT,
  title TEXT,
  authors TEXT,                     -- raw bibtex authors string
  year TEXT,
  journal TEXT,
  doi TEXT,
  arxiv_id TEXT,
  url TEXT,
  raw_bib TEXT,
  all_fields_json TEXT,             -- JSON blob of remaining bib fields
  status TEXT,                      -- e.g. insufficient_data
  manually_added INTEGER DEFAULT 0,
  UNIQUE(project_id, bib_key)
);
CREATE INDEX idx_parsed_refs_project ON parsed_refs(project_id);

-- One row per result of the lookup pipeline. Mostly mirrors today's `result` dict.
CREATE TABLE refs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  bib_key TEXT NOT NULL,
  -- identity
  title TEXT,
  authors_json TEXT,                -- JSON list of strings
  year TEXT,
  journal TEXT,
  doi TEXT,
  arxiv_id TEXT,
  url TEXT,
  pdf_url TEXT,
  -- enrichment
  abstract TEXT,
  citation_count INTEGER,
  sources_json TEXT,                -- JSON list of source names
  status TEXT NOT NULL,             -- found_pdf / found_abstract / found_web_page / not_found / bib_url_unreachable / insufficient_data
  error TEXT,
  bib_url_failure_json TEXT,
  raw_bib TEXT,
  url_source_only INTEGER DEFAULT 0,
  -- bookkeeping
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, bib_key)
);
CREATE INDEX idx_refs_project ON refs(project_id);
CREATE INDEX idx_refs_doi ON refs(doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_refs_arxiv ON refs(arxiv_id) WHERE arxiv_id IS NOT NULL;

-- Files attached to a ref. One row per (ref, filetype). Filetype ∈ pdf, page, abstract, md, pasted.
CREATE TABLE ref_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref_id INTEGER NOT NULL REFERENCES refs(id) ON DELETE CASCADE,
  filetype TEXT NOT NULL,
  filename TEXT NOT NULL,           -- e.g. "smith2020_pdf.pdf"
  size_bytes INTEGER,
  sha256 TEXT,                      -- for cache integrity / dedup
  UNIQUE(ref_id, filetype)
);
CREATE INDEX idx_ref_files_ref ON ref_files(ref_id);
CREATE INDEX idx_ref_files_sha ON ref_files(sha256) WHERE sha256 IS NOT NULL;

-- Per-file provenance — current files_origin dict.
CREATE TABLE ref_files_origin (
  ref_file_id INTEGER PRIMARY KEY REFERENCES ref_files(id) ON DELETE CASCADE,
  tier TEXT NOT NULL,               -- direct / oa_fallbacks / wayback / curl_cffi / playwright / cache_hit / manual_*
  url TEXT,
  host TEXT,
  captured_at TEXT
);

-- Optional per-ref artifacts: download_log, ref_match, pdf_url_fallbacks
-- Stored as JSON to avoid premature normalization (small, only the report reads them).
CREATE TABLE ref_extras (
  ref_id INTEGER PRIMARY KEY REFERENCES refs(id) ON DELETE CASCADE,
  download_log_json TEXT,           -- list of tier attempts
  ref_match_json TEXT,              -- {verdict, evidence, model, ...}
  pdf_url_fallbacks_json TEXT       -- list of alt URLs
);

-- Cache lookup index: denormalized rows for fast multi-key search.
-- Populated automatically when a ref reaches a cacheable state
-- (status ∈ {found_pdf, found_web_page} AND ref_match.verdict ∈ {matched, manual_matched}).
CREATE TABLE ref_cache_keys (
  ref_id INTEGER NOT NULL REFERENCES refs(id) ON DELETE CASCADE,
  key_type TEXT NOT NULL,           -- 'doi' | 'arxiv' | 'title_authors'
  key_value TEXT NOT NULL,
  PRIMARY KEY (ref_id, key_type, key_value)
);
CREATE INDEX idx_cache_keys_lookup ON ref_cache_keys(key_type, key_value);

-- Citations table — one row per occurrence of a \cite{} in the .tex.
CREATE TABLE citations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  bib_key TEXT NOT NULL,
  line_number INTEGER,
  position INTEGER,
  end_position INTEGER,
  cite_command TEXT,
  context_before TEXT,
  context_after TEXT,
  claim_check_id INTEGER REFERENCES claim_checks(id),
  -- Order in the .tex; preserves the user's expected list order.
  ord INTEGER NOT NULL
);
CREATE INDEX idx_citations_project ON citations(project_id);

-- Claim-check verdicts. Cache key (current `claim_checks` dict key) lives here.
CREATE TABLE claim_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  cache_key TEXT NOT NULL,          -- citation_index or paragraph hash
  verdict TEXT,                     -- supported / not_supported / partial / ...
  confidence REAL,
  explanation TEXT,
  evidence TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  checked_at TEXT,
  manual INTEGER DEFAULT 0,
  UNIQUE(project_id, cache_key)
);

-- Activity log — append-only.
CREATE TABLE activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  message TEXT NOT NULL,
  target TEXT
);
CREATE INDEX idx_activity_project ON activity(project_id, ts DESC);
```

### 2.2 Why JSON columns for `_extras`?

`download_log`, `ref_match`, and `pdf_url_fallbacks` are small (under 5 KB), only read when rendering the validity report or the review pane, and have unstable schemas (new tier names, new ref_match fields ship every few weeks). Normalizing them into separate tables would mean writing a migration every time we add a tier; JSON is the right call here.

### 2.3 What stays on disk

```
projects/
├── refchecker.sqlite                ← all metadata (this update)
├── refchecker.sqlite-wal            ← WAL log
├── refchecker.sqlite-shm            ← shared memory
└── <slug>/
    ├── main.tex                      (still on disk)
    ├── refs.bib                      (still on disk)
    ├── <bib_key>_pdf.pdf             (still on disk)
    ├── <bib_key>_page.html           (still on disk)
    ├── <bib_key>.md                  (still on disk)
    └── <bib_key>_abstract.txt        (still on disk)
```

`project.json` is **deleted** by the migration. The DB is the only source of truth for metadata.

---

## 3. The `ReferenceStore` abstraction

```python
# reference_store.py — module-level public API mirrors today's project_store.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class CacheHit:
    """A reference found in the global cache. The caller copies the file
    artifacts from the source project's folder into the destination project's
    folder, then writes a new ref row pointing at those copied files."""
    source_project_slug: str
    source_ref_id: int
    matched_by: str                      # 'doi' | 'arxiv' | 'title_authors'
    result: dict                         # the full result dict (today's shape)
    files: dict                          # {'pdf': 'smith_pdf.pdf', ...}
    files_origin: dict                   # provenance per filetype


class ReferenceStore(ABC):
    """Backend-agnostic reference + project storage.

    Concrete implementations: SqliteReferenceStore (v6.5),
    PostgresReferenceStore (future enterprise).
    """

    # ----- Project lifecycle -----
    @abstractmethod
    def create_project(self, name: str, user_id: int) -> dict: ...
    @abstractmethod
    def get_project(self, slug: str) -> Optional[dict]: ...
    @abstractmethod
    def list_projects(self, user_id: Optional[int] = None) -> list[dict]: ...
    @abstractmethod
    def delete_project(self, slug: str) -> bool: ...
    @abstractmethod
    def update_project(self, slug: str, **fields) -> None: ...

    # ----- Parsed refs (pre-lookup bib entries) -----
    @abstractmethod
    def save_parsed_refs(self, slug: str, bib_filename: str, parsed_refs: list[dict]) -> None: ...
    @abstractmethod
    def add_parsed_ref(self, slug: str, ref: dict) -> bool: ...
    @abstractmethod
    def get_parsed_ref(self, slug: str, bib_key: str) -> Optional[dict]: ...

    # ----- Results (post-lookup) -----
    @abstractmethod
    def save_result(self, slug: str, result: dict) -> None: ...
    @abstractmethod
    def save_results_batch(self, slug: str, results: list[dict]) -> None: ...
    @abstractmethod
    def get_result(self, slug: str, bib_key: str) -> Optional[dict]: ...
    @abstractmethod
    def list_results(self, slug: str) -> list[dict]: ...

    # ----- Citations + claim checks -----
    @abstractmethod
    def save_citations(self, slug: str, citations: list[dict]) -> None: ...
    @abstractmethod
    def save_claim_check(self, slug: str, cache_key: str, verdict: dict) -> None: ...
    @abstractmethod
    def get_claim_check(self, slug: str, cache_key: str) -> Optional[dict]: ...
    @abstractmethod
    def set_citation_check_key(self, slug: str, citation_index: int, cache_key: Optional[str]) -> None: ...

    # ----- Reference identity (for ref_match recheck) -----
    @abstractmethod
    def save_ref_match(self, slug: str, bib_key: str, match_dict: dict) -> bool: ...
    @abstractmethod
    def get_ref_match(self, slug: str, bib_key: str) -> Optional[dict]: ...

    # ----- Activity -----
    @abstractmethod
    def add_activity(self, slug: str, activity_type: str, message: str, target: str = None) -> None: ...

    # ----- Telemetry -----
    @abstractmethod
    def compute_download_stats(self, slug: str) -> Optional[dict]: ...

    # ----- Last-viewed citation (UI persistence) -----
    @abstractmethod
    def set_last_viewed_citation(self, slug: str, citation_index: int) -> None: ...
    @abstractmethod
    def get_last_viewed_citation(self, slug: str) -> int: ...

    # ============================================================
    # NEW in v6.5 — Global cache lookup
    # ============================================================

    @abstractmethod
    def find_in_cache(self, *, doi: str = None, arxiv_id: str = None,
                      title: str = None, authors: str = None
                      ) -> Optional[CacheHit]: ...

    @abstractmethod
    def index_for_cache(self, slug: str, bib_key: str) -> None:
        """Add this ref's identity into ref_cache_keys when it becomes
        cacheable (status + ref_match qualified). Called from save_result
        and save_ref_match."""

    @abstractmethod
    def project_dir(self, slug: str) -> str:
        """Disk path for a project's file artifacts. Stays on disk; not in DB."""
```

### 3.1 Module-level shim for backward compat

`project_store.py` is gradually replaced by a thin shim that delegates to the singleton store:

```python
# project_store.py (v6.5)
from reference_store import get_store

def create_project(name): return get_store().create_project(name, _default_user_id())
def get_project(slug):    return get_store().get_project(slug)
# ... etc.
```

Keeps every existing call site (`app.py`, `lookup_engine.py`, etc.) working unchanged. Once stable, the shim is removed and callers import directly from `reference_store`.

---

## 4. Cache lookup flow

### 4.1 When the cache is consulted

Call site: `_process_ref_with_bib_url` in `app.py` (and `_do_refresh`, `_do_add`), **before** `pre_download_bib_url`.

```python
def _process_ref_with_bib_url(ref):
    # NEW: cache lookup BEFORE any network I/O.
    hit = store.find_in_cache(doi=ref.get("doi"),
                              arxiv_id=ref.get("arxiv_id"),
                              title=ref.get("title"),
                              authors=ref.get("authors"))
    if hit:
        return _materialize_cache_hit(slug, ref, hit)

    # ... existing pre-fetch + lookup pipeline unchanged ...
```

### 4.2 Materialization

```python
def _materialize_cache_hit(slug, ref, hit):
    src_dir = store.project_dir(hit.source_project_slug)
    dst_dir = store.project_dir(slug)
    safe_key = _safe_filename(ref["bib_key"])

    # Copy each file artifact, renaming to the destination's bib_key.
    new_files = {}
    for filetype, src_filename in hit.files.items():
        # Source file is named after source bib_key — rename for destination.
        suffix = _file_suffix(filetype)             # _pdf.pdf, _page.html, .md, ...
        dst_filename = safe_key + suffix
        shutil.copy2(os.path.join(src_dir, src_filename),
                     os.path.join(dst_dir, dst_filename))
        new_files[filetype] = dst_filename

    # Build a result with bib's identity + cached enrichment + cache_hit provenance.
    result = dict(hit.result)                       # copy
    result["bib_key"] = ref["bib_key"]              # keep destination's key
    result["raw_bib"] = ref.get("raw_bib")
    result["files"] = new_files
    result["files_origin"] = {
        ft: {"tier": "cache_hit",
             "source_project": hit.source_project_slug,
             "source_ref_id": hit.source_ref_id,
             "matched_by": hit.matched_by,
             "captured_at": now_iso()}
        for ft in new_files
    }
    return result
```

`tier="cache_hit"` is a new provenance value. UI shows it as a distinct badge ("Cached from project X"). The `download_log` is empty (no tiers ran).

### 4.3 Cache eligibility (write side)

A ref enters `ref_cache_keys` when:
- `result.status ∈ {found_pdf, found_web_page}`
- `ref_match.verdict ∈ {matched, manual_matched}` (for `ref_match.manual=True` we trust the human)
- At least one of (doi, arxiv_id, title) is set

Re-indexing happens automatically inside `save_result` and `save_ref_match`. A ref that flips back to `not_matched` is removed from the cache via `DELETE FROM ref_cache_keys WHERE ref_id=?`.

### 4.4 Title-author key normalization

```python
def _title_author_key(title, authors):
    """Build a stable lookup key from (title, authors).

    Title: lowercased, alphanumeric+space only, collapsed whitespace.
    Authors: extract last names (reuse arxiv_client._last_names),
             sort, join with '|'. Empty → fall back to title only.
    """
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    last_names = sorted(_last_names(authors))
    return f"{t}::{('|'.join(last_names))}"
```

Examples:
- `("Working Memory", "Baddeley, Alan and Hitch, Graham")` → `"working memory::baddeley|hitch"`
- `("Working Memory", None)` → `"working memory::"`

Title-only matches are softer than DOI/arxiv hits — the lookup function returns them only when no DOI/arxiv match exists.

### 4.5 Lookup priority

```python
def find_in_cache(*, doi=None, arxiv_id=None, title=None, authors=None) -> Optional[CacheHit]:
    # 1. DOI exact match (strongest)
    if doi:
        hit = self._lookup_by_key("doi", doi.lower())
        if hit: return hit
    # 2. arxiv_id exact match
    if arxiv_id:
        hit = self._lookup_by_key("arxiv", arxiv_id.lower())
        if hit: return hit
    # 3. (title, authors) match
    if title:
        key = _title_author_key(title, authors)
        hit = self._lookup_by_key("title_authors", key)
        if hit: return hit
    return None
```

Multiple hits for the same key (e.g. same DOI in 5 projects): pick the most recently updated `ref` row. The files are equivalent — any source works.

### 4.6 Integrity guard before file copy

Before `shutil.copy2`, verify the source file still exists on disk and (when sha256 is recorded) matches the expected hash. If the file is missing or hash-mismatched (manual edit, disk corruption), **drop the cache entry** and fall through to a real download. This keeps stale rows from breaking new projects.

---

## 5. Migration

### 5.1 Script: `scripts/migrate_to_v6_5.py`

Idempotent. Runnable as `python -m reference_store migrate` (or the script directly).

Steps:
1. Open / create `projects/refchecker.sqlite`. Apply schema if version < 1.
2. Insert default user (`local`) if `users` is empty.
3. For each `projects/<slug>/project.json`:
   - Skip if a row in `projects` already exists with this slug AND `layout_version >= 1`.
   - Insert `projects` row (with the default user).
   - Insert `parsed_refs`, `refs`, `ref_files`, `ref_files_origin`, `ref_extras`, `citations`, `claim_checks`, `activity` rows.
   - Index cache-eligible refs into `ref_cache_keys`.
   - Compute sha256 of every artifact file, write to `ref_files.sha256`.
   - On success: rename `project.json` → `project.json.migrated` (don't delete — safety net for two weeks).
4. Print a summary: N projects migrated, M cache entries indexed, K files hashed.

### 5.2 Hard startup gate

`app.py` startup runs `_check_db_ready()`:
- If `projects/refchecker.sqlite` is missing OR `schema_version < 1`: refuse to start, print *"Run `python -m reference_store migrate` to migrate from v6.1 / v6.4."*
- If any `projects/<slug>/` exists without a corresponding row in `projects`: log a warning (someone added a folder manually).

The gate keeps users from running v6.5 code against half-migrated data and corrupting both project.json AND the DB.

### 5.3 Reverse migration (escape hatch)

`scripts/dump_to_project_json.py` walks the DB and rewrites `project.json` per project. Used when the user wants to roll back to v6.4 or share a project as a single file.

---

## 6. File integrity (sha256)

Every artifact gets a SHA-256 hash computed at insert time and stored on the `ref_files` row. Used for:
- Cache integrity check before serving from cache (§4.6).
- Future deduplication: identical files across projects can hard-link / symlink instead of duplicating bytes.
- Future change detection: a manually-edited file's sha256 stops matching, signaling stale provenance.

Hashing is incremental during migration (one pass) and online during normal downloads (write happens once per download).

---

## 7. UI changes

### 7.1 New tier badge: `cache_hit`

`static/js/app.js` `tierBadge()` adds a new variant:
- Color: teal (distinct from the existing tier palette).
- Label: `Cache hit` with hover-tooltip *"Copied from project &lt;slug&gt; (matched by DOI / arxiv / title+authors)"*.
- The result-card line currently reading "Downloaded via: direct" becomes "Downloaded via: cache_hit (from project finai-ch4)".

### 7.2 Validity report

The download_log section already renders empty when there's no log. For cache_hit refs, the download-source line reads *"Cached from finai-ch4 (matched by DOI 10.1234/foo) on 2026-04-19"*.

### 7.3 Project dashboard

Add a small **"Cache savings"** card to the dashboard panel:
- *"This project: 12 of 38 refs (32%) served from cache"*
- *"Bytes saved: 184 MB"* (sum of cached file sizes)

Optional, low-priority — first ship the cache, then the metric.

### 7.4 Refresh forces a re-download

The "Refresh" button on a single ref bypasses the cache (treats it as a fresh fetch). Otherwise users couldn't break out of a wrong cache hit. This is one extra param to `_do_refresh`: `bypass_cache=True`.

---

## 8. Multi-user wiring (placeholder)

This update lays the groundwork; auth is a separate update.

- Schema: `users` and `projects.user_id` are present and populated.
- App: a `_default_user_id()` helper returns the `local` user's id. Every `create_project` call uses it. No login UI yet.
- Cache search: `find_in_cache` ignores `user_id` entirely — global lookup across all users (per the requirement).
- Future auth: Flask-Login or similar wires a per-request user. `create_project` takes the request user. List/get/delete project filter by `user_id`. Cache search stays global.

---

## 9. Phases (implementation order)

| Phase | Deliverable |
|-------|-------------|
| **A** | Schema + `SqliteReferenceStore` skeleton + migration script + startup gate. App still uses `project_store.py` directly (no shim yet). |
| **B** | `project_store.py` becomes a shim → `reference_store`. All existing call sites work unchanged. Run full test suite. |
| **C** | Implement `find_in_cache` + `index_for_cache` + `_materialize_cache_hit`. Wire into `_process_ref_with_bib_url`, `_do_refresh`, `_do_add`. |
| **D** | UI: `cache_hit` tier badge, validity report tier explainer, refresh-bypass-cache checkbox. |
| **E** | Tests: unit tests for the SQLite store + cache lookup, integration test for materialization, regression tests for shim parity. |
| **F** *(optional)* | Cache-savings dashboard card + sha256 integrity verification + reverse migration script. |

Phase A delivers a no-op data layer change. Phase B is the high-risk swap (touches every persistence call). Phase C delivers the headline feature. Phase D polishes UI. Phases E + F are protective infrastructure.

---

## 10. Test strategy

Test categories:

1. **Schema migration tests** — fixture project.json files → run migrator → assert DB rows match.
2. **Store CRUD tests** — direct against `SqliteReferenceStore`, in-memory `:memory:` DB. Mirror the contract of today's `project_store.py` tests.
3. **Cache lookup tests**:
   - DOI hit beats arxiv hit beats title+authors hit.
   - Ineligible refs (status=found_abstract, verdict=not_matched) are NOT returned.
   - Multi-hit picks most recent.
   - Stale file (sha256 mismatch) drops the entry and returns None.
4. **Materialization tests** — fake source project + cache hit → assert files copied with correct destination bib_key, provenance stamped, no network calls.
5. **Refresh-bypass tests** — refresh on a cached ref triggers real download, bypasses cache.
6. **Shim parity** — every public function on `project_store.py` returns the same shape as before, proven by testing both layers under the same fixtures.

---

## 11. Open questions (please answer before Phase A)

1. **DB filename**: `refchecker.sqlite` at the projects/ root, or `.refchecker/db.sqlite` in a hidden subfolder? *(Default: `refchecker.sqlite` at root — visible, easy to back up.)*
2. **Cache scope tuning**: should cache hits include refs with `ref_match.verdict = unverifiable` (LLM couldn't decide)? *(Default: NO. Only `matched` and `manual_matched`.)*
3. **Bytes-saved metric**: include now in Phase D, or defer to Phase F? *(Default: defer.)*
4. **`tex_content` storage**: keep in `projects.tex_content` column (current behavior), or move out to a file `projects/<slug>/main.tex`? *(Default: keep in DB for now; v7's source-provider work will move it.)*
5. **WAL retention**: SQLite WAL files can grow if not checkpointed. Auto-checkpoint at 1000 pages (the default) is fine; do you want a periodic VACUUM? *(Default: rely on auto.)*
6. **Project deletion**: when a project is deleted, do we also delete its files from the cache index? *(Default: YES — cache hits referencing a deleted project's files would 500.)*
7. **Migration safety net**: keep `project.json.migrated` for 2 weeks, then auto-delete? *(Default: yes, log when deleted.)*

Once these are answered, Phase A can start.
