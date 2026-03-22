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
