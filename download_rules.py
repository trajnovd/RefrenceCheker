"""Site-specific download rules.

Registry of per-domain overrides for HTTP downloads. Many sites reject the
default Chrome User-Agent or require specific headers before serving content.

Precedence (highest first):
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
  - Complex behavior (multi-step handshake, cookie dance) → v7 plugin dir
"""


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
        "rate_limit_per_sec": 10,  # SEC policy; reserved for future throttling
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
