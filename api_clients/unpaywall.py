import threading
import time
import logging
import requests
from config import UNPAYWALL_EMAIL

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


def lookup_unpaywall(doi, timeout=10, max_retries=3):
    url = f"https://api.unpaywall.org/v2/{doi}"
    params = {"email": UNPAYWALL_EMAIL}
    logger.debug("Unpaywall query: doi=%s", doi)
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = requests.get(url, params=params, timeout=timeout)
            logger.debug("Unpaywall response: status=%d doi=%s", resp.status_code, doi)
            if resp.status_code == 429:
                logger.debug("Unpaywall rate-limited (429): doi=%s attempt=%d", doi, attempt)
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.debug("Unpaywall failed: doi=%s status=%d body=%s", doi, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            best = data.get("best_oa_location") or {}
            result = {
                "is_oa": data.get("is_oa", False),
                "pdf_url": best.get("url_for_pdf"),
            }
            logger.debug("Unpaywall result: doi=%s is_oa=%s pdf_url=%s", doi, result["is_oa"], result["pdf_url"])
            return result
        except Exception as e:
            logger.debug("Unpaywall error: doi=%s attempt=%d error=%s", doi, attempt, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
