"""Site-specific download rules + domain-class registries.

Single source of truth for:
  * Per-host download rules (headers, rate limits) — `BUILTIN_RULES`
  * Domains whose PDFs are bot-blocked / WAF'd — `FRAGILE_PDF_DOMAINS`
  * Domains with no useful reference content (stores, book listings)
    — `NONCONTENT_DOMAINS`
  * Helpers: `is_fragile(url)`, `is_noncontent(url)`, `resolve_headers(...)`

Before v6.1 A0, the fragile + noncontent lists lived in TWO places
(lookup_engine.py and api_clients/google_search.py) with a "keep in sync"
comment. Consolidated here so new WAF discoveries are a single-file edit.

Precedence for headers (highest first):
  1. User overrides in settings.json → "download"."site_rules"
  2. BUILTIN_RULES below (ship-time defaults)
  3. Default headers from file_downloader._HEADERS

Matching: longest host-suffix match wins. So "efts.sec.gov" matches the
"sec.gov" rule automatically; a more specific "efts.sec.gov" rule would
override it.

Template expansion: any string value may contain `{contact_email}`, which
resolves to UNPAYWALL_EMAIL (env var or settings.json).

Extending:
  - Simple header tweaks → add to BUILTIN_RULES below, or edit settings.json
  - New fragile domain → add to FRAGILE_PDF_DOMAINS
  - Complex behavior (multi-step handshake, cookie dance) → v7 plugin dir
"""


# ============================================================
# Domain-class registries (single source of truth — v6.1 A0.3)
# ============================================================

# Publisher domains that bot-block anonymous PDF downloads (Cloudflare / JS
# challenges, cookie walls, paywalls with partial OA pretence via Unpaywall).
# When Unpaywall/OpenAlex/S2 returns one of these as pdf_url, callers fire
# Google Search for a non-fragile mirror (university .edu, author homepage,
# arXiv).
#
# This list is referenced by:
#   - lookup_engine._is_fragile_pdf (Step 4 trigger)
#   - api_clients.google_search._is_fragile_pdf_url (skip fragile results)
FRAGILE_PDF_DOMAINS = (
    "onlinelibrary.wiley.com",
    # papers.ssrn.com — REMOVED 2026-04-19. SSRN's bot-blocking is sporadic
    # rather than absolute, and many finance / econ refs (Wiley/JF DOIs) only
    # have an OA copy on SSRN. Treating it as fragile meant Google rescue and
    # the Google search parser silently skipped SSRN PDFs even when they would
    # have downloaded fine. If SSRN starts hard-blocking, restore here AND
    # surface a curl_cffi suggestion in compute_download_stats.
    "econstor.eu",
    "sciencedirect.com",
    "link.springer.com",
    "jstor.org",
    "tandfonline.com",
    "academic.oup.com",     # Oxford Academic — Cloudflare-protected like Wiley
)


# Commerce / store / catalogue domains whose pages contain no useful
# reference content. Results on these are skipped entirely — they waste
# the .md build and mislead claim-checking if saved as "the source."
NONCONTENT_DOMAINS = (
    "amazon.", "goodreads.com", "barnesandnoble.com", "books-a-million.com",
    "ebay.", "abebooks.", "alibris.", "walmart.com", "waterstones.com",
    "bookdepository.com", "target.com",
)


# Hosts that serve a JS challenge / interstitial (non-200 status, JS redirect
# in the body) on cold anonymous fetches. Plain `requests.get` can't follow
# them — we route the bib URL pre-fetch (and the PDF tier orchestrator) to
# Playwright, which executes the challenge.
#
# An EUR-Lex 202 response is the canonical example: body is a small JS shim
# that redirects to the rendered content after a captcha-equivalent.
JS_CHALLENGE_HOSTS = (
    "eur-lex.europa.eu",
    "europa.eu",          # broader EU domain — same WAF
    "elsevier.com",
    "sciencedirect.com",  # Elsevier subdomain
)


def is_fragile(url):
    """True if the URL is on a bot-blocked publisher domain (host suffix match)."""
    if not url:
        return False
    u = url.lower()
    return any(d in u for d in FRAGILE_PDF_DOMAINS)


def is_js_challenge(url):
    """True if the URL is on a known JS-challenge host (host suffix match).

    Used by file_downloader._download_page / _download_pdf to skip the doomed
    direct attempt and go straight to Playwright."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in JS_CHALLENGE_HOSTS)


def is_noncontent(url):
    """True if the URL is a commerce / store / catalogue domain with no
    reference content. Empty / None URLs are treated as non-content (nothing
    to download)."""
    if not url:
        return True
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(d in host for d in NONCONTENT_DOMAINS)


# ============================================================
# Per-host rate limiter (v6.1 A2)
# ============================================================
# Token bucket using the `rate_limit_per_sec` field declared on rules.
# Rules without that field are no-op (no throttling). Thread-safe via a
# per-host lock held only during bucket updates.

import threading
import time
from urllib.parse import urlparse

_RATE_BUCKETS = {}         # host -> {"tokens": float, "last": float, "rate": float}
_RATE_LOCK = threading.Lock()


def _rate_for_host(host):
    """Return rate_limit_per_sec from the longest-suffix-matching built-in rule,
    or None when the host has no rate limit configured."""
    if not host:
        return None
    for _d, rule in _iter_rules(host, BUILTIN_RULES):
        r = rule.get("rate_limit_per_sec")
        if r is not None:
            return float(r)
    return None


def acquire_for(url):
    """Block until the per-host token bucket allows one more request.

    No-op for hosts without a `rate_limit_per_sec` rule. Called by fetchers
    before hitting the network. Uses monotonic time so a system clock change
    doesn't pause the whole process.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return
    rate = _rate_for_host(host)
    if not rate or rate <= 0:
        return
    # Token-bucket: capacity == rate (1 second of burst), refill = rate tokens/s
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            bucket = _RATE_BUCKETS.get(host)
            if bucket is None:
                bucket = {"tokens": rate, "last": now, "rate": rate}
                _RATE_BUCKETS[host] = bucket
            # Refill
            elapsed = now - bucket["last"]
            bucket["tokens"] = min(bucket["rate"], bucket["tokens"] + elapsed * bucket["rate"])
            bucket["last"] = now
            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return
            needed = (1.0 - bucket["tokens"]) / bucket["rate"]
        time.sleep(min(needed, 0.5))


def _reset_rate_limits_for_tests():
    """Clear the bucket registry. Test-only."""
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()


# ============================================================
# Host → best-tier cache (v6.1 A2)
# ============================================================
# Once a tier succeeds for a host, remember that for 1 hour so subsequent
# refs from the same bot-blocked host skip doomed Tier 0 attempts.

_HOST_TIER = {}            # host -> (tier_name, learned_at_monotonic)
_HOST_TIER_TTL_S = 3600    # 1 hour
_HOST_TIER_LOCK = threading.Lock()


def remember_winning_tier(url, tier):
    """Record that `tier` successfully delivered content for this host."""
    if not url or not tier:
        return
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return
    if not host:
        return
    with _HOST_TIER_LOCK:
        _HOST_TIER[host] = (tier, time.monotonic())


def preferred_tier_for(url):
    """Return the tier that last succeeded for this host (if within TTL), else None."""
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    with _HOST_TIER_LOCK:
        entry = _HOST_TIER.get(host)
        if entry is None:
            return None
        tier, ts = entry
        if time.monotonic() - ts > _HOST_TIER_TTL_S:
            _HOST_TIER.pop(host, None)
            return None
        return tier


def _reset_host_tier_cache_for_tests():
    with _HOST_TIER_LOCK:
        _HOST_TIER.clear()


# ============================================================
# Ship-time built-in rules
# ============================================================

BUILTIN_RULES = {
    # SEC requires app-name + contact email per their webmaster FAQ:
    # https://www.sec.gov/os/webmaster-faq
    # A generic Chrome UA is 403'd by their WAF.
    "sec.gov": {
        "headers": {
            "User-Agent": "RefChecker {contact_email}",
            "Accept-Encoding": "gzip, deflate",
        },
        "notes": "SEC fair-use: User-Agent must include app name + contact email",
        "rate_limit_per_sec": 10,  # SEC policy; enforced by acquire_for()
    },

    # v6.1 §3.18 — WAF'd hosts: skip the doomed Tier 0 attempt and go straight
    # to curl_cffi. Orchestrator falls back to normal walk order if the forced
    # tier is disabled in settings (curl_cffi not installed / not enabled).
    "econstor.eu": {
        "force_tier": "curl_cffi",
        "notes": "bot-check interstitial; needs TLS impersonation",
    },
    "papers.ssrn.com": {
        "force_tier": "curl_cffi",
        "notes": "WAF + session cookies; direct fetch always 403s",
    },
    "researchgate.net": {
        "force_tier": "curl_cffi",
        "notes": "bot-blocked; needs TLS impersonation + session",
    },

    # JS-challenge hosts: response status is non-200 (often 202) with a JS
    # redirect body that plain requests can't follow. Playwright executes
    # the JS and returns the rendered page.
    "eur-lex.europa.eu": {
        "force_tier": "playwright",
        "notes": "EUR-Lex serves HTTP 202 + JS interstitial on cold fetches",
    },
    "elsevier.com": {
        "force_tier": "playwright",
        "notes": "Elsevier portal — JS-rendered SPA",
    },
}


def _iter_rules(host, rules):
    """Yield (matched_domain, rule) pairs in order of specificity (longest suffix first)."""
    if not host:
        return
    host = host.lower()
    matches = [d for d in rules if host == d or host.endswith("." + d)]
    matches.sort(key=len, reverse=True)  # most specific first
    for d in matches:
        yield d, rules[d]


def resolve_headers(url, default_headers, user_rules=None, contact_email=""):
    """Resolve the final header dict for a URL.

    Merges (low → high precedence): default_headers → builtin rule → user rule.
    Template strings `{contact_email}` are expanded.

    Args:
        url: the target URL
        default_headers: fallback dict (typically the app's _HEADERS)
        user_rules: optional user-provided rules (settings.json → download.site_rules)
        contact_email: value used to expand `{contact_email}` template

    Returns: a header dict ready to pass to requests.get(headers=...)
    """
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return dict(default_headers)

    merged = dict(default_headers)

    # Built-in rules first (less specific)
    for _domain, rule in _iter_rules(host, BUILTIN_RULES):
        for k, v in (rule.get("headers") or {}).items():
            merged[k] = _expand(v, contact_email)
        break  # longest match only

    # User overrides
    for _domain, rule in _iter_rules(host, user_rules or {}):
        for k, v in (rule.get("headers") or {}).items():
            merged[k] = _expand(v, contact_email)
        break

    # Host header — some servers need it explicit when going through proxies
    if host:
        merged.setdefault("Host", host)

    return merged


def _expand(value, contact_email):
    """Simple `{contact_email}` template expansion."""
    if not isinstance(value, str):
        return value
    return value.replace("{contact_email}", contact_email or "contact@example.com")


def rules_summary():
    """Return a list of dicts describing all active rules — for the startup banner."""
    return [
        {"domain": d, "notes": r.get("notes", ""), "source": "builtin"}
        for d, r in BUILTIN_RULES.items()
    ]
