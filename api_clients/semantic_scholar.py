import threading
import time
import re
import logging
import requests
from http_client import get_session
from config import SEMANTIC_SCHOLAR_API_KEY

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 1.2 if SEMANTIC_SCHOLAR_API_KEY else 3.5  # Authenticated: 1 req/s (with margin); free: conservative
_blocked = False
_HEADERS = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}

logger.info("S2 client initialized: api_key=%s, delay=%.2fs",
            "present" if SEMANTIC_SCHOLAR_API_KEY else "MISSING", _DELAY)

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
                            timeout=10, max_retries=2):
    global _blocked
    if _blocked:
        logger.debug("S2 query skipped (blocked): doi=%s title=%s", doi, title)
        return None
    logger.debug("S2 query: doi=%s title=%s year=%s", doi, title, year)
    if doi:
        result = _lookup_by_doi(doi, timeout, max_retries)
    elif title:
        result = _lookup_by_title(title, year, authors_hint, timeout, max_retries)
    else:
        return None
    logger.debug("S2 result: doi=%s title=%s found=%s", doi, title, result is not None)
    if result:
        logger.debug("S2 detail: abstract=%s pdf_url=%s citations=%s",
                      bool(result.get("abstract")), result.get("pdf_url"), result.get("citation_count"))
    return result


def _handle_429(attempt):
    """Handle rate limiting. Back off briefly then retry."""
    global _blocked, _DELAY
    wait = 3 * (attempt + 1)  # 3s, 6s, 9s
    # Increase delay between future calls to avoid further 429s
    _DELAY = min(_DELAY + 0.5, 5.0)
    logger.warning(f"S2 rate limited (429). Waiting {wait}s, increasing delay to {_DELAY}s (attempt {attempt+1})")
    time.sleep(wait)


def _lookup_by_doi(doi, timeout, max_retries):
    global _blocked
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = get_session().get(url, params=params, headers=_HEADERS, timeout=timeout)
            logger.debug("S2 DOI response: status=%d doi=%s", resp.status_code, doi)
            if resp.status_code == 429:
                _handle_429(attempt)
                if attempt == max_retries - 1:
                    _blocked = True
                    logger.warning("S2 persistently rate-limited. Disabling for session.")
                continue
            if resp.status_code != 200:
                logger.debug("S2 DOI failed: doi=%s status=%d body=%s", doi, resp.status_code, resp.text[:200])
                return None
            return _parse_paper(resp.json())
        except Exception as e:
            logger.debug("S2 DOI error: doi=%s attempt=%d error=%s", doi, attempt, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def _title_matches(norm_query, norm_result):
    """Check if two normalized titles match reasonably."""
    if norm_query == norm_result:
        return True
    if norm_query in norm_result or norm_result in norm_query:
        return True
    query_words = set(norm_query.split())
    result_words = set(norm_result.split())
    if query_words and result_words:
        overlap = len(query_words & result_words) / max(len(query_words), len(result_words))
        if overlap > 0.6:
            return True
    return False


def _lookup_by_title(title, year, authors_hint, timeout, max_retries):
    global _blocked
    # Use the /match endpoint first — single call, returns best match with full fields
    url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    params = {"query": title, "fields": FIELDS}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = get_session().get(url, params=params, headers=_HEADERS, timeout=timeout)
            logger.debug("S2 title response: status=%d title=%s", resp.status_code, title)
            if resp.status_code == 429:
                _handle_429(attempt)
                if attempt == max_retries - 1:
                    _blocked = True
                    logger.warning("S2 persistently rate-limited. Disabling for session.")
                continue
            if resp.status_code == 404:
                logger.debug("S2 title no match (404): title=%s", title)
                break
            if resp.status_code != 200:
                logger.debug("S2 title failed: title=%s status=%d body=%s", title, resp.status_code, resp.text[:200])
                return None
            data = resp.json().get("data", [])
            if not data:
                logger.debug("S2 title empty results: title=%s", title)
                break
            best = data[0]
            norm_query = _normalize(title)
            norm_result = _normalize(best.get("title", ""))
            if _title_matches(norm_query, norm_result):
                parsed = _parse_paper(best)
                if parsed and parsed.get("abstract"):
                    return parsed
                # Match found but no abstract — fall through to /search
                logger.debug("S2 match has no abstract, trying /search: title=%s matched=%s", title, best.get("title"))
                break
            logger.debug("S2 title mismatch: query=%s result=%s",
                          norm_query[:80], norm_result[:80])
            break
        except Exception as e:
            logger.debug("S2 title error: title=%s attempt=%d error=%s", title, attempt, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue

    # Fallback: /search endpoint returns multiple results we can score
    if _blocked:
        return None
    return _search_by_title(title, year, authors_hint, timeout, max_retries)


def _search_by_title(title, year, authors_hint, timeout, max_retries):
    """Fallback search using the bulk /search endpoint."""
    global _blocked
    query = title
    if year:
        query += f" {year}"
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": query, "fields": FIELDS, "limit": 10}
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = get_session().get(url, params=params, headers=_HEADERS, timeout=timeout)
            logger.debug("S2 search response: status=%d title=%s", resp.status_code, title)
            if resp.status_code == 429:
                _handle_429(attempt)
                if attempt == max_retries - 1:
                    _blocked = True
                    logger.warning("S2 persistently rate-limited. Disabling for session.")
                continue
            if resp.status_code != 200:
                logger.debug("S2 search failed: title=%s status=%d body=%s", title, resp.status_code, resp.text[:200])
                return None
            candidates = resp.json().get("data", [])
            if not candidates:
                logger.debug("S2 search no results: title=%s", title)
                return None
            best = _pick_best(candidates, title, year, authors_hint)
            if best:
                logger.debug("S2 search picked: title=%s -> %s", title, best.get("title"))
                return _parse_paper(best)
            logger.debug("S2 search no good match: title=%s candidates=%d", title, len(candidates))
            return None
        except Exception as e:
            logger.debug("S2 search error: title=%s attempt=%d error=%s", title, attempt, e)
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
        # Prefer results that have an abstract
        if c.get("abstract"):
            score += 1
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= 3 else None
