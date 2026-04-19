# RefrenceCheker — v7 plan

**Theme:** restructure project storage into named subfolders, factor project lifecycle out of `project_store.py` into a dedicated module, add a pluggable **source provider** system (Overleaf first, with GitHub / Google Docs / …​ as future providers) alongside plain uploads, and make the `.bib` file a **first-class editable artifact** — editable inline for quick fixes, openable in a dedicated full-page editor, savable locally or pushable back to the remote provider.

Auth for any remote provider is via environment variable — no per-project token storage.

This plan does **not** change the lookup / download / validity-report pipelines themselves. It changes *where files live*, *how they get into the project*, and *how the user edits them*.

---

## 1. Goals

1. Each project on disk has a clear, predictable layout — easy to inspect, easy to zip, easy to back up:
   - `source/` — the LaTeX and bib files the user works on
   - `references/` — every artifact produced by the lookup pipeline (PDFs, HTML, abstracts, markdown, pasted/uploaded files)
   - `validity-report/` — the generated HTML report and its `references.zip`
   - root — `project.json` and any other small metadata files
2. A single module owns *project lifecycle and paths* (create, list, delete, resolve subpaths, migrate). Other modules import path helpers from it instead of joining strings against `PROJECTS_DIR`.
3. The "create project" flow supports a pluggable set of **source providers**. v7 ships with two:
   - **Upload** — user uploads `.tex` and `.bib`.
   - **Overleaf** — user gives an Overleaf project ID; we clone it via git, let them pick the tex+bib files from the worktree, and copy those into `source/`. They can later pull updates and push edits back.

   The provider layer is designed so that **GitHub** and **Google Docs** (and others) can be added later without touching the rest of the app.
4. The `.bib` file becomes editable using **the existing editor already on the reference-check page** (the one that today handles `.tex`). No new editor component is introduced. Saving offers two destinations: write to local `source/<bib>`, or push back to the remote provider (Overleaf today, GitHub tomorrow). The `.tex` editor stays exactly as it is today.
5. Existing projects in the flat layout keep working — there is a one-shot migration that moves files into the new subfolders.

---

## 2. New folder layout

```
projects/<slug>/
├── project.json
├── source/                        # ONLY the user-facing tex + bib, regardless of origin
│   ├── main.tex
│   └── refs.bib
├── .provider/                     # hidden; only present when source.kind != "upload"
│   └── overleaf/                  # the git worktree, or whatever transport state
│       ├── .git/
│       └── …
├── references/
│   ├── ref_key_pdf.pdf
│   ├── ref_key_page.html
│   ├── ref_key_abstract.txt
│   ├── ref_key.md
│   └── …
└── validity-report/
    ├── validity_report.html
    └── references.zip
```

Notes:
- **`source/` contains the same two files no matter where they came from** — upload, Overleaf, GitHub, Google Docs. Provider-specific transport state (a git worktree for Overleaf/GitHub, a Drive cache for Google Docs) lives in a separate hidden folder `.provider/<name>/` at the project root, **never inside `source/`**. The rest of the pipeline reads `source/` and is provider-blind.
- The dot-prefixed `.provider/` is hidden (won't clutter file pickers, won't get walked when scanning for tex/bib candidates), and is also added to the report zip's exclusion list.
- The `references/` folder replaces today's flat dump of `*_pdf.pdf`, `*_page.html`, etc. Filenames stay the same; only the parent directory changes.
- `validity-report/` is built per run. The `references.zip` inside it is built by zipping `references/` (so we drop the temp staging the report does today).

---

## 3. New module: `project_paths.py` (or `project_manager.py`)

Single source of truth for paths and project lifecycle. Pure functions, no Flask coupling.

```python
# project_paths.py
from config import PROJECTS_DIR

def project_dir(slug: str) -> str: ...
def source_dir(slug: str) -> str: ...
def references_dir(slug: str) -> str: ...
def report_dir(slug: str) -> str: ...

def project_json_path(slug: str) -> str: ...
def source_path(slug: str, filename: str) -> str: ...
def reference_path(slug: str, filename: str) -> str: ...
def report_path(slug: str, filename: str) -> str: ...

def ensure_project_dirs(slug: str) -> None:
    """Create project_dir + source/ + references/ + validity-report/ if missing."""

def list_source_files(slug: str, *, exts: tuple[str, ...] = (".tex", ".bib")) -> list[str]: ...
def list_reference_files(slug: str) -> list[str]: ...

def migrate_legacy_layout(slug: str) -> dict:
    """Move flat-layout files into source/ + references/. Idempotent.
    Returns {moved: [...], skipped: [...]}. Updates project.json bib/tex paths
    to be relative to source/.
    """
```

The lifecycle pieces currently in `project_store.py` (`create_project`, `delete_project`, `list_projects`, `slugify`, plus the dir-ensure code) move here. `project_store.py` keeps the JSON read/write + locking + result mutation helpers (`save_results`, etc.) — i.e. it stays the **state** module, while `project_paths.py` becomes the **layout** module.

Migration of call sites is mechanical: anywhere we currently do `os.path.join(PROJECTS_DIR, slug, X)`, it becomes `reference_path(slug, X)` or `source_path(slug, X)` depending on what `X` is. Grep finds ~20 files; most have 1–3 call sites.

---

## 4. Project metadata changes (`project.json`)

Add a `source` block describing where the tex+bib came from:

```json
{
  "source": {
    "kind": "upload" | "overleaf",
    "tex_filename": "main.tex",
    "bib_filename": "refs.bib",
    "overleaf": {
      "project_id": "65fa…",
      "remote_url": "https://git.overleaf.com/65fa…",
      "tex_path_in_repo": "chapters/main.tex",
      "bib_path_in_repo": "bibliography/refs.bib",
      "last_synced_commit": "abc123…",
      "last_synced_at": "2026-04-19T10:30:00+00:00"
    }
  }
}
```

The `source` block is **provider-agnostic** — `kind` selects which provider it belongs to, and the nested object (`overleaf`, `github`, `google_docs`, …) holds provider-specific state. This is the on-disk shape of the abstraction in §6.

For backwards compat: if `source` is missing, treat the project as `kind: "upload"` and read `bib_filename` / `tex_filename` from the top level the way we do today.

---

## 5. Editable `.bib` (and `.tex`) files

The bib file isn't just an input we read once — users will fix typos, add a missing `year`, repair a malformed entry, etc. v7 makes editing a real feature.

### 5.1 Inline single-entry edit (in the existing bib tab)

The reference cards in the bib tab gain an **Edit** action. Clicking it expands the card into a small textarea pre-populated with that entry's raw bibtex (the `raw_bib` field already on each result). The user edits, hits **Save**:

- Frontend: `PUT /api/projects/<slug>/bib/entries/<bib_key>` with the new raw text.
- Backend: parse the new entry to validate it's a single well-formed bibtex record with the same `bib_key` (or surface a clear error). Splice it into `source/<bib>` in place of the old entry. Re-run *just this entry's* lookup pipeline (we already have a single-ref re-lookup path) and refresh the card.
- The on-disk `source/<bib>` file is the single source of truth. The bib in `project.json` is regenerated from the file, not the other way round.

### 5.2 Full-page bib editor

A separate page at `/projects/<slug>/bib/edit` that loads `source/<bib>` into a code editor (CodeMirror 6 — small, no build step, supports a bibtex mode via a tiny custom tokenizer if needed; otherwise the generic stex/text mode is fine). Features:

- Syntax-aware coloring, line numbers, search, undo.
- **Validate** button: runs the bibtex parser, lists any malformed entries with line numbers (the parser we already use exposes errors).
- **Save** writes the file, invalidates affected entries' cached lookup results (anything whose raw_bib changed), and returns the user to the project page.
- Optimistic concurrency: the editor reads `mtime` on load and refuses to save if it changed (someone else / a remote pull touched it). Resolution is "reload" or "force save".

A **Push to <provider>** button on this page is shown when the project has a writable provider attached — it invokes the same push flow as §6.3.

### 5.3 The `.tex` file

Same pattern, but lighter priority — the `.tex` is mostly read-only from the app's perspective (we extract `\cite{}` calls, we don't rewrite them). v7 ships with a **read-only** full-page tex viewer (same editor component, `readOnly: true`). Editing the tex file remains an Overleaf/IDE concern. If users ask, the same component can become editable in a later version with no architectural change.

---

## 6. Source providers (pluggable)

Source = "where the canonical tex+bib live, and how we sync with that location". Upload, Overleaf, GitHub, Google Docs all answer the same questions: how do we get the files in, can we push edits back, and how do we know if the remote moved.

### 6.1 The provider interface — abstract base class

`SourceProvider` is an `abc.ABC`, not a Protocol. Reasons: (1) we want concrete providers to fail loudly at import time if they forget a method, not silently at runtime; (2) the base class can carry shared behavior (the `Upload`/`Overleaf` providers both need to ensure `source_dir` exists, both need to update `project.json -> source`, both need to expose `is_configured`); (3) `isinstance(p, SourceProvider)` works for registration validation and tests.

```python
# source_providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import project_paths
import project_store


@dataclass(frozen=True)
class ImportResult:
    """Returned by import_into. Frontend uses `candidates` to drive the
    tex/bib picker. `state` is what gets persisted under
    project.json -> source.<provider_name>."""
    candidates: dict           # {"tex": [str], "bib": [str]}
    state: dict                # provider-specific metadata to persist


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
    """Abstract base for everything that can supply (and optionally accept
    pushes of) a project's tex+bib files. One instance per provider, shared
    across all projects. Methods are stateless w.r.t. the instance — all
    per-project state lives on disk under projects/<slug>/."""

    # --- class-level metadata (subclasses override) ---
    name: ClassVar[str]                    # "upload", "overleaf", "github", ...
    display_name: ClassVar[str]            # "Upload files", "Overleaf", ...
    supports_pull: ClassVar[bool] = False
    supports_push: ClassVar[bool] = False
    env_var: ClassVar[str | None] = None   # e.g. "OVERLEAF_TOKEN" or None for upload

    # --- shared behavior (concrete) ---
    def is_configured(self) -> bool:
        """Default: configured iff env_var is unset-or-non-empty.
        Upload overrides to always return True."""
        if self.env_var is None:
            return True
        import os
        return bool(os.environ.get(self.env_var))

    def describe(self) -> dict:
        """Used by GET /api/providers to render the create-project tabs."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_pull": self.supports_pull,
            "supports_push": self.supports_push,
            "env_var": self.env_var,
            "is_configured": self.is_configured(),
        }

    def _persist_state(self, slug: str, state: dict) -> None:
        """Common: write provider state under project.json -> source.<name>."""
        project = project_store.load_project(slug)
        project.setdefault("source", {})["kind"] = self.name
        project["source"][self.name] = state
        project_store.save_project(slug, project)

    # --- methods every provider must implement ---
    @abstractmethod
    def import_into(self, slug: str, params: dict) -> ImportResult: ...

    @abstractmethod
    def select(self, slug: str, tex_path: str, bib_path: str) -> None: ...

    # --- methods with sensible "not supported" defaults ---
    def pull(self, slug: str) -> dict:
        raise NotSupported(f"{self.name} does not support pull")

    def push(self, slug: str, files: list[str], message: str) -> dict:
        raise NotSupported(f"{self.name} does not support push")

    def status(self, slug: str) -> SyncStatus:
        return SyncStatus(dirty=False, ahead=0, behind=0, last_synced_at=None)
```

### 6.2 Concrete providers

```python
# source_providers/upload.py
class UploadProvider(SourceProvider):
    name = "upload"
    display_name = "Upload files"
    # supports_pull / supports_push stay False; env_var stays None

    def is_configured(self) -> bool:
        return True

    def import_into(self, slug, params) -> ImportResult:
        # params = {"tex_file": FileStorage, "bib_file": FileStorage}
        # Save into source_dir(slug), then return what was saved.
        ...

    def select(self, slug, tex_path, bib_path) -> None:
        # No-op for upload — files were placed directly during import_into.
        # We still call _persist_state to record the chosen filenames.
        ...
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
        # 1. _git_helpers.clone(project_id, source_dir(slug)/"overleaf")
        # 2. walk for *.tex / *.bib
        # 3. return ImportResult(candidates=..., state={"project_id": ..., "cloned_at": ...})
        ...

    def select(self, slug, tex_path, bib_path) -> None: ...
    def pull(self, slug) -> dict: ...
    def push(self, slug, files, message) -> dict: ...
    def status(self, slug) -> SyncStatus: ...
```

### 6.3 Registry

```python
# source_providers/__init__.py
from .base import SourceProvider, ImportResult, SyncStatus, \
    AuthError, DivergedError, NotSupported
from .upload import UploadProvider
from .overleaf import OverleafProvider

_REGISTRY: dict[str, SourceProvider] = {}

def register(provider: SourceProvider) -> None:
    assert isinstance(provider, SourceProvider), \
        f"{provider!r} must subclass SourceProvider"
    if provider.name in _REGISTRY:
        raise ValueError(f"Provider {provider.name!r} already registered")
    _REGISTRY[provider.name] = provider

def get(name: str) -> SourceProvider:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown source provider: {name!r}")
    return _REGISTRY[name]

def all_providers() -> list[SourceProvider]:
    return list(_REGISTRY.values())

# Built-in registrations — happen at import time:
register(UploadProvider())
register(OverleafProvider())
# Future: register(GitHubProvider()), register(GoogleDocsProvider())
```

### 6.4 Using providers across the application

The provider classes are the **only** way the rest of the app interacts with source files for import/sync. There is no `if kind == "overleaf"` anywhere in `app.py`, the templates, or the JS — every branch goes through `get(kind).<method>()`.

| Call site | What it calls |
|---|---|
| `GET /api/providers` | `[p.describe() for p in source_providers.all_providers()]` — drives the create-project tabs and the Pull/Push button visibility. |
| `POST /api/projects` | Reads `provider` from the request body. `provider = source_providers.get(name); result = provider.import_into(slug, params)`. Returns `result.candidates` to the picker. |
| `POST /api/projects/<slug>/source/select` | `provider.select(slug, tex, bib)`. |
| `POST /api/projects/<slug>/source/pull` | `provider.pull(slug)`. UI button only shown when `provider.supports_pull`. |
| `POST /api/projects/<slug>/source/push` | `provider.push(slug, files, message)`. UI button only shown when `provider.supports_push`. |
| `GET /api/projects/<slug>/source/status` | `provider.status(slug)`. Used by the bib editor (mtime concurrency), the Push button (enable when dirty), and the project header. |
| Bib editor "Push to <provider>" button | Same `/source/push` endpoint. The button label comes from `provider.display_name`. |
| Project page header | Reads `project["source"]["kind"]`, calls `source_providers.get(kind)`, and uses the resulting instance to decide which sync buttons to render. |

Helper used everywhere we need the provider for a given project:

```python
# project_store.py
def provider_for(slug: str) -> SourceProvider:
    project = load_project(slug)
    kind = project.get("source", {}).get("kind", "upload")
    return source_providers.get(kind)
```

This is the *only* place that maps a project to its provider. Tests can monkey-patch `_REGISTRY` to inject fakes (`FakeProvider(SourceProvider)`), and the contract test in `tests/test_source_providers.py` runs every registered provider through a smoke sequence to enforce the ABC contract.

### 6.5 Token / auth strategy

Every provider that needs auth reads it from a single environment variable, named after the provider:

| Provider | Env var |
|---|---|
| Overleaf | `OVERLEAF_TOKEN` |
| GitHub | `GITHUB_TOKEN` (PAT with `repo` scope) |
| Google Docs | `GOOGLE_APPLICATION_CREDENTIALS` (service-account JSON path, standard Google convention) |

The provider itself owns the env-var name and the "is auth configured?" check. The UI calls `provider.is_configured() -> bool` and disables the corresponding tab with a one-liner ("set `OVERLEAF_TOKEN` and restart") when False. No tokens ever land in `project.json` or on disk.

### 6.6 Overleaf provider (v7 ships this one)

`source_providers/overleaf.py` — thin wrapper around `git` via `subprocess`. No `gitpython` dep.

Key internal helpers (private to the provider):

```python
def _clone(project_id, dest_dir) -> str: ...          # returns commit sha
def _pull(repo_dir) -> dict: ...                       # {old_sha, new_sha, changed_files}
def _commit_and_push(repo_dir, files, message) -> str: # returns new sha
def _status(repo_dir) -> dict: ...
```

All `git` invocations run with `env = {**os.environ, "GIT_ASKPASS": <helper>, "GIT_TERMINAL_PROMPT": "0"}`. The askpass helper is a tiny inline script that prints `$OVERLEAF_TOKEN`, so the token never lands in the URL, the reflog, or `ps`.

**Constraints inherited from Overleaf** (surface in the import modal as a one-time warning): single `master` branch, no LFS, file renames become delete+create, and "don't mix git with online track changes".

### 6.7 GitHub provider (sketch — not implemented in v7)

`source_providers/github.py` — same git-subprocess machinery as Overleaf, but with a configurable branch (default `main`) and the URL `https://github.com/<owner>/<repo>.git`. Token via `GITHUB_TOKEN`, passed the same way through `GIT_ASKPASS`. Push goes to a configurable branch (default the same one we cloned). Because the underlying transport is identical to Overleaf, ~80% of `overleaf.py` will factor out into a shared `_git_helpers.py` once the second git-based provider exists.

### 6.8 Google Docs provider (sketch — not implemented in v7)

The Google Docs provider would subclass `SourceProvider` exactly the same way — `name = "google_docs"`, `display_name = "Google Docs"`, `env_var = "GOOGLE_APPLICATION_CREDENTIALS"`, `supports_pull = True`, `supports_push = True | False` (depending on what the Drive API allows for the file types in question — TBD). It overrides `import_into` / `pull` / `push` to use the Drive API via the official client library instead of git, but everything outside the provider class stays unchanged. This is the proof that the abstraction holds: a transport totally unlike git fits the same interface.

### 6.9 Flows (provider-agnostic)

**Import:**
1. User picks a provider tab and fills in its form (Overleaf project ID, GitHub repo URL, Google Doc ID, or chooses upload).
2. Backend: `result = provider.import_into(slug, form_params)`.
3. UI: shows the picker built from `result.candidates`. User picks tex+bib.
4. Backend: `provider.select(slug, tex, bib)` copies them to `source/<bib>` and `source/<tex>`.

**Pull:**
- "Pull from <display_name>" button. Only shown when `provider.supports_pull`.
- Calls `provider.pull(slug)`. On success, re-copies the two tracked files into `source/`. If either changed, the UI offers "re-run lookup".
- On divergence: modal with "keep mine / keep theirs / cancel".

**Push:**
- "Push to <display_name>" button. Only shown when `provider.supports_push`.
- Diffs `source/<tex>` and `source/<bib>` against the provider's last-synced copies. If different, modal: file list + line counts + editable commit message.
- On confirm: `provider.push(slug, [tex, bib], message)`. Update `last_synced_*`.

---

## 7. Migration of existing projects

A flat-layout project (everything in `projects/<slug>/`) needs to become a structured one. Two choices, in priority order:

1. **Lazy migration on access** (preferred). The first time a project is opened after upgrade, `project_paths.migrate_legacy_layout(slug)` runs:
   - Create `source/`, `references/`, `validity-report/` if missing.
   - Move `*.tex` and `*.bib` from root → `source/`.
   - Move every other reference artifact (`*_pdf.pdf`, `*_page.html`, `*_abstract.txt`, `*.md`, `*_pasted.md`) from root → `references/`.
   - Move any prior `validity_report.html` / `references.zip` into `validity-report/`.
   - Leave `project.json` in place. Add `"layout_version": 2` to it so we don't re-run.
   - Idempotent: if `layout_version >= 2`, skip.
2. **Bulk CLI command** for power users: `python -m project_paths migrate-all`. Calls the same function for every project under `PROJECTS_DIR`.

Before any move, a one-line backup: `shutil.copy2` the project.json to `project.json.pre_v7.bak`. (Files themselves are moved, not copied — duplicating PDFs is wasteful.)

---

## 8. Code change inventory

Approximate touch list. Most are one-line path changes.

| File | Change |
|---|---|
| `project_paths.py` | **New.** Layout helpers + lifecycle moved out of `project_store.py`. |
| `project_store.py` | Drop `create_project` / `delete_project` / `list_projects` (move to `project_paths`). Keep state mutators. Add helpers to read/write a single bib entry by key (used by inline edit). |
| `bib_editor.py` | **New.** Parse / splice / re-serialize the `.bib` file. `read_entry(path, bib_key)`, `replace_entry(path, bib_key, raw_text)`, `validate(text) -> [errors]`, `read_all(path)`, `write_all(path, text)`. Uses the bibtex parser already in the project. |
| `source_providers/__init__.py` | **New.** Provider registry (`PROVIDERS`, `get_provider(name)`). |
| `source_providers/base.py` | **New.** `SourceProvider` Protocol + shared exceptions (`AuthError`, `DivergedError`, `NotSupported`). |
| `source_providers/upload.py` | **New.** Wraps the existing upload flow as a provider. `supports_push = False`. |
| `source_providers/overleaf.py` | **New.** Git-over-HTTPS provider. Token via `OVERLEAF_TOKEN`. |
| `source_providers/_git_helpers.py` | **New (light).** Small helpers (`run_git`, askpass-helper builder). Will absorb more shared code once GitHub lands. |
| `config.py` | No new settings keys. Each provider reads its own env var. |
| `app.py` | Routes refactored to be provider-agnostic: `POST /api/projects` takes `{provider: "<name>", params: {...}}`. New: `GET/PUT /api/projects/<slug>/bib`, `GET/PUT /api/projects/<slug>/bib/entries/<bib_key>`, `POST /api/projects/<slug>/source/pull`, `POST /api/projects/<slug>/source/push`, `GET /api/providers` (lists name + supports_push + is_configured for the UI). All path joins go through `project_paths.*`. |
| `file_downloader.py` | All artifact writes go through `project_paths.reference_path(slug, name)`. |
| `lookup_engine.py` | Same — output paths via `project_paths`. Single-entry re-lookup gains a `from_bib_text=...` mode used by the inline editor. |
| `validity_report.py` | Output goes to `report_dir(slug)`. `references.zip` is built from `references_dir(slug)` (no temp staging dir needed). |
| `templates/index.html` | Create-project form is rebuilt around the provider tabs (driven by `/api/providers`). Bib tab gains an Edit-entry affordance and a "Edit full file" button. New "Pull / Push" header buttons appear when the active provider supports them. |
| `templates/bib_editor.html` | **New.** Full-page bib editor (CodeMirror 6 via CDN, no build step). |
| `static/js/app.js` | Wire provider tabs (data-driven), inline edit on bib cards, "Edit full file" navigation, Pull/Push buttons. Show the "set `<env var>` and restart" hint when a provider reports unconfigured. |
| `static/js/bib_editor.js` | **New.** CodeMirror init, validate, save, mtime concurrency check, "Push to <provider>" wiring. |
| `tests/test_project_paths.py` | **New.** Layout helpers, `ensure_project_dirs`, `migrate_legacy_layout` (idempotency, layout_version gate). |
| `tests/test_bib_editor.py` | **New.** `read_entry` / `replace_entry` round-trips, validate-error reporting, mtime concurrency. |
| `tests/test_source_providers.py` | **New.** Registry + `SourceProvider` contract tests (use a fake provider). |
| `tests/test_overleaf_provider.py` | **New.** Mock `subprocess.run`; assert URL, env, args, error mapping. End-to-end import/pull/push against a local bare git repo. |
| Existing tests | Most pass unchanged because they go through `project_store` / `validity_report` rather than touching paths directly. The handful that hard-code `os.path.join(tmp_path, "projects", slug, "x.pdf")` get a one-line update to use the new subfolder. |

---

## 9. Phases

1. **Phase A — Layout + path module + migration.** No providers yet. Ship the folder restructure, the new `project_paths.py`, the lazy migration, and updated call sites. Existing upload flow continues to work; existing projects auto-migrate on first open. This is the riskiest change because it touches many files, so it ships alone and gets verified before anything else lands.
2. **Phase B — Provider abstraction + Upload provider.** Introduce `source_providers/` with the registry, the `SourceProvider` Protocol, and `UploadProvider` (which just wraps the existing flow). The create-project form becomes data-driven from `/api/providers`. **No behavior change visible to users yet** — this purely shifts where the code lives, behind a working abstraction we can extend.
3. **Phase C — Bib editing.** Inline single-entry edit in the bib tab + full-page bib editor (`/projects/<slug>/bib/edit`). `bib_editor.py` module + tests. Save updates `source/<bib>` and re-runs the affected single-entry lookup. Works for any provider (including upload) — push wiring comes in D.
4. **Phase D — Overleaf provider (read).** `OverleafProvider` with import + tex/bib picker. Token from `OVERLEAF_TOKEN`. The bib editor's Save still only writes locally; Push button doesn't appear yet.
5. **Phase E — Overleaf provider (write).** Pull / Push buttons in the project header *and* in the bib editor. Diff preview + commit message modal. Divergence handling. Once this lands, the full edit-and-publish loop works end-to-end.
6. **(Future) Phase F — GitHub provider.** Reuses `_git_helpers.py`. Should be ~150 LOC + tests, since the abstraction is already proven.
7. **(Future) Phase G — Google Docs provider.** Different transport, but the same provider interface.

Each phase is independently shippable and testable.

---

## 10. Open questions for you

1. **Module name:** `project_paths.py` (descriptive — about layout) or `project_manager.py` (broader — also owns lifecycle)? I'd lean `project_paths.py`.
2. **Migration timing:** lazy on first access (safer) vs. eager on app startup (simpler)? Write a migration on_time script for the current projects.
3. **Overleaf worktree location:** `source/overleaf/` (proposed — clean, everything source-y together) vs. hidden `.overleaf/` at the project root (keeps `source/` to just the two user-facing files)? I'd pick `source/overleaf/`, and have the bib full-page editor only ever open `source/<bib>` (not anything inside `overleaf/`), so the worktree never appears in pickers.
4. **What counts as a reference artifact** for migration? Currently: `*_pdf.pdf`, `*_page.html`, `*_abstract.txt`, `*_pasted.md`, and bib-key-prefixed `*.md`. Anything else in the root I should preserve in place rather than moving?
5. **Bib editor library:** CodeMirror 6 (proposed — small, modern, CDN-loadable, no build step) vs. Monaco (heavier, but matches VS Code feel). CodeMirror 6 is my pick — fits the no-bundler nature of this app.
6. **Editor scope on day one:** edit `.bib` only (proposed) or also editable `.tex`? The data model and editor component are the same — the difference is whether the lookup pipeline needs to react to tex edits (today it re-parses `\cite{}`s; that's cheap, so it would). I'd ship bib-editable + tex-readonly first and add tex-edit later if asked.

Confirm those and I'll start on Phase A.
