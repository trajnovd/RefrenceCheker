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
