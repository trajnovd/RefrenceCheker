"""Provenance tracking for downloaded reference artifacts (v6.1 A0.5).

Single responsibility: record which tier delivered each file on a result
dict. Used by A1 fallback tiers (`Wayback`, `OpenReview`, `PMC`, `CORE`,
`NBER`, ...) to stamp their identity onto the result so the UI can
display "Downloaded via OpenReview" instead of a silent success.

Data shape written onto result["files_origin"]:

    {
      "pdf":  {"tier": "openreview", "url": "https://...", "host": "openreview.net",
               "captured_at": "2026-04-19T03:10:00+00:00"},
      "page": {"tier": "direct",     ...},
    }

Keyed by file-type (`pdf`, `page`, `abstract`, `md`, `pasted`) so multiple
artifacts per reference can each have their own provenance.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse


def record_origin(result, filetype, tier, url):
    """Record that `filetype` for this result came from `tier` (with URL).

    Idempotent: always overwrites the entry for the given filetype, so
    re-downloads via a different tier correctly update the provenance.
    Never raises — bad inputs are silently ignored so tier implementations
    can't break the pipeline by omitting this call or passing None.
    """
    if not isinstance(result, dict) or not filetype or not tier:
        return
    try:
        host = (urlparse(url).hostname or "") if url else ""
    except Exception:
        host = ""
    origin = result.setdefault("files_origin", {})
    origin[filetype] = {
        "tier": tier,
        "url": url,
        "host": host.lower(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def get_origin(result, filetype):
    """Return the origin dict for `filetype`, or None if not recorded."""
    if not isinstance(result, dict):
        return None
    return (result.get("files_origin") or {}).get(filetype)


def clear_origin(result, filetype=None):
    """Drop provenance — for `filetype` only, or for everything when None.

    Called when a source is manually replaced (Set Link / Upload PDF /
    Paste Content) so stale tier tags don't linger.
    """
    if not isinstance(result, dict):
        return
    if filetype is None:
        result["files_origin"] = {}
        return
    origin = result.get("files_origin") or {}
    origin.pop(filetype, None)
    result["files_origin"] = origin
