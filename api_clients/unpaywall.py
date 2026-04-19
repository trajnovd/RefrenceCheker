import threading
import time
import logging
import requests
from http_client import get_session
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
            resp = get_session().get(url, params=params, timeout=timeout)
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
            # v6.1 §3.1: surface ALL oa_locations, not just the best. Later tiers
            # walk `pdf_url_fallbacks` when the primary 403s on a WAF'd host.
            all_pdf_urls = []
            for loc in data.get("oa_locations") or []:
                u = loc.get("url_for_pdf")
                if u and u not in all_pdf_urls:
                    all_pdf_urls.append(u)
            result = {
                "is_oa": data.get("is_oa", False),
                "pdf_url": best.get("url_for_pdf"),
                # Ordered list; may start with the same URL as pdf_url —
                # the orchestrator dedupes across tiers (A2).
                "pdf_url_fallbacks": all_pdf_urls,
            }
            logger.debug("Unpaywall result: doi=%s is_oa=%s pdf_url=%s alts=%d",
                         doi, result["is_oa"], result["pdf_url"], len(all_pdf_urls))
            return result
        except Exception as e:
            logger.debug("Unpaywall error: doi=%s attempt=%d error=%s", doi, attempt, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None
