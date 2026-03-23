import threading
import time
import re
import logging
import requests

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 1.1  # S2 free tier: ~1 req/sec
_blocked = False

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
    global _blocked
    if _blocked:
        return None
    if doi:
        return _lookup_by_doi(doi, timeout, max_retries)
    if title:
        return _lookup_by_title(title, year, authors_hint, timeout, max_retries)
    return None


def _handle_429(attempt):
    """Handle rate limiting. If we've been blocked too many times, disable for session."""
    global _blocked
    wait = 10 * (attempt + 1)  # 10s, 20s, 30s
    logger.warning(f"S2 rate limited (429). Waiting {wait}s (attempt {attempt+1})")
    time.sleep(wait)


def _lookup_by_doi(doi, timeout, max_retries):
    global _blocked
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                _handle_429(attempt)
                if attempt == max_retries - 1:
                    _blocked = True
                    logger.warning("S2 persistently rate-limited. Disabling for session.")
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
    global _blocked
    # Use the /match endpoint first — single call, returns best match with full fields
    url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    params = {"query": title, "fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                _handle_429(attempt)
                if attempt == max_retries - 1:
                    _blocked = True
                    logger.warning("S2 persistently rate-limited. Disabling for session.")
                continue
            if resp.status_code == 404:
                # Match endpoint returns 404 when no match found
                return None
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", [])
            if not data:
                return None
            # Match endpoint returns best match first — verify it's reasonable
            best = data[0]
            norm_query = _normalize(title)
            norm_result = _normalize(best.get("title", ""))
            # Accept if titles are similar enough
            if norm_query == norm_result:
                return _parse_paper(best)
            if norm_query in norm_result or norm_result in norm_query:
                return _parse_paper(best)
            # Fuzzy check: if >70% of words overlap
            query_words = set(norm_query.split())
            result_words = set(norm_result.split())
            if query_words and result_words:
                overlap = len(query_words & result_words) / max(len(query_words), len(result_words))
                if overlap > 0.6:
                    return _parse_paper(best)
            return None
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
    return best if best_score >= 3 else None
