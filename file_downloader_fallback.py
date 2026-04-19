"""Tiered download orchestrator — v6.1 A0.2 + A1.

Walks a sequence of Fetcher tiers in order; the first one that delivers a
valid PDF short-circuits the rest. Records provenance (which tier won) on
the result dict so the UI can show "Downloaded via <tier>".

Shipped tiers:
    direct           — plain requests + per-site UA rules
    oa_fallbacks     — walk pdf_url_fallbacks from Unpaywall/OpenAlex
    doi_negotiation  — Accept: application/pdf on doi.org (works for ~20% of DOIs)
    wayback          — Internet Archive CDX lookup + archived PDF fetch
    openreview       — NeurIPS / ICML / ICLR full-text by title

Each tier is a pure function with the signature:
    fetch(ctx: FetchContext) -> FetchResult

FetchContext carries everything a tier might want (url, result dict, ref dict,
title, doi, bib_key, target path). FetchResult says whether it succeeded and
records the final URL + status for the download_log.
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse, quote

import requests

from http_client import get_session
from provenance import record_origin


logger = logging.getLogger(__name__)


MAX_PDF_SIZE = 50 * 1024 * 1024   # 50MB — aligned with file_downloader
_PDF_CHUNK_SIZE = 65536


# ============================================================
# Fetch primitives
# ============================================================

@dataclass
class FetchContext:
    """Inputs a tier may consult during its attempt."""
    url: Optional[str]             # primary URL (may be None for title-only tiers)
    target_path: str               # where to write the PDF on success
    bib_key: str
    result: dict                   # the full lookup result (pdf_url_fallbacks, doi, ...)
    ref: Optional[dict] = None     # original bib entry
    title: Optional[str] = None
    doi: Optional[str] = None
    timeout_s: int = 30
    headers_fn: Optional[Callable[[str], dict]] = None  # called as headers_fn(url)


@dataclass
class FetchResult:
    ok: bool
    final_url: Optional[str] = None    # URL actually downloaded from (after redirects / CDX)
    http_status: Optional[int] = None
    kind: Optional[str] = None         # "http_4xx", "http_5xx", "network", "validation", "no_match"
    elapsed_ms: int = 0
    detail: str = ""


# ============================================================
# Shared helpers — validation + streaming
# ============================================================

def validate_pdf_head(head_bytes: bytes) -> bool:
    """True iff the first bytes start with the %PDF magic header."""
    return head_bytes[:5].startswith(b"%PDF")


def _stream_pdf_to_path(resp, target_path, max_size=MAX_PDF_SIZE):
    """Stream a PDF response to disk with magic-byte early-reject.
    Writes to <target>.partial then atomically renames on success.

    Returns (ok: bool, kind: str|None, detail: str). kind is set on failure.
    """
    tmp = target_path + ".partial"
    total = 0
    try:
        with open(tmp, "wb") as f:
            first = b""
            # Pull the first chunk and check magic bytes
            for chunk in resp.iter_content(chunk_size=_PDF_CHUNK_SIZE):
                if not chunk:
                    continue
                first = chunk
                break
            if not validate_pdf_head(first):
                try: os.remove(tmp)
                except OSError: pass
                return False, "validation", f"not_a_pdf (first_bytes={first[:20]!r})"
            f.write(first)
            total = len(first)
            if total > max_size:
                try: os.remove(tmp)
                except OSError: pass
                return False, "validation", "exceeds_max_size"
            # Stream the rest
            for chunk in resp.iter_content(chunk_size=_PDF_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_size:
                    try: os.remove(tmp)
                    except OSError: pass
                    return False, "validation", "exceeds_max_size"
                f.write(chunk)
        os.replace(tmp, target_path)
        return True, None, ""
    except Exception as e:
        try: os.remove(tmp)
        except OSError: pass
        return False, "network", str(e)


def _classify_http_failure(status):
    if 400 <= status < 500: return "http_4xx"
    if 500 <= status < 600: return "http_5xx"
    return "http_other"


def _fetch_pdf(url, target_path, *, headers=None, timeout=30):
    """Core GET-and-stream used by most tiers. Returns FetchResult.

    Respects per-host rate limits via `download_rules.acquire_for` (v6.1 A2).
    """
    from download_rules import acquire_for
    t0 = time.monotonic()
    acquire_for(url)  # blocks per token-bucket; no-op for unthrottled hosts
    try:
        resp = get_session().get(url, headers=headers or {}, timeout=timeout,
                                  stream=True, allow_redirects=True)
    except requests.RequestException as e:
        return FetchResult(ok=False, kind="network", detail=str(e),
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    if resp.status_code != 200:
        return FetchResult(ok=False, http_status=resp.status_code,
                           kind=_classify_http_failure(resp.status_code),
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    ok, kind, detail = _stream_pdf_to_path(resp, target_path)
    return FetchResult(ok=ok, final_url=str(resp.url),
                       http_status=resp.status_code if ok else resp.status_code,
                       kind=kind, detail=detail,
                       elapsed_ms=int((time.monotonic() - t0) * 1000))


# ============================================================
# Tier: direct (baseline — equivalent to Tier 0 in v6.1)
# ============================================================

def _tier_direct(ctx: FetchContext) -> FetchResult:
    if not ctx.url:
        return FetchResult(ok=False, kind="no_match", detail="no url")
    headers = ctx.headers_fn(ctx.url) if ctx.headers_fn else {}
    return _fetch_pdf(ctx.url, ctx.target_path, headers=headers, timeout=ctx.timeout_s)


# ============================================================
# Tier: oa_fallbacks (§3.1 / §3.2 — Unpaywall/OpenAlex alt URLs)
# ============================================================

def _tier_oa_fallbacks(ctx: FetchContext) -> FetchResult:
    alts = list(ctx.result.get("pdf_url_fallbacks") or []) if ctx.result else []
    primary = ctx.url  # we've already tried this via direct tier
    # Dedup by normalized host+path — Unpaywall and OpenAlex commonly return
    # the same mirror URL with different query strings (tracking params).
    tried_keys = {_dedup_key(primary)}
    tried_count = 0
    t0 = time.monotonic()
    for alt in alts:
        key = _dedup_key(alt)
        if not key or key in tried_keys:
            continue
        tried_keys.add(key)
        tried_count += 1
        headers = ctx.headers_fn(alt) if ctx.headers_fn else {}
        r = _fetch_pdf(alt, ctx.target_path, headers=headers, timeout=ctx.timeout_s)
        if r.ok:
            return r
    return FetchResult(ok=False, kind="no_match",
                       detail=f"tried {tried_count} alt URLs",
                       elapsed_ms=int((time.monotonic() - t0) * 1000))


def _dedup_key(url):
    """Normalize a URL to (host, path) for dedup across tiers. Ignores scheme
    and query/tracking params. Returns None for empty input."""
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    path = p.path or ""
    return f"{host}{path}" if host else url


# ============================================================
# Tier: doi_negotiation (§3.9)
# ============================================================

def _tier_doi_negotiation(ctx: FetchContext) -> FetchResult:
    """Ask doi.org for the PDF via content negotiation. Works for ~20% of
    DOIs whose publisher supports `Accept: application/pdf`."""
    doi = ctx.doi or (ctx.result or {}).get("doi") or (ctx.ref or {}).get("doi")
    if not doi:
        return FetchResult(ok=False, kind="no_match", detail="no doi")
    url = f"https://doi.org/{doi}"
    headers = {"Accept": "application/pdf"}
    # Blend per-site rules in (User-Agent is important for some publishers)
    if ctx.headers_fn:
        headers = {**ctx.headers_fn(url), **headers}
    return _fetch_pdf(url, ctx.target_path, headers=headers, timeout=ctx.timeout_s)


# ============================================================
# Tier: wayback (§3.10 / v6 Tier 1B)
# ============================================================

_WAYBACK_CDX = "https://archive.org/wayback/available"


def _tier_wayback(ctx: FetchContext) -> FetchResult:
    """Look up the closest archived snapshot for the primary URL.
    Wayback never bot-blocks its own cache."""
    target = ctx.url or (ctx.result or {}).get("url")
    if not target:
        return FetchResult(ok=False, kind="no_match", detail="no url")
    t0 = time.monotonic()
    try:
        r = get_session().get(_WAYBACK_CDX, params={"url": target},
                              timeout=min(ctx.timeout_s, 15))
    except requests.RequestException as e:
        return FetchResult(ok=False, kind="network", detail=f"cdx: {e}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    if r.status_code != 200:
        return FetchResult(ok=False, http_status=r.status_code,
                           kind=_classify_http_failure(r.status_code),
                           detail=f"cdx status {r.status_code}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        snaps = (r.json().get("archived_snapshots") or {}).get("closest") or {}
    except ValueError:
        return FetchResult(ok=False, kind="network", detail="cdx json parse",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    snap_url = snaps.get("url")
    if not snap_url:
        return FetchResult(ok=False, kind="no_match", detail="no snapshot",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    # Fetch the raw (id_) variant so Wayback serves the original bytes without
    # their toolbar HTML wrapper. Format: /web/<timestamp>id_/<original>
    raw = re.sub(r"/web/(\d+)/", r"/web/\1id_/", snap_url, count=1)
    result = _fetch_pdf(raw, ctx.target_path, timeout=ctx.timeout_s)
    # If the id_ variant wasn't a PDF, the toolbar-wrapped one certainly isn't either.
    if not result.ok:
        result.detail = f"snapshot at {snaps.get('timestamp')}: {result.detail}"
    else:
        result.final_url = snap_url  # show the user-facing snapshot URL
    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return result


# ============================================================
# Tier: openreview (§3.5)
# ============================================================

_OPENREVIEW_SEARCH = "https://api2.openreview.net/notes/search"


def _tier_openreview(ctx: FetchContext) -> FetchResult:
    """Find a matching paper on OpenReview by title, download its PDF.
    OpenReview serves PDFs without bot-blocks."""
    title = ctx.title or (ctx.result or {}).get("title") or (ctx.ref or {}).get("title")
    if not title:
        return FetchResult(ok=False, kind="no_match", detail="no title")
    t0 = time.monotonic()
    try:
        r = get_session().get(_OPENREVIEW_SEARCH,
                              params={"term": title, "type": "terms", "limit": 5},
                              timeout=min(ctx.timeout_s, 10))
    except requests.RequestException as e:
        return FetchResult(ok=False, kind="network", detail=f"search: {e}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    if r.status_code != 200:
        return FetchResult(ok=False, http_status=r.status_code,
                           kind=_classify_http_failure(r.status_code),
                           detail="search failed",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        notes = (r.json().get("notes") or [])
    except ValueError:
        return FetchResult(ok=False, kind="network", detail="json parse",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    # Pick first result whose title overlaps ≥60% with the query
    picked = _pick_openreview_match(title, notes)
    if not picked:
        return FetchResult(ok=False, kind="no_match",
                           detail=f"no match among {len(notes)} hits",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    # OpenReview PDF URL: https://openreview.net/pdf?id=<id>
    pdf_url = f"https://openreview.net/pdf?id={quote(picked)}"
    res = _fetch_pdf(pdf_url, ctx.target_path, timeout=ctx.timeout_s)
    res.final_url = pdf_url
    res.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return res


def _pick_openreview_match(query, notes):
    """Return the note id whose title has ≥60% word overlap with query, or None."""
    qwords = set(re.sub(r"[^\w\s]", " ", query.lower()).split())
    if not qwords:
        return None
    for note in notes:
        content = note.get("content") or {}
        t = content.get("title")
        # OpenReview v2 API sometimes wraps values: content.title.value
        if isinstance(t, dict):
            t = t.get("value")
        if not t:
            continue
        twords = set(re.sub(r"[^\w\s]", " ", str(t).lower()).split())
        overlap = len(qwords & twords) / len(qwords)
        if overlap >= 0.6:
            return note.get("id") or note.get("forum")
    return None


# ============================================================
# Tier: curl_cffi (§3.18 — Phase B, OPT-IN)
# ============================================================
# Defeats WAFs (Cloudflare/Akamai/AWS) by impersonating a real Chrome's TLS
# ClientHello. Recovers SSRN, ResearchGate, econstor, OUP/Wiley/ScienceDirect
# cold fetches. Requires `pip install curl_cffi` (~200 MB). Lazily imported
# so users who don't enable it don't pay the dependency.

def _curl_cffi_enabled():
    try:
        from config import get_settings
        s = get_settings().get("download") or {}
        return bool(s.get("use_curl_cffi_fallback"))
    except Exception:
        return False


def _tier_curl_cffi(ctx: FetchContext) -> FetchResult:
    if not _curl_cffi_enabled():
        return FetchResult(ok=False, kind="disabled", detail="curl_cffi disabled in settings")
    if not ctx.url:
        return FetchResult(ok=False, kind="no_match", detail="no url")
    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.warning("curl_cffi not installed; skipping Tier 2")
        return FetchResult(ok=False, kind="not_installed",
                           detail="curl_cffi package not installed")
    from config import get_settings
    s = (get_settings().get("download") or {})
    impersonate = s.get("curl_cffi_impersonate", "chrome120")
    timeout = int(s.get("curl_cffi_timeout_s", ctx.timeout_s))

    t0 = time.monotonic()
    try:
        # Session so SSRN/ResearchGate cookie handshakes work across the redirect chain.
        with cf_requests.Session() as sess:
            r = sess.get(ctx.url, impersonate=impersonate, timeout=timeout,
                          allow_redirects=True)
    except Exception as e:
        return FetchResult(ok=False, kind="network", detail=f"curl_cffi: {e}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    if r.status_code != 200:
        return FetchResult(ok=False, http_status=r.status_code,
                           kind=_classify_http_failure(r.status_code),
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    content = r.content
    if not validate_pdf_head(content):
        return FetchResult(ok=False, http_status=200, kind="validation",
                           detail="not_a_pdf",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    if len(content) > MAX_PDF_SIZE:
        return FetchResult(ok=False, http_status=200, kind="validation",
                           detail="exceeds_max_size",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        tmp = ctx.target_path + ".partial"
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, ctx.target_path)
    except OSError as e:
        return FetchResult(ok=False, kind="network", detail=f"write: {e}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    return FetchResult(ok=True, final_url=str(r.url), http_status=200,
                       elapsed_ms=int((time.monotonic() - t0) * 1000))


# ============================================================
# Tier: Playwright (§3.19 — Phase C, OPT-IN)
# ============================================================
# Heaviest fallback: real Chromium with JS execution. Handles EUR-Lex's
# JS interstitial, SPA publisher portals, reCAPTCHA-gated pages. Requires
# `pip install playwright` AND `playwright install chromium` (~400 MB).

_browser_pool = None
_browser_pool_lock = threading.Lock() if False else None  # see below — we use the singleton from browser_pool


def _playwright_enabled():
    try:
        from config import get_settings
        s = get_settings().get("download") or {}
        return bool(s.get("use_playwright_fallback"))
    except Exception:
        return False


def _tier_playwright(ctx: FetchContext) -> FetchResult:
    if not _playwright_enabled():
        return FetchResult(ok=False, kind="disabled", detail="playwright disabled in settings")
    if not ctx.url:
        return FetchResult(ok=False, kind="no_match", detail="no url")
    try:
        from browser_pool import BrowserPool
    except ImportError:
        return FetchResult(ok=False, kind="not_installed",
                           detail="playwright not installed")
    from config import get_settings
    s = get_settings().get("download") or {}
    pool_size = int(s.get("playwright_pool_size", 1))
    timeout = int(s.get("playwright_timeout_s", 30))
    html_to_pdf = bool(s.get("playwright_html_to_pdf", True))

    pool = BrowserPool.instance(size=pool_size)
    if pool is None:
        return FetchResult(ok=False, kind="not_installed",
                           detail="playwright runtime not available")
    t0 = time.monotonic()
    browser = pool.acquire(timeout=60)
    if browser is None:
        return FetchResult(ok=False, kind="network", detail="pool acquire timeout",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    try:
        import threading as _t
        ctx_pw = browser.new_context(accept_downloads=True)
        page = ctx_pw.new_page()
        try:
            try:
                with page.expect_download(timeout=timeout * 1000) as dl_info:
                    try:
                        page.goto(ctx.url, wait_until="commit", timeout=timeout * 1000)
                    except Exception:
                        pass
                dl_info.value.save_as(ctx.target_path)
                # Validate what landed
                with open(ctx.target_path, "rb") as f:
                    head = f.read(8)
                if not validate_pdf_head(head):
                    raise RuntimeError("downloaded content is not a PDF")
                return FetchResult(ok=True, final_url=ctx.url, http_status=200,
                                    elapsed_ms=int((time.monotonic() - t0) * 1000))
            except Exception:
                # No download fired — the page rendered HTML inline.
                # Convert to PDF as a last resort so downstream extraction still works.
                if not html_to_pdf:
                    raise
                page.goto(ctx.url, wait_until="networkidle", timeout=timeout * 1000)
                pdf_bytes = page.pdf(format="A4", print_background=True)
                if not validate_pdf_head(pdf_bytes):
                    raise RuntimeError("page.pdf() did not produce a PDF")
                tmp = ctx.target_path + ".partial"
                with open(tmp, "wb") as f:
                    f.write(pdf_bytes)
                os.replace(tmp, ctx.target_path)
                return FetchResult(ok=True, final_url=ctx.url, http_status=200,
                                    elapsed_ms=int((time.monotonic() - t0) * 1000))
        finally:
            try: ctx_pw.close()
            except Exception: pass
    except Exception as e:
        return FetchResult(ok=False, kind="network", detail=f"playwright: {e}",
                           elapsed_ms=int((time.monotonic() - t0) * 1000))
    finally:
        try: pool.release(browser)
        except Exception: pass


# ============================================================
# Orchestrator
# ============================================================

# Default tier plan for a PDF-seeking download.
# Tier functions are resolved by name at call time (via the module global) so
# tests can patch individual tiers with patch.object(fdf, "_tier_X", ...).
DEFAULT_PDF_TIERS = [
    ("direct",          "_tier_direct"),
    ("oa_fallbacks",    "_tier_oa_fallbacks"),
    ("doi_negotiation", "_tier_doi_negotiation"),
    ("openreview",      "_tier_openreview"),   # before wayback — prefer current content
    ("wayback",         "_tier_wayback"),
    ("curl_cffi",       "_tier_curl_cffi"),     # Phase B — opt-in, no-ops if disabled
    ("playwright",      "_tier_playwright"),    # Phase C — opt-in, no-ops if disabled
]


def _resolve_force_tier(url):
    """Return the tier a site rule forces (§3.18 `force_tier` field), or None."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        from download_rules import BUILTIN_RULES, _iter_rules
        host = (urlparse(url).hostname or "").lower()
        for _d, rule in _iter_rules(host, BUILTIN_RULES):
            if rule.get("force_tier"):
                return rule["force_tier"]
    except Exception:
        return None
    return None


def download_with_fallback(url, target_path, *, bib_key, result=None, ref=None,
                            title=None, doi=None, is_bib_url=False,
                            timeout_s=30, headers_fn=None, on_attempt=None):
    """Try each tier in order. First success wins. Returns a dict:

        {"ok": True,  "tier": "openreview", "final_url": "...", "log": [...]}
        {"ok": False, "tier": None,          "final_url": None,  "log": [...]}

    - `is_bib_url=True` restricts to the direct tier only (bib URLs must be
       flagged broken via `bib_url_unreachable` when they fail — see §11.14).
    - `on_attempt(tier, fetch_result)` callback fires per tier for SSE / telemetry.
    """
    ctx = FetchContext(
        url=url, target_path=target_path, bib_key=bib_key,
        result=result or {}, ref=ref,
        title=title or (result or {}).get("title") or (ref or {}).get("title"),
        doi=doi or (result or {}).get("doi") or (ref or {}).get("doi"),
        timeout_s=timeout_s, headers_fn=headers_fn,
    )

    # §11.14 — bib-URL path is direct-only, no fallbacks.
    tier_plan = list([DEFAULT_PDF_TIERS[0]] if is_bib_url else DEFAULT_PDF_TIERS)

    if not is_bib_url and url:
        # §3.18 force_tier — site rule declares "always use curl_cffi" for this
        # host (econstor/ssrn). Jump straight to it; fall back to the rest of
        # the plan if the forced tier is disabled in settings.
        forced = _resolve_force_tier(url)
        if forced:
            forced_entry = next((t for t in tier_plan if t[0] == forced), None)
            if forced_entry:
                tier_plan = [forced_entry] + [t for t in tier_plan if t[0] != forced]

        # §11.8 — host best-tier cache: if we've recently seen a non-`direct` tier
        # win for this host, promote it to the front so we skip the doomed Tier 0
        # attempt on subsequent refs from the same bot-blocked host.
        from download_rules import preferred_tier_for
        preferred = preferred_tier_for(url)
        if preferred and preferred != "direct":
            preferred_entry = next((t for t in tier_plan if t[0] == preferred), None)
            if preferred_entry:
                tier_plan = [preferred_entry] + [t for t in tier_plan if t[0] != preferred]

    log = []
    for tier_name, tier_attr in tier_plan:
        # Resolve by name at call time so tests can patch.object(fdf, "_tier_X", ...)
        tier_fn = globals().get(tier_attr)
        if tier_fn is None:
            continue
        try:
            fr = tier_fn(ctx)
        except Exception as e:
            logger.debug("[%s] tier %s raised: %s", bib_key, tier_name, e)
            fr = FetchResult(ok=False, kind="network", detail=str(e))
        entry = {
            "tier": tier_name,
            "ok": fr.ok,
            "final_url": fr.final_url,
            "http_status": fr.http_status,
            "kind": fr.kind,
            "elapsed_ms": fr.elapsed_ms,
        }
        log.append(entry)
        if on_attempt:
            try: on_attempt(tier_name, fr)
            except Exception: pass
        if fr.ok:
            # Stamp provenance on the result so the UI can display tier origin.
            if result is not None:
                record_origin(result, "pdf", tier_name, fr.final_url or url)
            # §11.8 — learn: remember this tier worked for this host.
            if not is_bib_url and url and tier_name != "direct":
                from download_rules import remember_winning_tier
                remember_winning_tier(url, tier_name)
            return {"ok": True, "tier": tier_name,
                    "final_url": fr.final_url or url, "log": log}

    return {"ok": False, "tier": None, "final_url": None, "log": log}
