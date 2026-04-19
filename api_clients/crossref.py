import threading
import time
import logging
import requests
from http_client import get_session

logger = logging.getLogger(__name__)

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
    logger.debug("CrossRef query: doi=%s", doi)
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = get_session().get(url, timeout=timeout)
            logger.debug("CrossRef response: status=%d doi=%s", resp.status_code, doi)
            if resp.status_code == 429:
                logger.debug("CrossRef rate-limited (429): doi=%s attempt=%d", doi, attempt)
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.debug("CrossRef failed: doi=%s status=%d body=%s", doi, resp.status_code, resp.text[:200])
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
            result = {
                "title": titles[0] if titles else None,
                "authors": authors,
                "journal": container[0] if container else None,
                "year": year,
                "url": msg.get("URL"),
            }
            logger.debug("CrossRef result: doi=%s title=%s", doi, result.get("title"))
            return result
        except Exception as e:
            logger.debug("CrossRef error: doi=%s attempt=%d error=%s", doi, attempt, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
