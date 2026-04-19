import threading
import time
import logging
import re
import requests
from http_client import get_session
from bs4 import BeautifulSoup
from config import SCHOLARLY_ENABLED

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 2.0  # Be conservative with Google Scholar
_disabled = False
_consecutive_failures = 0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def lookup_scholarly(title, timeout=15):
    global _disabled, _consecutive_failures
    if not SCHOLARLY_ENABLED or _disabled:
        logger.debug("Scholarly query skipped (disabled=%s): title=%s", _disabled, title)
        return None
    logger.debug("Scholarly query: title=%s", title)
    try:
        _rate_limit()
        result = _search_google_scholar(title, timeout)
        if result:
            _consecutive_failures = 0
            logger.debug("Scholarly result: title=%s url=%s pdf=%s abstract=%s",
                          title, result.get("url"), result.get("pdf_url"), bool(result.get("abstract")))
        else:
            logger.debug("Scholarly result: title=%s found=None", title)
        return result
    except Exception as e:
        _consecutive_failures += 1
        logger.warning(f"Google Scholar lookup failed: {e}")
        if _consecutive_failures >= 3:
            logger.warning("Google Scholar: 3 consecutive failures. Disabling for session.")
            _disabled = True
        return None


_TITLE_OVERLAP_THRESHOLD = 0.6  # min fraction of query words that must appear in result title


def _normalize(text):
    """Lowercase and strip non-alphanumerics for word-overlap comparison."""
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).strip()


def _is_relevant(query_title, result_title):
    """Reject results whose title doesn't overlap enough with the query.

    Scholar's broad (no-quote) retry happily returns related-but-different papers
    for unique-sounding bib titles (regression: chiang2025llm "LLMs for Corporate
    Transparency: Evaluating Earnings Call Q&A" matched a different COLING 2025
    paper). Without this guard, the wrong paper gets downloaded as the source.
    """
    if not result_title:
        return False
    qwords = set(_normalize(query_title).split())
    twords = set(_normalize(result_title).split())
    if not qwords:
        return True
    overlap = len(qwords & twords) / len(qwords)
    return overlap >= _TITLE_OVERLAP_THRESHOLD


def _parse_scholar_result(result):
    """Extract title/url/abstract/pdf/meta from one Scholar result element."""
    title_el = result.select_one("h3.gs_rt a")
    found_title = title_el.get_text(strip=True) if title_el else None
    found_url = title_el["href"] if title_el and title_el.has_attr("href") else None

    abstract_el = result.select_one("div.gs_rs")
    abstract = abstract_el.get_text(strip=True) if abstract_el else None

    pdf_url = None
    pdf_link = result.select_one("div.gs_ggs a")
    if pdf_link and pdf_link.has_attr("href"):
        href = pdf_link["href"]
        if href.endswith(".pdf") or "pdf" in href.lower():
            pdf_url = href

    meta_el = result.select_one("div.gs_a")
    authors, year, journal = [], None, None
    if meta_el:
        meta_text = meta_el.get_text(strip=True)
        parts = meta_text.split(" - ")
        if parts:
            authors = [a.strip() for a in parts[0].split(",") if a.strip() and not a.strip().isdigit()]
        year_match = re.search(r'\b(19|20)\d{2}\b', meta_text)
        if year_match:
            year = year_match.group()
        if len(parts) > 1:
            journal = parts[1].strip().rstrip(",").strip()
            journal = re.sub(r',?\s*(19|20)\d{2}', '', journal).strip()

    return {
        "title": found_title,
        "abstract": abstract,
        "authors": authors,
        "year": year,
        "journal": journal or None,
        "url": found_url,
        "pdf_url": pdf_url,
    }


def _pick_relevant(results, query_title):
    """Walk Scholar results and return the first whose title passes the overlap
    threshold. Returns None if none qualify (better than returning a wrong paper).
    """
    for r in results:
        parsed = _parse_scholar_result(r)
        if not parsed.get("title") and not parsed.get("abstract"):
            continue
        if _is_relevant(query_title, parsed.get("title")):
            return parsed
        logger.debug("Scholarly skipping low-overlap result: query=%r got=%r",
                     query_title, parsed.get("title"))
    return None


def _search_google_scholar(title, timeout):
    """Search Google Scholar directly via HTTP and parse the HTML results."""
    url = "https://scholar.google.com/scholar"
    params = {"q": f'"{title}"', "hl": "en", "num": 3}

    resp = get_session().get(url, params=params, headers=HEADERS, timeout=timeout)

    if resp.status_code == 429 or "captcha" in resp.text.lower():
        global _disabled
        _disabled = True
        logger.warning("Google Scholar returned 429 or CAPTCHA. Disabling for session.")
        return None

    if resp.status_code != 200:
        logger.debug("Scholarly failed: title=%s status=%d body=%s", title, resp.status_code, resp.text[:200])
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    results = soup.select("div.gs_r.gs_or.gs_scl")

    picked = _pick_relevant(results, title) if results else None

    if not picked:
        logger.debug("Scholarly: no relevant exact-match result, retrying broad: title=%s", title)
        # Try without quotes for a broader search
        params["q"] = title
        resp = get_session().get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Scholarly broad retry failed: title=%s status=%d", title, resp.status_code)
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        results = soup.select("div.gs_r.gs_or.gs_scl")
        picked = _pick_relevant(results, title) if results else None

    if not picked:
        logger.debug("Scholarly no relevant results after both passes: title=%s", title)
        return None

    return picked
