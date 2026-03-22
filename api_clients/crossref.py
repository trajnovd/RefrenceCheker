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
