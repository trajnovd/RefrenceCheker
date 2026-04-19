"""Process-wide requests.Session with connection pooling + retry.

Before this module, every API client and download path called
requests.get() directly — each paying a fresh TLS handshake (~100-300 ms)
and TCP setup (~20-50 ms). On a 100-reference project with 15+ network
hops per reference, that's 200-600 s of wasted latency.

Usage:
    from http_client import get_session
    resp = get_session().get(url, headers=..., timeout=...)

The session pools connections per host (max 32), reuses TLS, and retries
5xx / gateway errors transparently via urllib3's Retry. Per-call overrides
(timeout, headers) are unchanged.
"""

import threading
import requests
from requests.adapters import HTTPAdapter

try:
    # urllib3 v2 path
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover — very old urllib3
    from urllib3.util import Retry


_session = None
_lock = threading.Lock()


def _build_session():
    """Construct the process-wide Session. Separate from get_session() so
    tests can reset state via _reset_for_tests()."""
    s = requests.Session()
    # Retry strategy:
    # - 2 retries on 502/503/504 + connection errors (handled by urllib3 automatically).
    # - backoff_factor=0.5 → sleeps 0.5s, 1s before the final attempt.
    # - allowed_methods: only idempotent verbs; downloads are all GET so we're safe.
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=32,
        max_retries=retry,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def get_session():
    """Return the singleton Session. Thread-safe double-checked init."""
    global _session
    if _session is not None:
        return _session
    with _lock:
        if _session is None:
            _session = _build_session()
    return _session


def _reset_for_tests():
    """Force-recreate the session. Only for test setup — not a public API."""
    global _session
    with _lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass
        _session = None


def close_session():
    """Release all pooled connections. Call at app shutdown."""
    global _session
    with _lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass
            _session = None
