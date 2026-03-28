# References Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web app that parses .bib files and finds full papers/abstracts via academic APIs (Semantic Scholar, CrossRef, Unpaywall, Scholarly), with real-time progress via SSE and downloadable reports.

**Architecture:** Flask backend with ThreadPoolExecutor for concurrent API lookups. SSE streams progress to a single-page frontend. In-memory session store with TTL. No LLM, no database, no auth.

**Tech Stack:** Python 3.10+, Flask, bibtexparser v2, requests, scholarly, fpdf2

**Spec:** `docs/superpowers/specs/2026-03-22-references-checker-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `app.py` | Flask routes: upload, SSE stream, download, serve UI |
| `config.py` | All configuration from env vars with defaults |
| `bib_parser.py` | Parse .bib files, extract fields, deduplicate |
| `lookup_engine.py` | Orchestrate lookup chain per reference, manage ThreadPool |
| `session_store.py` | In-memory session dict with TTL and cleanup |
| `report_exporter.py` | Generate CSV and PDF reports from results |
| `api_clients/__init__.py` | Package init |
| `api_clients/semantic_scholar.py` | Semantic Scholar API wrapper |
| `api_clients/crossref.py` | CrossRef API wrapper |
| `api_clients/unpaywall.py` | Unpaywall API wrapper |
| `api_clients/scholarly_client.py` | Google Scholar fallback via scholarly lib |
| `templates/index.html` | Single-page HTML shell |
| `static/css/style.css` | All styles |
| `static/js/app.js` | Frontend logic: upload, SSE, rendering, download |
| `requirements.txt` | Python dependencies |
| `tests/test_bib_parser.py` | Tests for .bib parsing |
| `tests/test_lookup_engine.py` | Tests for lookup orchestration |
| `tests/test_api_clients.py` | Tests for each API client |
| `tests/test_session_store.py` | Tests for session management |
| `tests/test_report_exporter.py` | Tests for report generation |
| `tests/test_app.py` | Integration tests for Flask routes |

---

### Task 1: Project Setup and Configuration

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize git repo and create requirements.txt**

```bash
cd "/Users/darkotrajanov/Refrences Checker"
git init
```

```txt
flask==3.1.0
bibtexparser>=2.0.0
requests==2.32.3
scholarly==1.7.11
fpdf2==2.8.3
pytest==8.3.4
```

- [ ] **Step 2: Create config.py with all settings**

```python
import os

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "references-checker@example.com")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "5"))
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", str(2 * 1024 * 1024)))  # 2MB
SESSION_TTL = int(os.environ.get("SESSION_TTL", "1800"))  # 30 minutes
SCHOLARLY_ENABLED = os.environ.get("SCHOLARLY_ENABLED", "true").lower() == "true"
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5000"))
```

- [ ] **Step 3: Create empty tests package**

Create `tests/__init__.py` (empty file).

- [ ] **Step 4: Install dependencies and verify**

```bash
pip install -r requirements.txt
python -c "import flask; import bibtexparser; import requests; import fpdf; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.py tests/__init__.py
git commit -m "feat: project setup with dependencies and config"
```

---

### Task 2: Session Store

**Files:**
- Create: `session_store.py`
- Create: `tests/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

```python
import time
from session_store import SessionStore

def test_create_session():
    store = SessionStore(ttl=60)
    sid = store.create()
    session = store.get(sid)
    assert session is not None
    assert session["status"] == "created"
    assert session["results"] == []
    assert session["progress_index"] == 0

def test_get_nonexistent_returns_none():
    store = SessionStore(ttl=60)
    assert store.get("nonexistent") is None

def test_update_session():
    store = SessionStore(ttl=60)
    sid = store.create()
    store.update(sid, status="processing", total=10)
    session = store.get(sid)
    assert session["status"] == "processing"
    assert session["total"] == 10

def test_add_result():
    store = SessionStore(ttl=60)
    sid = store.create()
    store.add_result(sid, {"title": "Test Paper", "status": "found_pdf"})
    session = store.get(sid)
    assert len(session["results"]) == 1
    assert session["results"][0]["title"] == "Test Paper"

def test_expired_session_returns_none():
    store = SessionStore(ttl=0)  # immediate expiry
    sid = store.create()
    time.sleep(0.1)
    store.cleanup()
    assert store.get(sid) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_session_store.py -v
```

Expected: FAIL — `session_store` module not found.

- [ ] **Step 3: Implement session_store.py**

```python
import threading
import time
import uuid
from config import SESSION_TTL


class SessionStore:
    def __init__(self, ttl=None):
        self._store = {}
        self._lock = threading.Lock()
        self._ttl = ttl if ttl is not None else SESSION_TTL

    def create(self):
        sid = str(uuid.uuid4())
        with self._lock:
            self._store[sid] = {
                "status": "created",
                "results": [],
                "progress_index": 0,
                "total": 0,
                "created_at": time.time(),
            }
        return sid

    def get(self, sid):
        import copy
        with self._lock:
            data = self._store.get(sid)
            if data is None:
                return None
            snapshot = dict(data)
            snapshot["results"] = list(data["results"])
            return snapshot

    def update(self, sid, **kwargs):
        with self._lock:
            if sid in self._store:
                self._store[sid].update(kwargs)

    def add_result(self, sid, result):
        with self._lock:
            if sid in self._store:
                self._store[sid]["results"].append(result)
                self._store[sid]["progress_index"] += 1

    def cleanup(self):
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, data in self._store.items()
                if now - data["created_at"] > self._ttl
            ]
            for sid in expired:
                del self._store[sid]

    def start_cleanup_thread(self, interval=300):
        def _loop():
            while True:
                time.sleep(interval)
                self.cleanup()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_session_store.py -v
```

Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add session_store.py tests/test_session_store.py
git commit -m "feat: in-memory session store with TTL and cleanup"
```

---

### Task 3: Bib Parser

**Files:**
- Create: `bib_parser.py`
- Create: `tests/test_bib_parser.py`
- Create: `tests/fixtures/sample.bib`

- [ ] **Step 1: Create test fixture — sample.bib**

```bib
@article{smith2020,
  author = {Smith, John and Doe, Jane},
  title = {A Study on Machine Learning},
  journal = {Journal of AI},
  year = {2020},
  doi = {10.1234/example.2020}
}

@inproceedings{jones2019,
  author = {Jones, Bob},
  title = {Deep Learning in Practice},
  booktitle = {Proceedings of NeurIPS},
  year = {2019}
}

@article{duplicate2020,
  author = {Smith, John},
  title = {A Study on Machine Learning},
  journal = {Journal of AI},
  year = {2020},
  doi = {10.1234/example.2020}
}

@misc{noinfo,
}

@article{unicode2021,
  author = {M\"{u}ller, Hans and Gar\c{c}ia, Pedro},
  title = {Erd\H{o}s Number Analysis},
  year = {2021},
  doi = {10.5678/unicode.2021}
}
```

- [ ] **Step 2: Write the failing tests**

```python
from bib_parser import parse_bib_file

def test_parse_basic_entries():
    results = parse_bib_file("tests/fixtures/sample.bib")
    titles = [r["title"] for r in results if r.get("title")]
    assert "A Study on Machine Learning" in titles
    assert "Deep Learning in Practice" in titles

def test_extracts_all_fields():
    results = parse_bib_file("tests/fixtures/sample.bib")
    smith = next(r for r in results if r["bib_key"] == "smith2020")
    assert smith["doi"] == "10.1234/example.2020"
    assert smith["year"] == "2020"
    assert "Smith" in smith["authors"]

def test_deduplicates_by_doi():
    results = parse_bib_file("tests/fixtures/sample.bib")
    doi_entries = [r for r in results if r.get("doi") == "10.1234/example.2020"]
    assert len(doi_entries) == 1

def test_handles_entry_with_no_title_no_doi():
    results = parse_bib_file("tests/fixtures/sample.bib")
    noinfo = next((r for r in results if r["bib_key"] == "noinfo"), None)
    assert noinfo is not None
    assert noinfo["status"] == "insufficient_data"

def test_handles_unicode():
    results = parse_bib_file("tests/fixtures/sample.bib")
    uni = next(r for r in results if r["bib_key"] == "unicode2021")
    assert uni["doi"] == "10.5678/unicode.2021"
    assert uni["title"] is not None

def test_empty_file_returns_empty():
    import tempfile, os
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".bib", delete=False)
    f.write("")
    f.close()
    results = parse_bib_file(f.name)
    os.unlink(f.name)
    assert results == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_bib_parser.py -v
```

Expected: FAIL — `bib_parser` module not found.

- [ ] **Step 4: Implement bib_parser.py**

```python
import bibtexparser


def parse_bib_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        bib_string = f.read()
    return parse_bib_string(bib_string)


def parse_bib_string(bib_string):
    if not bib_string.strip():
        return []

    library = bibtexparser.parse(bib_string)
    seen_dois = set()
    seen_titles = set()
    results = []

    # Include failed/malformed entries as parse errors
    for block in library.failed_blocks:
        results.append({
            "bib_key": f"parse_error_{len(results)}",
            "title": None,
            "authors": "",
            "year": None,
            "journal": None,
            "doi": None,
            "url": None,
            "status": "parse_error",
            "raw": str(block.raw),
        })

    for entry in library.entries:
        bib_key = entry.key
        fields = {f.key: f.value for f in entry.fields}

        title = fields.get("title", "").strip().strip("{}")
        doi = fields.get("doi", "").strip()
        authors = fields.get("author", "")
        year = fields.get("year", "").strip()
        journal = fields.get("journal", "") or fields.get("booktitle", "")
        url = fields.get("url", "").strip()

        # Deduplicate by DOI
        if doi:
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
        elif title:
            norm_title = title.lower().strip()
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

        # Determine status for entries with no useful data
        status = None
        if not title and not doi:
            status = "insufficient_data"

        results.append({
            "bib_key": bib_key,
            "title": title or None,
            "authors": authors,
            "year": year or None,
            "journal": journal.strip().strip("{}") or None,
            "doi": doi or None,
            "url": url or None,
            "status": status,
        })

    return results
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_bib_parser.py -v
```

Expected: All 6 PASS.

- [ ] **Step 6: Commit**

```bash
git add bib_parser.py tests/test_bib_parser.py tests/fixtures/sample.bib
git commit -m "feat: bib parser with deduplication and unicode handling"
```

---

### Task 4: API Clients — CrossRef

**Files:**
- Create: `api_clients/__init__.py`
- Create: `api_clients/crossref.py`
- Create: `tests/test_api_clients.py`

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import patch, Mock
from api_clients.crossref import lookup_crossref


def test_crossref_with_valid_doi():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "title": ["Machine Learning Study"],
            "author": [{"given": "John", "family": "Smith"}],
            "container-title": ["Journal of AI"],
            "published-print": {"date-parts": [[2020]]},
            "URL": "https://doi.org/10.1234/test"
        }
    }
    with patch("api_clients.crossref.requests.get", return_value=mock_resp):
        result = lookup_crossref("10.1234/test")
    assert result["title"] == "Machine Learning Study"
    assert result["authors"] == ["John Smith"]
    assert result["year"] == "2020"


def test_crossref_with_invalid_doi():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.crossref.requests.get", return_value=mock_resp):
        result = lookup_crossref("10.9999/nonexistent")
    assert result is None


def test_crossref_timeout():
    with patch("api_clients.crossref.requests.get", side_effect=Exception("timeout")):
        result = lookup_crossref("10.1234/test")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_clients.py::test_crossref_with_valid_doi -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create api_clients package and implement crossref.py**

`api_clients/__init__.py` — empty file.

```python
# api_clients/crossref.py
import threading
import time
import requests

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 0.2


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def lookup_crossref(doi, timeout=10, max_retries=3):
    url = f"https://api.crossref.org/works/{doi}"
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            msg = resp.json().get("message", {})
            titles = msg.get("title", [])
            authors_raw = msg.get("author", [])
            authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in authors_raw
            ]
            container = msg.get("container-title", [])
            pub = msg.get("published-print") or msg.get("published-online") or {}
            date_parts = pub.get("date-parts", [[]])
            year = str(date_parts[0][0]) if date_parts and date_parts[0] else None
            return {
                "title": titles[0] if titles else None,
                "authors": authors,
                "journal": container[0] if container else None,
                "year": year,
                "url": msg.get("URL"),
            }
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_clients.py -k crossref -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api_clients/ tests/test_api_clients.py
git commit -m "feat: crossref API client with rate limiting"
```

---

### Task 5: API Clients — Unpaywall

**Files:**
- Modify: `api_clients/unpaywall.py` (create)
- Modify: `tests/test_api_clients.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_clients.py`:

```python
from api_clients.unpaywall import lookup_unpaywall


def test_unpaywall_finds_oa_pdf():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/paper.pdf"
        }
    }
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.1234/test")
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["is_oa"] is True


def test_unpaywall_no_oa():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "is_oa": False,
        "best_oa_location": None
    }
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.1234/test")
    assert result["pdf_url"] is None


def test_unpaywall_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.9999/fake")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_clients.py -k unpaywall -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement unpaywall.py**

```python
# api_clients/unpaywall.py
import threading
import time
import requests
from config import UNPAYWALL_EMAIL

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 0.2


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def lookup_unpaywall(doi, timeout=10, max_retries=3):
    url = f"https://api.unpaywall.org/v2/{doi}"
    params = {"email": UNPAYWALL_EMAIL}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            data = resp.json()
            best = data.get("best_oa_location") or {}
            return {
                "is_oa": data.get("is_oa", False),
                "pdf_url": best.get("url_for_pdf"),
            }
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_clients.py -k unpaywall -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api_clients/unpaywall.py tests/test_api_clients.py
git commit -m "feat: unpaywall API client for open-access PDF lookup"
```

---

### Task 6: API Clients — Semantic Scholar

**Files:**
- Create: `api_clients/semantic_scholar.py`
- Modify: `tests/test_api_clients.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_clients.py`:

```python
from api_clients.semantic_scholar import lookup_semantic_scholar


def test_s2_search_by_doi():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "paperId": "abc123",
        "title": "Machine Learning Study",
        "abstract": "This paper studies ML.",
        "year": 2020,
        "citationCount": 42,
        "isOpenAccess": True,
        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
        "authors": [{"name": "John Smith"}],
        "externalIds": {"DOI": "10.1234/test"}
    }
    with patch("api_clients.semantic_scholar.requests.get", return_value=mock_resp):
        result = lookup_semantic_scholar(doi="10.1234/test")
    assert result["abstract"] == "This paper studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["citation_count"] == 42


def test_s2_search_by_title():
    search_resp = Mock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "data": [{
            "paperId": "abc123",
            "title": "Machine Learning Study",
            "year": 2020,
            "authors": [{"name": "John Smith"}]
        }]
    }
    detail_resp = Mock()
    detail_resp.status_code = 200
    detail_resp.json.return_value = {
        "paperId": "abc123",
        "title": "Machine Learning Study",
        "abstract": "This paper studies ML.",
        "year": 2020,
        "citationCount": 42,
        "isOpenAccess": False,
        "openAccessPdf": None,
        "authors": [{"name": "John Smith"}],
        "externalIds": {"DOI": "10.1234/test"}
    }
    with patch("api_clients.semantic_scholar.requests.get", side_effect=[search_resp, detail_resp]):
        result = lookup_semantic_scholar(title="Machine Learning Study", year="2020")
    assert result["abstract"] == "This paper studies ML."


def test_s2_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.semantic_scholar.requests.get", return_value=mock_resp):
        result = lookup_semantic_scholar(doi="10.9999/fake")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_clients.py -k s2 -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement semantic_scholar.py**

```python
# api_clients/semantic_scholar.py
import threading
import time
import re
import requests

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 0.5

FIELDS = "paperId,title,abstract,year,citationCount,isOpenAccess,openAccessPdf,authors,externalIds"


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _parse_paper(data):
    if not data:
        return None
    oa_pdf = data.get("openAccessPdf") or {}
    authors = [a.get("name", "") for a in data.get("authors", [])]
    ext_ids = data.get("externalIds") or {}
    return {
        "title": data.get("title"),
        "abstract": data.get("abstract"),
        "year": str(data["year"]) if data.get("year") else None,
        "citation_count": data.get("citationCount"),
        "pdf_url": oa_pdf.get("url"),
        "authors": authors,
        "doi": ext_ids.get("DOI"),
    }


def lookup_semantic_scholar(doi=None, title=None, year=None, authors_hint=None,
                            timeout=10, max_retries=3):
    if doi:
        return _lookup_by_doi(doi, timeout, max_retries)
    if title:
        return _lookup_by_title(title, year, authors_hint, timeout, max_retries)
    return None


def _lookup_by_doi(doi, timeout, max_retries):
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            return _parse_paper(resp.json())
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def _lookup_by_title(title, year, authors_hint, timeout, max_retries):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": title, "limit": 5, "fields": "paperId,title,year,authors"}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", [])
            if not data:
                return None
            best = _pick_best(data, title, year, authors_hint)
            if not best:
                return None
            # Fetch full details
            return _fetch_details(best["paperId"], timeout, max_retries)
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def _pick_best(candidates, title, year, authors_hint):
    norm_title = _normalize(title)
    best_score = -1
    best = None
    for c in candidates:
        score = 0
        c_title = _normalize(c.get("title", ""))
        if c_title == norm_title:
            score += 3
        elif norm_title in c_title or c_title in norm_title:
            score += 1
        if year and str(c.get("year", "")) == str(year):
            score += 2
        if authors_hint and c.get("authors"):
            first_author = c["authors"][0].get("name", "").lower()
            if authors_hint.lower().split(",")[0].split()[-1] in first_author:
                score += 1
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= 4 else None


def _fetch_details(paper_id, timeout, max_retries):
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
    params = {"fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            return _parse_paper(resp.json())
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_clients.py -k s2 -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api_clients/semantic_scholar.py tests/test_api_clients.py
git commit -m "feat: semantic scholar API client with title disambiguation"
```

---

### Task 7: API Clients — Scholarly (Google Scholar fallback)

**Files:**
- Create: `api_clients/scholarly_client.py`
- Modify: `tests/test_api_clients.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_clients.py`:

```python
from api_clients.scholarly_client import lookup_scholarly


def test_scholarly_finds_paper():
    mock_result = {
        "bib": {
            "title": "Machine Learning Study",
            "abstract": "This paper studies ML.",
            "author": ["John Smith"],
            "pub_year": "2020",
            "venue": "Journal of AI",
        },
        "pub_url": "https://example.com/paper",
        "eprint_url": "https://example.com/paper.pdf",
    }
    with patch("api_clients.scholarly_client.scholarly") as mock_scholarly:
        mock_scholarly.search_pubs.return_value = iter([mock_result])
        result = lookup_scholarly("Machine Learning Study")
    assert result["abstract"] == "This paper studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"


def test_scholarly_no_results():
    with patch("api_clients.scholarly_client.scholarly") as mock_scholarly:
        mock_scholarly.search_pubs.return_value = iter([])
        result = lookup_scholarly("Nonexistent Paper XYZ123")
    assert result is None


def test_scholarly_disabled():
    with patch("api_clients.scholarly_client.SCHOLARLY_ENABLED", False):
        result = lookup_scholarly("Any Title")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_clients.py -k scholarly -v
```

Expected: FAIL.

- [ ] **Step 3: Implement scholarly_client.py**

```python
# api_clients/scholarly_client.py
import threading
import time
import logging
from config import SCHOLARLY_ENABLED

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 1.0
_disabled = False

try:
    from scholarly import scholarly
except ImportError:
    scholarly = None


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def lookup_scholarly(title, timeout=15):
    global _disabled
    if not SCHOLARLY_ENABLED or _disabled or scholarly is None:
        return None
    try:
        _rate_limit()
        results = scholarly.search_pubs(title)
        first = next(results, None)
        if not first:
            return None
        bib = first.get("bib", {})
        return {
            "title": bib.get("title"),
            "abstract": bib.get("abstract"),
            "authors": bib.get("author", []),
            "year": bib.get("pub_year"),
            "journal": bib.get("venue"),
            "url": first.get("pub_url"),
            "pdf_url": first.get("eprint_url"),
        }
    except Exception as e:
        logger.warning(f"Scholarly lookup failed: {e}. Disabling for this session.")
        _disabled = True
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_clients.py -k scholarly -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api_clients/scholarly_client.py tests/test_api_clients.py
git commit -m "feat: scholarly (Google Scholar) fallback client with self-disable"
```

---

### Task 8: Lookup Engine

**Files:**
- Create: `lookup_engine.py`
- Create: `tests/test_lookup_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import patch, MagicMock
from lookup_engine import process_reference, process_all


def test_process_reference_with_doi():
    ref = {"bib_key": "smith2020", "title": "ML Study", "doi": "10.1234/test",
           "authors": "Smith, John", "year": "2020", "journal": "J AI", "url": None, "status": None}

    crossref_data = {"title": "ML Study", "authors": ["John Smith"],
                     "journal": "J AI", "year": "2020", "url": "https://doi.org/10.1234/test"}
    unpaywall_data = {"is_oa": True, "pdf_url": "https://example.com/paper.pdf"}
    s2_data = {"title": "ML Study", "abstract": "Studies ML.", "year": "2020",
               "citation_count": 42, "pdf_url": None, "authors": ["John Smith"], "doi": "10.1234/test"}

    with patch("lookup_engine.lookup_crossref", return_value=crossref_data), \
         patch("lookup_engine.lookup_unpaywall", return_value=unpaywall_data), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=s2_data):
        result = process_reference(ref)

    assert result["status"] == "found_pdf"
    assert result["abstract"] == "Studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert "crossref" in result["sources"]


def test_process_reference_abstract_only():
    ref = {"bib_key": "jones2019", "title": "DL in Practice", "doi": None,
           "authors": "Jones, Bob", "year": "2019", "journal": None, "url": None, "status": None}

    s2_data = {"title": "DL in Practice", "abstract": "Deep learning stuff.",
               "year": "2019", "citation_count": 10, "pdf_url": None,
               "authors": ["Bob Jones"], "doi": None}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=s2_data), \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    assert result["status"] == "found_abstract"
    assert result["abstract"] == "Deep learning stuff."


def test_process_reference_not_found():
    ref = {"bib_key": "unknown", "title": "Unknown Paper", "doi": None,
           "authors": "", "year": None, "journal": None, "url": None, "status": None}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    assert result["status"] == "not_found"


def test_process_reference_insufficient_data():
    ref = {"bib_key": "empty", "title": None, "doi": None,
           "authors": "", "year": None, "journal": None, "url": None, "status": "insufficient_data"}
    result = process_reference(ref)
    assert result["status"] == "insufficient_data"


def test_process_all_calls_callback():
    refs = [
        {"bib_key": "a", "title": "Paper A", "doi": None,
         "authors": "", "year": None, "journal": None, "url": None, "status": None},
    ]
    callback = MagicMock()
    with patch("lookup_engine.process_reference", return_value={
        "bib_key": "a", "status": "not_found", "sources": []
    }):
        process_all(refs, callback=callback, max_workers=1)
    callback.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_lookup_engine.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement lookup_engine.py**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from api_clients.crossref import lookup_crossref
from api_clients.unpaywall import lookup_unpaywall
from api_clients.semantic_scholar import lookup_semantic_scholar
from api_clients.scholarly_client import lookup_scholarly
from config import MAX_WORKERS


def process_reference(ref):
    if ref.get("status") == "insufficient_data":
        return {
            "bib_key": ref["bib_key"],
            "title": ref.get("title"),
            "authors": [],
            "year": None,
            "journal": None,
            "doi": None,
            "abstract": None,
            "pdf_url": None,
            "url": None,
            "citation_count": None,
            "sources": [],
            "status": "insufficient_data",
            "error": "No title or DOI in .bib entry",
        }

    title = ref.get("title")
    doi = ref.get("doi")
    authors = ref.get("authors", "")
    year = ref.get("year")

    result = {
        "bib_key": ref["bib_key"],
        "title": title,
        "authors": authors if isinstance(authors, list) else [authors] if authors else [],
        "year": year,
        "journal": ref.get("journal"),
        "doi": doi,
        "abstract": None,
        "pdf_url": None,
        "url": ref.get("url"),
        "citation_count": None,
        "sources": [],
        "status": "not_found",
        "error": None,
    }

    # Step 1: CrossRef + Unpaywall (if DOI available)
    if doi:
        cr = lookup_crossref(doi)
        if cr:
            result["sources"].append("crossref")
            result["title"] = result["title"] or cr.get("title")
            result["authors"] = cr.get("authors") or result["authors"]
            result["journal"] = result["journal"] or cr.get("journal")
            result["year"] = result["year"] or cr.get("year")
            result["url"] = result["url"] or cr.get("url")

        uw = lookup_unpaywall(doi)
        if uw:
            result["sources"].append("unpaywall")
            if uw.get("pdf_url"):
                result["pdf_url"] = uw["pdf_url"]

    # Step 2: Semantic Scholar
    s2 = lookup_semantic_scholar(doi=doi, title=title, year=year, authors_hint=authors)
    if s2:
        result["sources"].append("semantic_scholar")
        result["abstract"] = result["abstract"] or s2.get("abstract")
        result["citation_count"] = s2.get("citation_count")
        result["doi"] = result["doi"] or s2.get("doi")
        if not result["pdf_url"] and s2.get("pdf_url"):
            result["pdf_url"] = s2["pdf_url"]
        # If we got a DOI from S2 and didn't have one, try Unpaywall now
        if result["doi"] and not doi and not result["pdf_url"]:
            uw = lookup_unpaywall(result["doi"])
            if uw and uw.get("pdf_url"):
                result["pdf_url"] = uw["pdf_url"]
                if "unpaywall" not in result["sources"]:
                    result["sources"].append("unpaywall")

    # Step 3: Scholarly fallback
    if not result["abstract"] and not result["pdf_url"] and title:
        sch = lookup_scholarly(title)
        if sch:
            result["sources"].append("scholarly")
            result["abstract"] = result["abstract"] or sch.get("abstract")
            if not result["pdf_url"] and sch.get("pdf_url"):
                result["pdf_url"] = sch["pdf_url"]

    # Determine final status
    if result["pdf_url"]:
        result["status"] = "found_pdf"
    elif result["abstract"]:
        result["status"] = "found_abstract"
    else:
        result["status"] = "not_found"

    return result


def process_all(refs, callback=None, max_workers=None):
    workers = max_workers or MAX_WORKERS
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {
            executor.submit(process_reference, ref): i
            for i, ref in enumerate(refs)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "bib_key": refs[idx]["bib_key"],
                    "title": refs[idx].get("title"),
                    "authors": [],
                    "year": None,
                    "journal": None,
                    "doi": None,
                    "abstract": None,
                    "pdf_url": None,
                    "url": None,
                    "citation_count": None,
                    "sources": [],
                    "status": "not_found",
                    "error": str(e),
                }
            results.append(result)
            if callback:
                callback(idx, result)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_lookup_engine.py -v
```

Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add lookup_engine.py tests/test_lookup_engine.py
git commit -m "feat: lookup engine with chained API calls and thread pool"
```

---

### Task 9: Report Exporter

**Files:**
- Create: `report_exporter.py`
- Create: `tests/test_report_exporter.py`

- [ ] **Step 1: Write the failing tests**

```python
import csv
import io
from report_exporter import export_csv, export_pdf


SAMPLE_RESULTS = [
    {
        "bib_key": "smith2020",
        "title": "ML Study",
        "authors": ["John Smith"],
        "year": "2020",
        "journal": "J AI",
        "doi": "10.1234/test",
        "abstract": "Studies ML.",
        "pdf_url": "https://example.com/paper.pdf",
        "url": "https://doi.org/10.1234/test",
        "citation_count": 42,
        "sources": ["crossref", "semantic_scholar"],
        "status": "found_pdf",
        "error": None,
    }
]


def test_export_csv():
    output = export_csv(SAMPLE_RESULTS)
    reader = csv.DictReader(io.StringIO(output))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["title"] == "ML Study"
    assert rows[0]["status"] == "found_pdf"
    assert "bib_key" in reader.fieldnames


def test_export_pdf():
    pdf_bytes = export_pdf(SAMPLE_RESULTS)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 100
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_report_exporter.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement report_exporter.py**

```python
import csv
import io
from fpdf import FPDF

CSV_FIELDS = [
    "bib_key", "title", "authors", "year", "journal", "doi",
    "abstract", "pdf_url", "url", "citation_count", "sources", "status"
]


def export_csv(results):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        row = dict(r)
        row["authors"] = "; ".join(row.get("authors", []))
        row["sources"] = ", ".join(row.get("sources", []))
        writer.writerow(row)
    return output.getvalue()


def export_pdf(results):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "References Checker Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # Summary
    total = len(results)
    found_pdf = sum(1 for r in results if r["status"] == "found_pdf")
    found_abstract = sum(1 for r in results if r["status"] == "found_abstract")
    not_found = total - found_pdf - found_abstract
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Total: {total} | PDF found: {found_pdf} | Abstract only: {found_abstract} | Not found: {not_found}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # References
    for i, r in enumerate(results, 1):
        pdf.set_font("Helvetica", "B", 11)
        title = r.get("title") or "(No title)"
        pdf.multi_cell(0, 6, f"{i}. {title}")
        pdf.set_font("Helvetica", "", 9)
        authors = "; ".join(r.get("authors", []))
        if authors:
            pdf.cell(0, 5, f"Authors: {authors}", new_x="LMARGIN", new_y="NEXT")
        if r.get("year"):
            pdf.cell(0, 5, f"Year: {r['year']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("journal"):
            pdf.cell(0, 5, f"Journal: {r['journal']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("doi"):
            pdf.cell(0, 5, f"DOI: {r['doi']}", new_x="LMARGIN", new_y="NEXT")
        status_label = {"found_pdf": "Full Paper", "found_abstract": "Abstract Only",
                        "not_found": "Not Found", "insufficient_data": "Insufficient Data",
                        "parse_error": "Parse Error"}.get(r["status"], r["status"])
        pdf.cell(0, 5, f"Status: {status_label}", new_x="LMARGIN", new_y="NEXT")
        if r.get("pdf_url"):
            pdf.cell(0, 5, f"PDF: {r['pdf_url']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("abstract"):
            pdf.set_font("Helvetica", "I", 8)
            pdf.multi_cell(0, 4, f"Abstract: {r['abstract'][:500]}")
        pdf.ln(4)

    return pdf.output()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_report_exporter.py -v
```

Expected: All 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add report_exporter.py tests/test_report_exporter.py
git commit -m "feat: CSV and PDF report exporter"
```

---

### Task 10: Flask App — Routes and SSE

**Files:**
- Create: `app.py`
- Create: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

```python
import json
from unittest.mock import patch, MagicMock
from app import create_app


def test_index_page():
    app = create_app()
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"References Checker" in resp.data


def test_upload_valid_bib():
    app = create_app()
    client = app.test_client()
    bib_content = b"""@article{test, title={Test Paper}, year={2020}}"""
    with patch("app.threading.Thread"):
        resp = client.post("/upload", data={"file": (bib_content, "test.bib")},
                          content_type="multipart/form-data")
    data = json.loads(resp.data)
    assert resp.status_code == 200
    assert "session_id" in data
    assert data["total"] >= 1


def test_upload_no_file():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload")
    assert resp.status_code == 400


def test_upload_empty_file():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload", data={"file": (b"", "empty.bib")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400


def test_download_csv():
    app = create_app()
    client = app.test_client()
    # Create a session with results directly
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="completed")
        store.add_result(sid, {
            "bib_key": "test", "title": "Test", "authors": ["A"],
            "year": "2020", "journal": "J", "doi": None, "abstract": "Ab",
            "pdf_url": None, "url": None, "citation_count": 0,
            "sources": [], "status": "found_abstract", "error": None
        })
    resp = client.get(f"/download/{sid}/csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type


def test_download_pdf():
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="completed")
        store.add_result(sid, {
            "bib_key": "test", "title": "Test", "authors": ["A"],
            "year": "2020", "journal": "J", "doi": None, "abstract": "Ab",
            "pdf_url": None, "url": None, "citation_count": 0,
            "sources": [], "status": "found_abstract", "error": None
        })
    resp = client.get(f"/download/{sid}/pdf")
    assert resp.status_code == 200
    assert "application/pdf" in resp.content_type


def test_download_while_processing_returns_409():
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="processing")
    resp = client.get(f"/download/{sid}/csv")
    assert resp.status_code == 409


def test_upload_wrong_extension():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload", data={"file": (b"some content", "test.txt")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400


def test_download_nonexistent_session():
    app = create_app()
    client = app.test_client()
    resp = client.get("/download/nonexistent/csv")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_app.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement app.py**

```python
import json
import threading
import tempfile
import os
from flask import Flask, request, jsonify, Response, render_template, send_file
from session_store import SessionStore
from bib_parser import parse_bib_file
from lookup_engine import process_all
from report_exporter import export_csv, export_pdf
from config import MAX_UPLOAD_SIZE, FLASK_PORT

store = SessionStore()


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Maximum size is 2MB."}), 413

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/upload", methods=["POST"])
    def upload():
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".bib"):
            return jsonify({"error": "Please upload a .bib file"}), 400

        content = file.read()
        if not content.strip():
            return jsonify({"error": "File is empty"}), 400

        # Save to temp file for parsing
        tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".bib", delete=False)
        tmp.write(content)
        tmp.close()

        try:
            refs = parse_bib_file(tmp.name)
        finally:
            os.unlink(tmp.name)

        if not refs:
            return jsonify({"error": "No valid references found in file"}), 400

        sid = store.create()
        store.update(sid, status="processing", total=len(refs))

        # Process in background thread
        def _process():
            def on_result(idx, result):
                store.add_result(sid, result)
            process_all(refs, callback=on_result)
            store.update(sid, status="completed")

        t = threading.Thread(target=_process, daemon=True)
        t.start()

        warning = None
        if len(refs) > 500:
            warning = f"Large file with {len(refs)} references. This may take several minutes."

        return jsonify({"session_id": sid, "total": len(refs), "warning": warning})

    @app.route("/stream/<session_id>")
    def stream(session_id):
        session = store.get(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404

        def generate():
            import time
            # Support reconnection: resume from Last-Event-ID
            last_id = request.headers.get("Last-Event-ID")
            sent = int(last_id) + 1 if last_id and last_id.isdigit() else 0
            last_heartbeat = time.time()
            while True:
                session = store.get(session_id)
                if session is None:
                    break

                results = session["results"]
                total = session["total"]

                # Send new results
                while sent < len(results):
                    r = results[sent]
                    event_data = json.dumps({
                        "index": sent,
                        "total": total,
                        "bib_key": r.get("bib_key"),
                        "status": r.get("status"),
                        "result": r,
                    })
                    # Emit error event for failed lookups, progress for successful ones
                    if r.get("error"):
                        yield f"id: {sent}\nevent: error\ndata: {json.dumps({'index': sent, 'total': total, 'bib_key': r.get('bib_key'), 'message': r.get('error')})}\n\n"
                    else:
                        yield f"id: {sent}\nevent: progress\ndata: {event_data}\n\n"
                    sent += 1

                # Check if done
                if session["status"] == "completed" and sent >= total:
                    found_pdf = sum(1 for r in results if r["status"] == "found_pdf")
                    found_abstract = sum(1 for r in results if r["status"] == "found_abstract")
                    not_found = total - found_pdf - found_abstract
                    done_data = json.dumps({
                        "total": total,
                        "found_pdf": found_pdf,
                        "found_abstract": found_abstract,
                        "not_found": not_found,
                    })
                    yield f"event: complete\ndata: {done_data}\n\n"
                    break

                # Heartbeat
                now = time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now

                time.sleep(0.3)

        return Response(generate(), mimetype="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/download/<session_id>/<fmt>")
    def download(session_id, fmt):
        session = store.get(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404

        if session["status"] == "processing":
            return jsonify({"error": "Processing still in progress"}), 409

        results = session["results"]

        if fmt == "csv":
            csv_data = export_csv(results)
            return Response(csv_data, mimetype="text/csv",
                          headers={"Content-Disposition": "attachment; filename=references_report.csv"})
        elif fmt == "pdf":
            pdf_data = export_pdf(results)
            return Response(pdf_data, mimetype="application/pdf",
                          headers={"Content-Disposition": "attachment; filename=references_report.pdf"})
        else:
            return jsonify({"error": "Invalid format. Use 'csv' or 'pdf'"}), 400

    return app


if __name__ == "__main__":
    store.start_cleanup_thread()
    app = create_app()
    app.run(debug=True, port=FLASK_PORT, threaded=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_app.py -v
```

Expected: All 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: flask app with upload, SSE streaming, and download routes"
```

---

### Task 11: Frontend — HTML Template

**Files:**
- Create: `templates/index.html`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p templates static/css static/js
```

- [ ] **Step 2: Create index.html**

Create `templates/index.html` — the single-page HTML shell with three view states (upload, processing, results). Use ui-ux-pro-max skill and Magic MCP for component design.

The HTML should include:
- Upload zone with drag-and-drop
- Processing view with progress bar and live card feed
- Results view with stats, filter, cards, and download buttons
- Links to `static/css/style.css` and `static/js/app.js`

- [ ] **Step 3: Verify template renders**

```bash
python -c "from app import create_app; app = create_app(); client = app.test_client(); r = client.get('/'); print('OK' if r.status_code == 200 else 'FAIL')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: HTML template with upload, processing, and results views"
```

---

### Task 12: Frontend — CSS Styling

**Files:**
- Create: `static/css/style.css`

- [ ] **Step 1: Create style.css**

Use ui-ux-pro-max skill for the design system. Style all three states:
- Upload: centered card with drag-and-drop zone
- Processing: progress bar + scrolling card feed
- Results: stats bar, filter input, responsive card grid
- Color-coded status: green (found_pdf), amber (found_abstract), red (not_found)
- Status icons alongside colors for accessibility
- Responsive: works on desktop and tablet
- Modern, clean aesthetic

- [ ] **Step 2: Verify styles load**

Run the app and check in browser that styles are applied.

- [ ] **Step 3: Commit**

```bash
git add static/css/style.css
git commit -m "feat: UI styling with responsive layout and status colors"
```

---

### Task 13: Frontend — JavaScript

**Files:**
- Create: `static/js/app.js`

- [ ] **Step 1: Implement app.js**

Handle all three states:

**Upload logic:**
- Drag-and-drop events on the drop zone
- File input change handler
- POST to `/upload` with FormData
- Show error messages for invalid files
- On success: switch to processing view, open SSE

**SSE streaming:**
- `new EventSource("/stream/" + sessionId)`
- Handle `progress` events: update progress bar, render result card
- Handle `complete` event: switch to results view, show stats
- Handle `heartbeat`: keep connection alive
- Handle `error` / reconnect logic

**Results view:**
- Filter/search: filter cards by title/author text
- Expandable abstracts (click to toggle)
- Download buttons: `window.location = "/download/" + sid + "/csv"` etc.

- [ ] **Step 2: Test manually**

Run the app, upload `tests/fixtures/sample.bib`, verify the full flow works end-to-end.

- [ ] **Step 3: Commit**

```bash
git add static/js/app.js
git commit -m "feat: frontend JS with upload, SSE streaming, and results rendering"
```

---

### Task 14: End-to-End Testing and Polish

**Files:**
- Modify: various files for bug fixes

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Manual end-to-end test**

1. Start the app: `python app.py`
2. Open `http://localhost:5000`
3. Upload a .bib file
4. Verify: progress bar updates, cards appear live
5. Verify: results show correct status colors and icons
6. Verify: filter works
7. Verify: CSV and PDF downloads work
8. Verify: expanding abstracts works

- [ ] **Step 3: Fix any bugs found**

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: end-to-end testing and polish"
```

---

## Task Dependency Order

```
Task 1 (Setup)
  └─> Task 2 (Session Store)
  └─> Task 3 (Bib Parser)
  └─> Task 4 (CrossRef) ─> Task 5 (Unpaywall) ─> Task 6 (S2) ─> Task 7 (Scholarly)
       └────────────────────────────────────────────────────────> Task 8 (Lookup Engine)
  └─> Task 9 (Report Exporter)
       └─> Task 10 (Flask App) — depends on Tasks 2, 3, 8, 9
            └─> Task 11 (HTML) ─> Task 12 (CSS) ─> Task 13 (JS)
                 └─> Task 14 (E2E Testing)
```

**Parallelizable groups:**
- Tasks 2, 3, 4 can start simultaneously after Task 1
- Task 9 can run in parallel with Tasks 4-8
- Tasks 11-13 (frontend) can be worked on in parallel once Task 10 is done
