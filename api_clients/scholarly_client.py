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
