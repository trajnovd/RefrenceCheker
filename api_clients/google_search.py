import threading
import time
import re
import logging
import requests
from config import GOOGLE_API_KEY, GOOGLE_CSE_ID

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 0.2

_ENABLED = bool(GOOGLE_API_KEY and GOOGLE_CSE_ID)
_disabled = False

SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def lookup_google_search(title, doi=None, timeout=10, max_retries=3):
    global _disabled
    if not _ENABLED or _disabled or not title:
        return None

    query = f'"{title}"'
    if doi:
        query += f" {doi}"

    logger.debug("GoogleSearch query: q=%s", query)

    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": 5,
    }

    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(SEARCH_URL, params=params, timeout=timeout)
            logger.debug("GoogleSearch response: status=%d title=%s", resp.status_code, title)
            if resp.status_code == 429:
                logger.debug("GoogleSearch rate-limited (429): title=%s attempt=%d", title, attempt)
                if attempt == max_retries - 1:
                    _disabled = True
                    logger.warning("Google Search persistently rate-limited (quota exceeded). Disabling for session.")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.debug("GoogleSearch failed: title=%s status=%d body=%s", title, resp.status_code, resp.text[:300])
                return None
            result = _parse_results(resp.json(), title)
            logger.debug("GoogleSearch result: title=%s found=%s url=%s pdf=%s",
                          title, result is not None,
                          result.get("url") if result else None,
                          result.get("pdf_url") if result else None)
            return result
        except Exception as e:
            logger.warning(f"Google Search lookup failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _parse_results(data, query_title):
    items = data.get("items", [])
    if not items:
        logger.debug("GoogleSearch no items returned: title=%s", query_title)
        return None

    norm_query = _normalize(query_title)
    pdf_url = None
    best_url = None
    abstract = None

    for item in items:
        link = item.get("link", "")
        item_title = item.get("title", "")
        snippet = item.get("snippet", "")

        # Check title relevance
        norm_title = _normalize(item_title)
        query_words = set(norm_query.split())
        title_words = set(norm_title.split())
        if not query_words:
            continue
        overlap = len(query_words & title_words) / len(query_words)
        if overlap < 0.5:
            continue

        # Grab first PDF link
        if not pdf_url and (link.endswith(".pdf") or "/pdf/" in link):
            pdf_url = link

        # Grab first relevant page URL
        if not best_url:
            best_url = link

        # Use snippet as abstract if it's substantial
        if not abstract and snippet and len(snippet) > 80:
            abstract = snippet.strip()

    if not best_url and not pdf_url:
        logger.debug("GoogleSearch no relevant matches: title=%s items_checked=%d", query_title, len(items))
        return None

    return {
        "url": best_url,
        "pdf_url": pdf_url,
        "abstract": abstract,
    }
