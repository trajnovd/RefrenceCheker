# RefrenceCheker — v7 plan

**Theme:** restructure project storage into named subfolders, move **all** reference metadata out of `project.json` into a single SQLite database (with a global cross-project reference cache), introduce a clean **data-layer abstraction** (no SQL anywhere outside the database class), add a pluggable **source provider** system (Overleaf first, with GitHub / Google Docs / …​ as future providers) alongside plain uploads, and make the `.bib` file a **first-class editable artifact** — editable inline for quick fixes, openable in a dedicated full-page editor, savable locally or pushable back to the remote provider.

Auth for any remote provider is via environment variable — no per-project token storage.

This plan does **not** change the lookup / download / validity-report pipelines themselves. It changes *where files live*, *where metadata lives*, *how it's accessed*, *how content gets into the project*, and *how the user edits it*.

---

## 1. Goals

1. **Data layer separation.** All persistence — both database access and file-system operations — moves into a dedicated `data_layer/` package. **No SQL exists outside the database class.** No raw `os.path.join(PROJECTS_DIR, ...)` outside the file manager. Other modules use the public APIs only.

2. **Predictable on-disk layout.** Each project folder has a clear, predictable structure — easy to inspect, easy to zip, easy to back up:
   - `source/` — the LaTeX and bib files the user works on
   - `references/` — every artifact produced by the lookup pipeline (PDFs, HTML, abstracts, markdown, pasted/uploaded files)
   - `validity-report/` — the generated HTML report and its bundle
   - root — small marker files only (no `project.json` anymore — all metadata lives in the DB)

3. **Single SQLite database** at `projects/refchecker.sqlite` is the authoritative store for everything that isn't a content file: project metadata, parsed bib entries, lookup results, file inventory, provenance, claim-check verdicts, citations, activity log, and the global cache index.

4. **Global reference cache.** Before downloading a paper for any project, look up whether *any* project (across all users) has already downloaded a verified copy. Match by DOI, then arXiv ID, then `(title, authors)`. On hit, copy the artifacts from the source project's folder into the new one — no network I/O.

5. **Multi-user from day one** at the schema level. Every project belongs to a user. Cache search is *global* across users (per requirement). No login UI yet — a single default user is provisioned by migration.

6. **Pluggable source providers.** v7 ships with two:
   - **Upload** — user uploads `.tex` and `.bib`.
   - **Overleaf** — user gives an Overleaf project ID; we clone via git, let them pick the tex+bib files from the worktree, and copy those into `source/`. They can later pull updates and push edits back.

   The provider layer is designed so **GitHub** and **Google Docs** can be added later without touching the rest of the app.

7. **Editable `.bib`** using the existing reference-check page editor (no new editor component). Saving offers two destinations: write to local `source/<bib>`, or push back to the remote provider. The `.tex` editor stays exactly as it is today.

8. **Re-download all references** — a project-level action with manual-source confirmation modal. Now also benefits from the cache: refs that other projects have are served instantly.

9. **One-time migration** for existing flat-layout projects: moves files into the new subfolders AND moves `project.json` content into the DB. Idempotent. Hard startup gate: the app refuses to run against un-migrated projects.

---

## 2. New folder layout

```
projects/
├── refchecker.sqlite                    # ALL metadata for ALL projects
├── refchecker.sqlite-wal                # WAL log
├── refchecker.sqlite-shm                # shared memory
└── <slug>/
    ├── source/                          # the user-facing tex + bib, regardless of origin
    │   ├── main.tex
    │   ├── refs.bib
    │   └── .provider/                   # hidden; only present when source.kind != "upload"
    │       └── overleaf/                # the git worktree, or whatever transport state
    │           ├── .git/
    │           └── …
    ├── references/
    │   ├── ref_key_pdf.pdf
    │   ├── ref_key_page.html
    │   ├── ref_key_abstract.txt
    │   ├── ref_key.md
    │   └── _manual_backup/              # backup of overwritten manual files (created on demand)
    └── validity-report/
        ├── <slug>_report.html
        └── report.zip                   # HTML + references/ bundle
```

Notes:

- **No `project.json` anywhere.** Every byte of metadata that used to live there is in the DB.
- `source/` contains the same two user-facing files no matter where they came from. Provider-specific transport state (a git worktree for Overleaf/GitHub, a Drive cache for Google Docs) lives in a hidden subfolder `source/.provider/<name>/`. Co-locating it under `source/` keeps everything that *originated from the source* in one place; making it dot-prefixed keeps it out of the user's way.
- `source/.provider/` is hidden (won't clutter the bib/tex pickers, won't get walked when scanning for tex/bib candidates), and is excluded from the report zip.
- The `references/` folder replaces today's flat dump of `*_pdf.pdf`, `*_page.html`, etc. Filenames stay the same; only the parent directory changes.
- `validity-report/` is built per run.

---

## 3. Data layer — `data_layer/` package

A dedicated package owns **all persistence**. Two clean concerns: the database (rows) and the file system (bytes). Cache orchestration lives here too because it spans both. Nothing else in the app touches SQL or path-joins.

### 3.1 Package structure

```
data_layer/
├── __init__.py           # public API: get_store(), get_files(), get_cache()
├── reference_store.py    # ABC + SqliteReferenceStore — the ONLY place with SQL
├── file_manager.py       # path helpers, file copy/move/delete, sha256 — NO SQL
├── cache.py              # cache lookup + materialization — uses store + files, NO SQL
├── schema.sql            # DDL applied by reference_store on first open
├── exceptions.py         # ProjectNotFound, RefNotFound, ConcurrencyError, …
└── migrations/
    ├── __init__.py
    ├── v001_initial.py   # baseline schema
    └── v002_*.py         # future migrations
```

### 3.2 The hard rule about SQL

> **All `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, and `ALTER` statements live inside `data_layer/reference_store.py` (and its future Postgres sibling). Nowhere else.**

- `cache.py` and `file_manager.py` call store methods like `store.find_cache_candidates(doi=...)` — they never write SQL.
- The store's public methods take and return Python dicts / dataclasses. Callers never see `Cursor`, `Row`, or `?` placeholders.
- Tests for query logic live in `tests/test_reference_store.py`. Tests for higher layers mock the store ABC.

A lint check (ripgrep in CI) enforces this: `rg -n '\\b(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE)\\b' --type py | grep -v '^data_layer/'` must return empty. The exclusion covers the whole `data_layer/` package (store, migrations, future Postgres sibling) — same pattern as §12.

### 3.3 `ReferenceStore` ABC

```python
# data_layer/reference_store.py — public API only (impls below)

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class CacheCandidate:
    """Returned by find_cache_candidates. The cache module decides which to
    materialize after verifying file integrity."""
    ref_id: int
    project_slug: str
    matched_by: str            # 'doi' | 'arxiv' | 'title_authors'
    result: dict               # full result dict (today's shape)
    files: dict                # {'pdf': 'smith_pdf.pdf', ...}
    files_origin: dict
    updated_at: str


class ReferenceStore(ABC):
    """Backend-agnostic storage for projects + references.

    Concrete implementations: SqliteReferenceStore (v7), PostgresReferenceStore
    (future enterprise). All SQL lives inside the implementation; callers see
    only Python dicts.
    """

    # ---------- users ----------
    @abstractmethod
    def get_or_create_user(self, email: str, name: str = None) -> int: ...
    @abstractmethod
    def default_user_id(self) -> int: ...

    # ---------- projects ----------
    @abstractmethod
    def create_project(self, name: str, user_id: int, source_kind: str = "upload") -> dict: ...
    @abstractmethod
    def get_project(self, slug: str) -> Optional[dict]: ...
    @abstractmethod
    def list_projects(self, user_id: Optional[int] = None) -> list[dict]: ...
    @abstractmethod
    def delete_project(self, slug: str) -> bool: ...
    @abstractmethod
    def update_project(self, slug: str, **fields) -> None: ...
    @abstractmethod
    def set_source_state(self, slug: str, kind: str, state: dict) -> None: ...
    @abstractmethod
    def get_source_state(self, slug: str) -> dict: ...

    # ---------- parsed refs (pre-lookup bib entries) ----------
    @abstractmethod
    def save_parsed_refs(self, slug: str, parsed_refs: list[dict]) -> None: ...
    @abstractmethod
    def add_parsed_ref(self, slug: str, ref: dict) -> bool: ...
    @abstractmethod
    def get_parsed_ref(self, slug: str, bib_key: str) -> Optional[dict]: ...
    @abstractmethod
    def list_parsed_refs(self, slug: str) -> list[dict]: ...

    # ---------- results (post-lookup) ----------
    @abstractmethod
    def save_result(self, slug: str, result: dict) -> None: ...
    @abstractmethod
    def save_results_batch(self, slug: str, results: list[dict]) -> None: ...
    @abstractmethod
    def get_result(self, slug: str, bib_key: str) -> Optional[dict]: ...
    @abstractmethod
    def list_results(self, slug: str) -> list[dict]: ...
    @abstractmethod
    def delete_result(self, slug: str, bib_key: str) -> None: ...

    # ---------- ref_match (identity verification) ----------
    @abstractmethod
    def save_ref_match(self, slug: str, bib_key: str, match: dict) -> bool:
        """Persist the full match dict in ref_extras.ref_match_json AND mirror
        match['verdict'] + match.get('confidence') into refs.ref_match_verdict /
        refs.ref_match_confidence (the two promoted columns, §4.2). Both writes
        happen in a single transaction so the columns and the JSON never drift.
        Triggers ref_cache_keys re-indexing via index_for_cache."""
    @abstractmethod
    def get_ref_match(self, slug: str, bib_key: str) -> Optional[dict]: ...

    # ---------- citations + claim checks ----------
    @abstractmethod
    def save_citations(self, slug: str, citations: list[dict]) -> None: ...
    @abstractmethod
    def list_citations(self, slug: str) -> list[dict]: ...
    @abstractmethod
    def save_claim_check(self, slug: str, cache_key: str, verdict: dict) -> None: ...
    @abstractmethod
    def get_claim_check(self, slug: str, cache_key: str) -> Optional[dict]: ...
    @abstractmethod
    def list_claim_checks(self, slug: str) -> dict: ...
    @abstractmethod
    def set_citation_check_key(self, slug: str, citation_index: int, cache_key: Optional[str]) -> None: ...

    # ---------- activity log + UI state ----------
    @abstractmethod
    def add_activity(self, slug: str, activity_type: str, message: str, target: str = None) -> None: ...
    @abstractmethod
    def list_activity(self, slug: str, limit: int = 50) -> list[dict]: ...
    @abstractmethod
    def set_last_viewed_citation(self, slug: str, citation_index: int) -> None: ...
    @abstractmethod
    def get_last_viewed_citation(self, slug: str) -> int: ...

    # ---------- telemetry ----------
    @abstractmethod
    def compute_download_stats(self, slug: str) -> Optional[dict]: ...

    # ---------- global cache (cross-project, cross-user) ----------
    @abstractmethod
    def find_cache_candidates(self, *, doi: str = None, arxiv_id: str = None,
                              title: str = None, authors: str = None
                              ) -> list[CacheCandidate]: ...
    @abstractmethod
    def index_for_cache(self, slug: str, bib_key: str) -> None:
        """Insert into ref_cache_keys when this ref becomes cacheable
        (status + ref_match qualified). Called from save_result and
        save_ref_match. Removes from cache when ref becomes ineligible."""
    @abstractmethod
    def drop_cache_keys(self, slug: str, bib_key: str) -> None:
        """Force-remove this ref from the global cache (e.g. file deleted)."""
    @abstractmethod
    def get_ref_file_hashes(self, ref_id: int) -> dict[str, str]:
        """Return {filetype: sha256} for a ref's artifacts, used by cache.py
        to verify integrity before materializing a cache hit."""

    # ---------- migration / schema ----------
    @abstractmethod
    def schema_version(self) -> int: ...
    @abstractmethod
    def apply_migrations(self) -> None: ...
```

### 3.4 `FileManager` — the bytes side

```python
# data_layer/file_manager.py — pure file/path operations. NO SQL. NO database imports.

from typing import Optional

class FileManager:
    """Owns the projects/ directory tree. Every path-join, file copy, move,
    or delete the application performs goes through here."""

    def __init__(self, projects_root: str): ...

    # ----- project paths -----
    def project_dir(self, slug: str) -> str: ...
    def source_dir(self, slug: str) -> str: ...
    def references_dir(self, slug: str) -> str: ...
    def report_dir(self, slug: str) -> str: ...
    def provider_state_dir(self, slug: str, provider_name: str) -> str: ...

    def source_path(self, slug: str, filename: str) -> str: ...
    def reference_path(self, slug: str, filename: str) -> str: ...
    def report_path(self, slug: str, filename: str) -> str: ...

    # ----- lifecycle -----
    def ensure_project_dirs(self, slug: str) -> None: ...
    def delete_project_dir(self, slug: str) -> bool: ...

    # ----- listings (skip dot-prefixed, used by pickers) -----
    def list_source_files(self, slug: str, *, exts: tuple = (".tex", ".bib")) -> list[str]: ...
    def list_reference_files(self, slug: str) -> list[str]: ...

    # ----- safe file ops (atomic write, mtime check, sha256) -----
    def read_text(self, path: str) -> tuple[str, float]: ...               # (content, mtime)
    def write_text_atomic(self, path: str, content: str,
                          expected_mtime: Optional[float] = None) -> float:
        """Atomic write via tmp+rename. If expected_mtime given and file
        changed since, raise ConcurrencyError."""
    def copy_file(self, src: str, dst: str) -> None: ...
    def move_to_backup(self, src: str, backup_dir: str) -> str: ...
    def sha256(self, path: str) -> str: ...
    def file_size(self, path: str) -> int: ...
    def exists(self, path: str) -> bool: ...

    # ----- legacy migration helpers (one-shot script use) -----
    def migrate_legacy_layout(self, slug: str) -> dict:
        """Move flat-layout files into source/ + references/. Idempotent."""
```

`FileManager` is constructed once at startup with `PROJECTS_DIR` and exposed via `data_layer.get_files()`. Tests pass a `tmp_path`-rooted instance.

### 3.5 `cache.py` — the orchestration glue

```python
# data_layer/cache.py — uses store + files. NO SQL.

from data_layer import get_store, get_files
from data_layer.exceptions import CacheStaleError

def lookup_and_materialize(slug: str, ref: dict) -> Optional[dict]:
    """Try the global cache for this ref. On hit, copy files into the
    destination project and return a complete result dict (no network I/O).
    Returns None when no usable cache entry exists."""
    store = get_store()
    files = get_files()

    candidates = store.find_cache_candidates(
        doi=ref.get("doi"),
        arxiv_id=ref.get("arxiv_id"),
        title=ref.get("title"),
        authors=ref.get("authors"),
    )
    for cand in candidates:
        try:
            return _materialize(slug, ref, cand, files, store)
        except CacheStaleError:
            # Source files vanished or hash-mismatched — drop the index entry
            # and try the next candidate.
            store.drop_cache_keys(cand.project_slug, cand.result["bib_key"])
            continue
    return None


def _materialize(slug, ref, cand, files, store) -> dict:
    """Copy files + build result. Stamps tier=cache_hit on every artifact."""
    src_dir = files.references_dir(cand.project_slug)
    dst_dir = files.references_dir(slug)
    safe_key = _safe_filename(ref["bib_key"])

    expected_hashes = store.get_ref_file_hashes(cand.ref_id)  # {filetype: sha256}
    new_files = {}
    for filetype, src_filename in cand.files.items():
        src = files.reference_path(cand.project_slug, src_filename)
        if not files.exists(src):
            raise CacheStaleError(f"missing {src}")
        # sha256 verification is mandatory on every cache hit (§14, decision 18).
        # ~50 ms for a 5 MB PDF beats serving a corrupted artifact.
        expected = expected_hashes.get(filetype)
        if expected and files.sha256(src) != expected:
            raise CacheStaleError(f"sha256 mismatch on {src}")
        suffix = _file_suffix(filetype)
        dst_filename = safe_key + suffix
        files.copy_file(src, files.reference_path(slug, dst_filename))
        new_files[filetype] = dst_filename

    result = dict(cand.result)
    result["bib_key"] = ref["bib_key"]
    result["raw_bib"] = ref.get("raw_bib")
    result["files"] = new_files
    result["files_origin"] = {
        ft: {"tier": "cache_hit",
             "source_project": cand.project_slug,
             "source_ref_id": cand.ref_id,
             "matched_by": cand.matched_by,
             "captured_at": _now_iso()}
        for ft in new_files
    }
    return result
```

### 3.6 Public API at the package boundary

```python
# data_layer/__init__.py
from .reference_store import ReferenceStore, SqliteReferenceStore, CacheCandidate
from .file_manager import FileManager
from .exceptions import ProjectNotFound, RefNotFound, ConcurrencyError, CacheStaleError
from .cache import lookup_and_materialize

_store: ReferenceStore | None = None
_files: FileManager | None = None

def get_store() -> ReferenceStore: ...
def get_files() -> FileManager: ...
def init(projects_root: str) -> None:
    """Called once at app startup. Constructs store + files, applies any
    pending migrations, raises if the schema is too old."""
```

Other modules import only `from data_layer import get_store, get_files, lookup_and_materialize`. They never reach into `data_layer.reference_store` directly.

---

## 4. Database schema

### 4.1 Tables (`data_layer/schema.sql`, applied by `apply_migrations()`)

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
  source_kind TEXT NOT NULL DEFAULT 'upload',  -- upload / overleaf / github / google_docs
  source_state_json TEXT,           -- provider-specific state (project_id, last_synced_commit, …)
  bib_filename TEXT,                -- name of the file in source/
  tex_filename TEXT,
  layout_version INTEGER NOT NULL DEFAULT 2,   -- v7 = 2 (v6 flat layout = 1)
  last_viewed_citation INTEGER DEFAULT 0
);
CREATE INDEX idx_projects_user ON projects(user_id);

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
  all_fields_json TEXT,
  status TEXT,                      -- e.g. insufficient_data
  manually_added INTEGER DEFAULT 0,
  UNIQUE(project_id, bib_key)
);
CREATE INDEX idx_parsed_refs_project ON parsed_refs(project_id);

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
  sources_json TEXT,
  status TEXT NOT NULL,
  error TEXT,
  bib_url_failure_json TEXT,
  raw_bib TEXT,
  url_source_only INTEGER DEFAULT 0,
  -- ref_match promotion: these two are queried directly (cache-eligibility
  -- predicate in §8.3, future "low-confidence refs" reports). Everything else
  -- about ref_match — evidence dict, manual flag, timestamps — stays in
  -- ref_extras.ref_match_json. See §4.2 for the rationale.
  ref_match_verdict TEXT,           -- matched / not_matched / manual_matched / NULL
  ref_match_confidence REAL,        -- 0.0–1.0 or NULL
  -- bookkeeping
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, bib_key)
);
CREATE INDEX idx_refs_project ON refs(project_id);
CREATE INDEX idx_refs_doi ON refs(doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_refs_arxiv ON refs(arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE INDEX idx_refs_verdict ON refs(ref_match_verdict) WHERE ref_match_verdict IS NOT NULL;

CREATE TABLE ref_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref_id INTEGER NOT NULL REFERENCES refs(id) ON DELETE CASCADE,
  filetype TEXT NOT NULL,           -- pdf / page / abstract / md / pasted
  filename TEXT NOT NULL,
  size_bytes INTEGER,
  sha256 TEXT,
  UNIQUE(ref_id, filetype)
);
CREATE INDEX idx_ref_files_ref ON ref_files(ref_id);
CREATE INDEX idx_ref_files_sha ON ref_files(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE ref_files_origin (
  ref_file_id INTEGER PRIMARY KEY REFERENCES ref_files(id) ON DELETE CASCADE,
  tier TEXT NOT NULL,               -- direct / oa_fallbacks / wayback / curl_cffi /
                                    -- playwright / cache_hit / manual_*
  url TEXT,
  host TEXT,
  captured_at TEXT,
  -- cache_hit-specific fields (NULL otherwise)
  source_project TEXT,
  source_ref_id INTEGER,
  matched_by TEXT
);

CREATE TABLE ref_extras (
  ref_id INTEGER PRIMARY KEY REFERENCES refs(id) ON DELETE CASCADE,
  download_log_json TEXT,
  ref_match_json TEXT,
  pdf_url_fallbacks_json TEXT
);

-- Cache lookup index. Populated on save_result/save_ref_match when ref is
-- cacheable (status + ref_match qualified).
CREATE TABLE ref_cache_keys (
  ref_id INTEGER NOT NULL REFERENCES refs(id) ON DELETE CASCADE,
  key_type TEXT NOT NULL,           -- 'doi' | 'arxiv' | 'title_authors'
  key_value TEXT NOT NULL,
  PRIMARY KEY (ref_id, key_type, key_value)
);
CREATE INDEX idx_cache_keys_lookup ON ref_cache_keys(key_type, key_value);

CREATE TABLE claim_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  cache_key TEXT NOT NULL,          -- citation_index or paragraph hash
  verdict TEXT,
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
  ord INTEGER NOT NULL              -- preserves .tex order in the UI list
);
CREATE INDEX idx_citations_project ON citations(project_id);

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

### 4.2 Why JSON columns for `_extras` (with two promoted exceptions)

`download_log`, `ref_match.evidence`, and `pdf_url_fallbacks` are small (under 5 KB), only read when rendering the validity report or the review pane, and have unstable schemas (new tier names, new ref_match evidence keys ship every few weeks). Normalizing them into separate tables would mean writing a migration every time we add a tier — for query power we don't use. The decision rule:

> **JSON when:** field set evolves frequently AND always read with parent AND never used as a query predicate AND stays small AND has nested structure that wouldn't normalize cleanly anyway.

All three blobs satisfy every criterion, so they live in `ref_extras` as JSON.

**The two promoted exceptions** — `ref_match.verdict` and `ref_match.confidence` — are stable enums/scalars used as query predicates:

- `verdict` gates cache eligibility (§8.3) — checked on every `save_ref_match`.
- `confidence` powers operational visibility (e.g. *"refs below 0.7 across all projects"*) and could anchor future ranking logic.

Promoting them to columns on `refs` (with an index on `verdict`) costs one cheap migration up front and ~10 LOC in the store, in exchange for clean SQL predicates instead of `json_extract` everywhere. The full `ref_match` dict — `evidence`, `manual`, timestamps, future fields — still lives in `ref_extras.ref_match_json`. The store is responsible for keeping the two stores consistent inside `save_ref_match` (single transaction).

### 4.3 What stays on disk

Everything in §2's tree under `<slug>/` is on disk. The `.tex` and `.bib` files live as files in `source/`; the DB only records their filenames (in `projects.tex_filename` / `projects.bib_filename`). Everything else — the parsed bib structure, the lookup results, the citations, the claim checks, the activity log — lives in the DB.

---

## 5. Source providers (pluggable)

Source = "where the canonical tex+bib live, and how we sync with that location". Upload, Overleaf, GitHub, Google Docs all answer the same questions: how do we get the files in, can we push edits back, and how do we know if the remote moved.

### 5.1 The provider interface — abstract base class

`SourceProvider` is an `abc.ABC`, not a Protocol. Reasons: (1) we want concrete providers to fail loudly at import time if they forget a method, not silently at runtime; (2) the base class can carry shared behavior (every provider needs to ensure `source_dir` exists via `FileManager`, persist its state via `ReferenceStore`, and expose `is_configured`); (3) `isinstance(p, SourceProvider)` works for registration validation and tests.

```python
# source_providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from data_layer import get_store, get_files


@dataclass(frozen=True)
class ImportResult:
    """Returned by import_into. Frontend uses `candidates` to drive the
    tex/bib picker. `state` is what gets persisted via store.set_source_state."""
    candidates: dict           # {"tex": [str], "bib": [str]}
    state: dict                # provider-specific metadata


@dataclass(frozen=True)
class SyncStatus:
    dirty: bool
    ahead: int
    behind: int
    last_synced_at: str | None


class SourceProviderError(Exception): ...
class AuthError(SourceProviderError): ...
class DivergedError(SourceProviderError): ...
class NotSupported(SourceProviderError): ...


class SourceProvider(ABC):
    """Abstract base. One instance per provider, shared across all projects.
    Methods are stateless w.r.t. the instance — all per-project state lives
    in the database (via ReferenceStore.set_source_state) and on disk under
    the project's source/.provider/<name>/."""

    name: ClassVar[str]
    display_name: ClassVar[str]
    supports_pull: ClassVar[bool] = False
    supports_push: ClassVar[bool] = False
    env_var: ClassVar[str | None] = None

    def is_configured(self) -> bool:
        if self.env_var is None:
            return True
        import os
        return bool(os.environ.get(self.env_var))

    def describe(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_pull": self.supports_pull,
            "supports_push": self.supports_push,
            "env_var": self.env_var,
            "is_configured": self.is_configured(),
        }

    def _persist_state(self, slug: str, state: dict) -> None:
        """Write provider state to the database via the store."""
        get_store().set_source_state(slug, self.name, state)

    @abstractmethod
    def import_into(self, slug: str, params: dict) -> ImportResult: ...

    @abstractmethod
    def select(self, slug: str, tex_path: str, bib_path: str) -> None: ...

    def pull(self, slug: str) -> dict:
        raise NotSupported(f"{self.name} does not support pull")

    def push(self, slug: str, files: list[str], message: str) -> dict:
        raise NotSupported(f"{self.name} does not support push")

    def status(self, slug: str) -> SyncStatus:
        return SyncStatus(dirty=False, ahead=0, behind=0, last_synced_at=None)
```

### 5.2 Concrete providers

```python
# source_providers/upload.py
class UploadProvider(SourceProvider):
    name = "upload"
    display_name = "Upload files"

    def is_configured(self) -> bool: return True

    def import_into(self, slug, params) -> ImportResult:
        # params = {"tex_file": FileStorage, "bib_file": FileStorage}
        # Save into source_dir(slug) via FileManager, then return what was saved.
        ...

    def select(self, slug, tex_path, bib_path) -> None:
        self._persist_state(slug, {"tex_filename": tex_path,
                                    "bib_filename": bib_path})
```

```python
# source_providers/overleaf.py
class OverleafProvider(SourceProvider):
    name = "overleaf"
    display_name = "Overleaf"
    supports_pull = True
    supports_push = True
    env_var = "OVERLEAF_TOKEN"

    def import_into(self, slug, params) -> ImportResult:
        # 1. _git.clone(project_id, files.provider_state_dir(slug, "overleaf"))
        # 2. walk for *.tex / *.bib (skip .git)
        # 3. return ImportResult(candidates=..., state={"project_id": ..., "cloned_at": ...})
        ...

    def select(self, slug, tex_path, bib_path) -> None: ...
    def pull(self, slug) -> dict: ...
    def push(self, slug, files, message) -> dict: ...
    def status(self, slug) -> SyncStatus: ...
```

### 5.3 Registry

```python
# source_providers/__init__.py
from .base import SourceProvider, ImportResult, SyncStatus, \
    AuthError, DivergedError, NotSupported
from .upload import UploadProvider
from .overleaf import OverleafProvider

_REGISTRY: dict[str, SourceProvider] = {}

def register(provider: SourceProvider) -> None:
    assert isinstance(provider, SourceProvider)
    if provider.name in _REGISTRY:
        raise ValueError(f"Provider {provider.name!r} already registered")
    _REGISTRY[provider.name] = provider

def get(name: str) -> SourceProvider: ...
def all_providers() -> list[SourceProvider]: ...

register(UploadProvider())
register(OverleafProvider())
```

### 5.4 Using providers across the application

The provider classes are the **only** way the app interacts with source files for import/sync. There is no `if kind == "overleaf"` anywhere in `app.py` — every branch goes through `get(kind).<method>()`.

| Call site | What it calls |
|---|---|
| `GET /api/providers` | `[p.describe() for p in source_providers.all_providers()]` |
| `POST /api/projects` | `provider = source_providers.get(name); result = provider.import_into(slug, params)` |
| `POST /api/projects/<slug>/source/select` | `provider.select(slug, tex, bib)` |
| `POST /api/projects/<slug>/source/pull` | `provider.pull(slug)` (button only when `supports_pull`) |
| `POST /api/projects/<slug>/source/push` | `provider.push(slug, files, message)` (button only when `supports_push`) |
| `GET  /api/projects/<slug>/source/status` | `provider.status(slug)` |

Helper used everywhere we need the provider for a given project:

```python
def provider_for(slug: str) -> SourceProvider:
    src = get_store().get_source_state(slug)
    return source_providers.get(src.get("kind", "upload"))
```

Tests can monkey-patch `_REGISTRY` to inject fakes (`FakeProvider(SourceProvider)`).

### 5.5 Token / auth strategy

| Provider | Env var |
|---|---|
| Overleaf | `OVERLEAF_TOKEN` |
| GitHub | `GITHUB_TOKEN` (PAT with `repo` scope) |
| Google Docs | `GOOGLE_APPLICATION_CREDENTIALS` |

The provider owns its env-var name and `is_configured()` check. UI disables the corresponding tab with *"set `OVERLEAF_TOKEN` and restart"* when False. No tokens ever land in the DB or on disk.

### 5.6 Overleaf provider (v7 ships this)

`source_providers/overleaf.py` — thin wrapper around `git` via `subprocess`. No `gitpython` dep.

All `git` invocations run with `env = {**os.environ, "GIT_ASKPASS": <helper>, "GIT_TERMINAL_PROMPT": "0"}`. The askpass helper prints `$OVERLEAF_TOKEN`, so the token never lands in the URL, the reflog, or `ps`.

**Constraints inherited from Overleaf** (surface in the import modal): single `master` branch, no LFS, file renames become delete+create, "don't mix git with online track changes".

### 5.7 GitHub / Google Docs (sketch — not implemented in v7)

Same `SourceProvider` ABC. GitHub reuses the git subprocess machinery (factor `_git_helpers.py` once the second git provider exists). Google Docs uses the Drive API, demonstrating the abstraction holds for non-git transports.

### 5.8 Flows (provider-agnostic)

**Import:**
1. User picks a provider tab; backend calls `provider.import_into(slug, form_params)`.
2. UI shows the picker built from `result.candidates`. User picks tex+bib.
3. Backend calls `provider.select(slug, tex, bib)` → copies files into `source/` via `FileManager`, persists state via `store.set_source_state`.

**Pull:** "Pull from <display_name>" button (only when `supports_pull`). Calls `provider.pull(slug)`. On change, UI offers "re-run lookup".

**Push:** "Push to <display_name>" button (only when `supports_push`). Diffs `source/<tex>` and `source/<bib>` against last-synced; modal with file list + commit message. On confirm: `provider.push(slug, [tex, bib], message)`.

---

## 6. Editable `.bib` files

The bib file isn't just an input we read once — users will fix typos, add a missing `year`, repair a malformed entry. v7 makes the bib editable using **the same editor component already on the reference-check page**. There is no new editor, no new page, no new library.

### 6.1 What changes

- The reference-check page (View 4: Citation Review) already provides a full `.tex` editor with save-back-to-disk via `PUT /api/projects/<slug>/save-tex`. v7 adds a sibling tab/view for `source/<bib>` that uses the same component with the same controls. The `.tex` editor itself is unchanged — it already does what we'd want for `.tex`. v7 only extends the same machinery to `.bib`.
- The on-disk `source/<bib>` file is the single source of truth. The parsed bib structure in the DB (`parsed_refs` table) is regenerated from the file on save, not the other way round.
- Save offers two destinations:
  - **Save locally** — writes `source/<bib>` via `FileManager.write_text_atomic`. Always available.
  - **Save & push to <provider>** — writes locally, then runs `provider.push(slug, [bib], message)`. Only shown when `provider.supports_push`.
- After save, any cached lookup results whose `raw_bib` changed are invalidated and the affected references re-run through the lookup pipeline.
- Concurrency: `FileManager.write_text_atomic` checks `expected_mtime` and raises `ConcurrencyError` if the file changed underneath. The user gets "reload" or "force save".

### 6.2 New backend endpoints

- `GET  /api/projects/<slug>/bib` → `{content, mtime}` (via `FileManager.read_text` + `ReferenceStore.get_source_state` for the filename)
- `PUT  /api/projects/<slug>/bib` → body `{content, expected_mtime}`. Saves to `source/<bib>`, re-parses, calls `store.save_parsed_refs`. Returns the new mtime + list of bib_keys whose entries changed.
- `POST /api/projects/<slug>/bib/push` → after a local save, push to the provider.

The `.tex` file already has its write endpoint (`PUT /api/projects/<slug>/save-tex`) and its editor in View 4 (Citation Review); v7 reuses both unchanged. Phase I adds a sibling `POST /api/projects/<slug>/tex/push` so the existing `.tex` editor gains a Save-and-push-to-provider button mirroring the new `.bib` flow (§14, decision 19).

---

## 7. Re-download all references from the current `.bib`

A project-level **Re-download all references** action on the project page. Useful after a bulk bib edit, after a `pull` from Overleaf, or simply because some downloads have gone stale. **Now also benefits from the global cache** — refs other projects already have are served instantly.

### 7.1 What it does

Walks every entry in the current `source/<bib>` and re-runs the lookup + download pipeline for each one. Cache lookup runs first (§9); on miss, the existing pipeline runs. Existing files in `references/` are overwritten. Status panel and bib tab refresh as entries complete (existing SSE flow).

### 7.2 The "manual references" question

Some references have been manually curated (uploaded PDF, pasted abstract, manual URL). These are tagged via `files_origin.<filetype>.tier == "manual_*"` and `ref_match.manual=True`. Re-downloading these by default would silently destroy work the user explicitly did. So when **Re-download all references** is clicked, a confirmation modal asks:

> You have **N** references with manually-set sources.
>
> - **Re-download everything** — overwrite manual sources too. Originals kept under `references/_manual_backup/`.
> - **Skip manual references** — only re-download the **M** automatically-fetched references.
> - **Cancel**

If N is zero, the modal is skipped.

### 7.3 Backend

- `GET  /api/projects/<slug>/refs/manual-count` → `{manual: N, auto: M}`
- `POST /api/projects/<slug>/refs/redownload` body `{include_manual: bool, message?: str}`
- The endpoint partitions results into `manual` vs `auto` based on `files_origin` / `ref_match.manual` (queried via `store.list_results`). For each entry to re-process: clear current `files_origin`, back up any existing manual file via `FileManager.move_to_backup`, route through the same single-ref re-lookup. SSE progress reuses the existing channel.

### 7.4 Frontend

A **Re-download all references** button next to the existing Refresh / Verification table / Validity Report buttons. Click → fetch `/refs/manual-count` → show modal → on confirm, hit the endpoint and switch to the existing in-progress UI.

---

## 8. Global reference cache

Headline feature: never download the same paper twice across all projects.

### 8.1 When the cache is consulted

Call site: `_process_ref_with_bib_url` in `app.py` (and `_do_refresh`, `_do_add`, the redownload-all path), **before** `pre_download_bib_url`.

```python
from data_layer import lookup_and_materialize

def _process_ref_with_bib_url(ref):
    if not bypass_cache:
        cached = lookup_and_materialize(slug, ref)
        if cached is not None:
            return cached
    # ... existing pre-fetch + lookup pipeline unchanged ...
```

Refresh on a single ref bypasses the cache (`bypass_cache=True`) so the user can break out of a wrong cache hit.

### 8.2 Match keys (priority order)

1. **DOI** exact match (strongest).
2. **arXiv ID** exact match.
3. **`(title, authors)`** match — both required, normalized as below.

```python
def _title_author_key(title, authors):
    """Title: lowercased, alphanumeric+space only, collapsed whitespace.
    Authors: extract last names (reuse arxiv_client._last_names), sort, join '|'.
    """
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    last_names = sorted(_last_names(authors))
    return f"{t}::{('|'.join(last_names))}"
```

Title-only (no authors) does NOT enter the cache. Too risky.

### 8.3 Cache eligibility (what gets indexed)

A ref enters `ref_cache_keys` when:
- `refs.status ∈ {found_pdf, found_web_page}`
- `refs.ref_match_verdict ∈ {matched, manual_matched}` *(promoted column from §4.2 — no JSON parsing on the hot path)*
- At least one of (doi, arxiv_id, (title AND authors)) is set

Re-indexing happens automatically inside `store.save_result` and `store.save_ref_match`. The eligibility query is a plain `SELECT id FROM refs WHERE ref_match_verdict IN ('matched','manual_matched') AND status IN ('found_pdf','found_web_page') AND id=?` — no `json_extract`. A ref that flips back to `not_matched` is removed via `DELETE FROM ref_cache_keys WHERE ref_id=?`.

### 8.4 Materialization (cache hit handling)

`data_layer/cache.py:lookup_and_materialize` (§3.5) does it. Steps:

1. Query `store.find_cache_candidates` for matching `ref_cache_keys` rows; return ordered by `updated_at DESC`.
2. For each candidate: verify each source file exists on disk via `FileManager.exists` AND that `FileManager.sha256(src)` matches the `ref_files.sha256` recorded at indexing time. The hash check is mandatory (per §14, decision 18) — ~50 ms for a 5 MB PDF beats serving a corrupted artifact.
3. On verification pass: `FileManager.copy_file` each artifact from `references_dir(source_slug)` to `references_dir(dest_slug)`, renaming to the destination's `bib_key`. Build a result dict with `files_origin.tier="cache_hit"` + source provenance fields. Return.
4. On verification fail (missing file OR sha256 mismatch): `store.drop_cache_keys(source_slug, source_bib_key)` and try the next candidate. Falls through to `None` if all candidates are stale.

### 8.5 Multi-user behavior

`store.find_cache_candidates` ignores `user_id` entirely — global lookup across all users. Per requirement.

### 8.6 UI surface

- New tier badge `cache_hit` (teal). Hover-tooltip: *"Copied from project &lt;slug&gt; (matched by DOI / arxiv / title+authors)"*.
- Validity report tier explainer adds a row for `cache_hit`.
- Optional dashboard card *"Cache savings — 12 of 38 refs (32%) served from cache, ~184 MB saved"* (Phase F, optional).
- Refresh on a single ref shows a subtle *"forcing fresh download"* note.

---

## 9. Multi-user wiring (placeholder)

This update lays the groundwork; auth is a separate update.

- Schema: `users` and `projects.user_id` are present and populated.
- App: `data_layer.get_store().default_user_id()` returns the seed user's id. Every `create_project` call uses it. No login UI yet.
- Cache search: `store.find_cache_candidates` is global (ignores `user_id`).
- Future auth: Flask-Login or similar wires a per-request user. `create_project` takes the request user. List/get/delete project filter by `user_id`. Cache search stays global.

---

## 10. Migration of existing projects

A flat-layout project with `project.json` needs to become a structured project whose metadata lives in the DB. **One-time CLI script**, idempotent, hard startup gate.

### 10.1 The script: `scripts/migrate_to_v7.py`

Runnable as `python scripts/migrate_to_v7.py` or `python -m data_layer.migrate`.

Steps:

1. **Open / create** `projects/refchecker.sqlite`. Apply schema if `schema_version` is absent.
2. **Insert default user** (`local`) if `users` is empty. Capture its id.
3. **For each `projects/<slug>/`**:
   - Skip if a `projects` row with this slug AND `layout_version >= 2` already exists (idempotent).
   - **Backup `project.json`** to `project.json.pre_v7.bak`.
   - **Create the new dirs**: `source/`, `references/`, `validity-report/` if missing.
   - **Move files**:
     - `*.tex`, `*.bib` from project root → `source/`.
     - `*_pdf.pdf`, `*_page.html`, `*_abstract.txt`, `*_pasted.md`, `<bib_key>.md` → `references/`.
     - Any `validity_report.html` / `references.zip` / `report.zip` → `validity-report/`.
   - **Insert DB rows** from `project.json`: `projects` (with default user, `layout_version=2`), `parsed_refs`, `refs`, `ref_files` (compute sha256 of each artifact — required for cache verification per §8.4), `ref_files_origin`, `ref_extras`, `citations`, `claim_checks`, `activity`. Carry over the source state as `source_kind="upload"`, `source_state_json={"tex_filename":..., "bib_filename":...}`.
   - **Populate the promoted `ref_match` columns** (§4.2): for each ref with a `ref_match` blob in `project.json`, copy `ref_match["verdict"]` → `refs.ref_match_verdict` and `ref_match.get("confidence")` → `refs.ref_match_confidence`. The full dict still goes to `ref_extras.ref_match_json`.
   - **Index cache-eligible refs** into `ref_cache_keys`.
   - **Rename `project.json` → `project.json.migrated`** (don't delete — safety net for two weeks).
4. **Print summary**: *"N projects migrated, M refs indexed for cache, K files hashed."*

### 10.2 Hard startup gate

`app.py` startup runs `data_layer.init(PROJECTS_DIR)`:

- If `refchecker.sqlite` is missing OR `schema_version < 1`: refuse to start, print *"Run `python scripts/migrate_to_v7.py` to migrate from v6.x."*
- If any `projects/<slug>/` exists without a corresponding row in `projects`: log a warning (someone added a folder manually).

The gate prevents *"kind of works but some files are in the wrong place"* half-states.

### 10.3 Reverse migration (escape hatch)

`scripts/dump_to_project_json.py` walks the DB and rewrites `project.json` per project. Used to roll back to v6.x or share a project as a single file.

---

## 11. Operator workflow

```
$ git pull                            # get the v7 code
$ pip install -r requirements.txt     # no new deps, but be safe
$ python scripts/migrate_to_v7.py     # one-time, idempotent
$ python app.py                       # start the v7 app
```

---

## 12. Code change inventory

Approximate touch list. The data-layer abstraction makes most call-site changes mechanical (one-line: `os.path.join(PROJECTS_DIR, ...)` → `files.reference_path(...)`; `_read_json(slug)` → `store.get_project(slug)`).

| File | Change |
|---|---|
| `data_layer/__init__.py` | **New.** Public API (`get_store`, `get_files`, `init`, `lookup_and_materialize`). |
| `data_layer/reference_store.py` | **New.** ABC + `SqliteReferenceStore`. Only place with SQL. |
| `data_layer/file_manager.py` | **New.** Path resolution, atomic writes, copy/move/sha256. No SQL, no DB imports. |
| `data_layer/cache.py` | **New.** `lookup_and_materialize` orchestration. No SQL. |
| `data_layer/schema.sql` | **New.** DDL applied on first open. |
| `data_layer/exceptions.py` | **New.** `ProjectNotFound`, `RefNotFound`, `ConcurrencyError`, `CacheStaleError`. |
| `data_layer/migrations/v001_initial.py` | **New.** Baseline schema migration. |
| `project_store.py` | **Deleted.** Replaced by `data_layer`. |
| `bib_io.py` | **New (small).** `read_bib(path) → (text, mtime)`, `write_bib(path, text, expected_mtime) → mtime`, `diff_changed_keys(old_text, new_text) → [bib_keys]`. Wraps `FileManager` for I/O; uses the existing bibtex parser. |
| `source_providers/__init__.py` | **New.** Provider registry. |
| `source_providers/base.py` | **New.** `SourceProvider` ABC + shared exceptions. |
| `source_providers/upload.py` | **New.** Wraps the existing upload flow. |
| `source_providers/overleaf.py` | **New.** Git-over-HTTPS provider. Token via `OVERLEAF_TOKEN`. |
| `source_providers/_git_helpers.py` | **New (light).** `run_git`, askpass-helper builder. |
| `app.py` | All `os.path.join(PROJECTS_DIR, ...)` → `files.*`. All `_read_json` / `_write_json` → store calls. Routes refactored to be provider-agnostic (`POST /api/projects` takes `{provider, params}`). New endpoints: `GET/PUT /api/projects/<slug>/bib`, `POST /api/projects/<slug>/bib/push`, `POST /api/projects/<slug>/source/{pull,push,select}`, `GET /api/projects/<slug>/source/status`, `GET /api/providers`, `GET /api/projects/<slug>/refs/manual-count`, `POST /api/projects/<slug>/refs/redownload`. Cache lookup wired into `_process_ref_with_bib_url`. |
| `file_downloader.py` | All artifact writes go through `files.reference_path(slug, name)`. No path-joining. |
| `lookup_engine.py` | Output paths via `files.*`. Existing single-ref re-lookup is reused by the bib-save flow + the redownload-all path. |
| `validity_report.py` | Reads everything via `store.list_results` / `store.list_citations` / `store.list_claim_checks`. Output goes to `files.report_dir(slug)`. `report.zip` is built from `files.references_dir(slug)`. |
| `templates/index.html` | Create-project form rebuilt around provider tabs (data-driven from `/api/providers`). Reference-check page gains a `.bib` view next to the `.tex` editor (same component). New "Pull / Push" header buttons appear when the active provider supports them. |
| `static/js/app.js` | Wire provider tabs, the new `.bib` editor view, Save / Save-and-push, mtime concurrency, Pull/Push, Re-download all + manual-references modal, `cache_hit` tier badge. Show *"set `<env var>` and restart"* hint when a provider reports unconfigured. |
| `scripts/migrate_to_v7.py` | **New.** Migration script per §10. |
| `scripts/dump_to_project_json.py` | **New.** Reverse migration escape hatch. |
| `tests/test_reference_store.py` | **New.** All store CRUD against `:memory:` SQLite. Cache lookup + indexing. Schema migration. **Dual-write invariant**: `save_ref_match` updates `refs.ref_match_verdict` / `refs.ref_match_confidence` AND `ref_extras.ref_match_json` atomically; round-trip equality between the columns and the JSON's `verdict` / `confidence` fields is verified. |
| `tests/test_file_manager.py` | **New.** Path helpers, atomic writes, mtime concurrency, sha256, listings (skip dot-prefixed). |
| `tests/test_cache.py` | **New.** Materialization with mocked store + tmp-path file manager. Stale-file fallthrough. |
| `tests/test_bib_io.py` | **New.** Round-trips, mtime concurrency rejection, `diff_changed_keys` correctness. |
| `tests/test_source_providers.py` | **New.** Registry + `SourceProvider` contract test using a fake provider. |
| `tests/test_overleaf_provider.py` | **New.** Mock `subprocess.run`; assert URL, env, args, error mapping. End-to-end against a local bare git repo. |
| `tests/test_redownload.py` | **New.** Manual/auto partitioning, `_manual_backup/` creation, idempotency, cache-hit short-circuit. |
| `tests/test_migrate_v7.py` | **New.** Fixture project.json + flat layout → run migrator → assert DB rows + folder layout. Idempotency. |
| Existing tests | Most pass after a search-and-replace of `project_store` → `data_layer.get_store()`. Tests that hard-code `os.path.join(tmp_path, "projects", slug, "x.pdf")` get a one-line update to use `FileManager`. |

CI lint check: `rg -n '\\b(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE)\\b' --type py | grep -v 'data_layer/'` must return empty.

---

## 13. Phases

Each phase is independently shippable and testable.

| Phase | Deliverable |
|---|---|
| **A** | `data_layer/` skeleton: schema, `SqliteReferenceStore`, `FileManager`, `init()`, migrations module, exceptions. Everything compiles; no app integration yet. Tests for the store + file manager pass on `:memory:` / `tmp_path`. |
| **B** | Migration script (§10) + startup gate. Run against a real project folder; verify it produces a valid DB and the new layout. App still uses old `project_store.py`. |
| **C** | **Cut-over.** Delete `project_store.py`. Wire `app.py`, `file_downloader.py`, `lookup_engine.py`, `validity_report.py` to the data layer. Largest blast radius — full test pass before merge. |
| **D** | Provider abstraction + `UploadProvider`. Create-project becomes data-driven from `/api/providers`. **No user-visible behavior change.** |
| **E** | Bib editing (§6). `bib_io.py` + endpoints + UI tab. |
| **F** | Re-download all references (§7). |
| **G** | Global cache (§8) — `lookup_and_materialize` wired into `_process_ref_with_bib_url`, `cache_hit` UI badge, mandatory sha256 verification on every hit (§14, decision 18). |
| **H** | Overleaf provider — read (import + select). |
| **I** | Overleaf provider — write (pull + push + bib editor "Save & push" + tex editor "Save & push"). New endpoints `POST /api/projects/<slug>/{bib,tex}/push`. |
| **(Future) J** | GitHub provider. ~150 LOC + tests once `_git_helpers.py` is established. |
| **(Future) K** | Google Docs provider. Different transport, same interface. |
| **(Future) L** | Polish: cache-savings dashboard card; reverse-migration script auto-tested in CI; cache-warmup CLI (see §15). |

Phase A–C is the **architectural** part. D–I is the **product** part. J–L is **future**.

---

## 14. Decisions (answered)

1. **Database location**: `projects/refchecker.sqlite` (single file at the projects root). Visible, easy to back up, easy to copy to another machine.
2. **DB engine**: SQLite via `sqlite3` stdlib. WAL mode enabled at open. No SQLAlchemy.
3. **Data-layer organization**: dedicated `data_layer/` package. SQL allowed only in `reference_store.py` (and future Postgres sibling). Path-joins allowed only in `file_manager.py`. Cache lives in `cache.py`. Lint check enforces.
4. **Multi-user schema**: `users` + `projects.user_id` from day one. One default user (`local`) seeded by migration. No login UI in v7.
5. **Cache scope**: lookup is global across all users.
6. **Cache eligibility**: `status ∈ {found_pdf, found_web_page}` AND `ref_match.verdict ∈ {matched, manual_matched}`.
7. **Cache match keys**: DOI → arXiv ID → `(normalized_title, sorted_last_names)`. Title alone (no authors) is NOT cached.
8. **Refresh bypasses cache**: yes (per-button override).
9. **Folder restructure**: `source/`, `references/`, `validity-report/` per §2. `source/.provider/<name>/` is the hidden provider state.
10. **Provider state**: lives in `projects.source_state_json` (DB). The hidden `source/.provider/<name>/` folder holds transport-only state (git worktree, etc.) — never metadata.
11. **Migration**: one-time CLI script, idempotent, hard startup gate. `project.json` is renamed to `project.json.migrated` (kept for two weeks as safety net).
12. **`tex_content` storage**: stays on disk in `source/<tex>` (not in DB). The DB only stores the filename.
13. **Reverse migration**: `scripts/dump_to_project_json.py` exists as an escape hatch.
14. **CI lint**: `rg` check rejects SQL keywords outside `data_layer/`.
15. **WAL retention**: rely on SQLite's auto-checkpoint (1000 pages, the default). No periodic VACUUM job — our scale doesn't warrant it; revisit if the file ever exceeds ~500 MB.
16. **`project.json.migrated` retention**: kept for 2 weeks, then auto-deleted by a startup-time sweep. Each deletion logged to `activity` so it shows up in the project's activity log.
17. **Cache index cascade on project delete**: yes — `ref_cache_keys.ref_id` has `ON DELETE CASCADE` (via `refs.project_id`). Deleting a project automatically purges its cache index entries; future lookups can't 500 by referencing vanished files.
18. **sha256 verification on every cache hit**: mandatory, not opportunistic. Hash is recorded into `ref_files.sha256` when the ref is indexed for cache (§8.3) and re-checked by `cache.lookup_and_materialize` before any copy (§8.4 step 2). Mismatch → drop the cache key and try the next candidate.
19. **`.tex` "Save & push to provider"**: yes — the existing View 4 `.tex` editor gains a Save-and-push button alongside its local-save, mirroring the `.bib` flow. Shipped in Phase I alongside bib push for behavioral parity.

---

## 15. Future enhancements (post-v7)

Tracked separately, not blocking v7:

- **Cache-savings dashboard card** — *"X of Y refs (Z%) served from cache, ~N MB saved"* per project, plus a global aggregate across all users.
- **Reverse-migration auto-test in CI** — round-trip migrate → dump → re-migrate to guarantee the v7 ↔ v6 bridge stays operational.
- **Auth + multi-user UI** — wire Flask-Login (or similar) onto the `users.user_id` column that v7 already populates. Cache search stays global (per §8.5).
- **Additional providers** — GitHub (Phase J) and Google Docs (Phase K) reuse the `SourceProvider` ABC.
- **Cache warmup CLI** — `python scripts/cache_warm.py <slug>` to pre-populate cache index for a freshly imported large project before the user clicks Run.

Ready to start Phase A.
 