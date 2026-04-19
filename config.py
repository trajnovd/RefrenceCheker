import os
import json
import sys
import copy

_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")

# ============================================================
# Default settings — written to settings.json on first run.
# Env vars (if set) override values at load time.
# API keys are NEVER stored here; they come only from env vars.
# ============================================================
_DEFAULT_SETTINGS = {
    # --- General ---
    "flask_port": 5000,
    "projects_dir": "projects",
    "max_upload_size_mb": 50,
    "session_ttl": 1800,
    "max_workers": 1,
    "unpaywall_email": "dimitar.trajanov@finki.ukim.mk",
    "scholarly_enabled": True,

    # --- PDF to Markdown conversion ---
    # Two converters + a page-count threshold: short docs get the high-quality
    # (layout-aware) pass; long docs fall back to the fast text extractor.
    # Values: "pymupdf_text" (raw text, fastest, handles huge PDFs),
    #         "pymupdf4llm"  (layout-aware markdown, slow, OOM on big PDFs),
    #         "docling"      (ML-based, slowest, highest structural quality).
    "pdf_converter_fast":         "pymupdf_text",
    "pdf_converter_high_quality": "pymupdf4llm",
    "pdf_quality_page_limit":     30,
    # Backward-compat: "pdf_converter" (single value) is ignored if the pair above is set.

    # --- Per-site download rules ---
    # User overrides that deep-merge over download_rules.BUILTIN_RULES.
    # See download_rules.py for the full schema. Keys are host suffixes.
    # Example:
    #   "download": {
    #     "site_rules": {
    #       "imf.org": {"headers": {"Accept": "application/pdf"}}
    #     }
    #   }
    "download": {
        "site_rules": {},
        # v6.1 Phase B — curl_cffi (opt-in; requires `pip install curl_cffi`)
        "use_curl_cffi_fallback": False,
        "curl_cffi_impersonate":  "chrome120",
        "curl_cffi_timeout_s":    30,
        # v6.1 Phase C — Playwright (opt-in; requires `pip install playwright`
        # AND `playwright install chromium`)
        "use_playwright_fallback": False,
        "playwright_pool_size":    1,
        "playwright_timeout_s":    30,
        "playwright_html_to_pdf":  True,
    },

    # --- LLM claim checking ---
    "claim_check": {
        "enabled": True,
        "openai_model": "gpt-5-mini",
        "max_ref_chars": 100000,
        "max_paragraph_chars": 4000,
        "max_sentence_chars": 1500,
        "max_batch_usd": 5.00,
        "request_timeout_s": 60,
        "max_retries": 3,
        "max_parallel": 4,   # concurrent OpenAI calls during batch check
    },

    # --- Reference identity match ---
    # Asks an LLM whether the downloaded text actually matches the bib's title +
    # authors (defends against the wrong-paper failure mode where Google/S2/arXiv
    # title-matching pulled an unrelated document).
    "reference_match": {
        "enabled": True,
        "openai_model": "gpt-5-mini",
        "max_chars": 6000,                  # excerpt size sent to the LLM (~2 pages)
        "auto_check_on_download": True,     # check after every successful download
        "max_parallel": 4,                  # concurrent OpenAI calls during batch
        "request_timeout_s": 30,
        "max_retries": 2,
    },
}


def _deep_merge(base, override):
    """Recursively merge override into base. Returns a new dict."""
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _load_settings():
    if not os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_SETTINGS, f, indent=2)
        except OSError as e:
            print(f"[config] Could not create settings.json: {e}", file=sys.stderr)
        return copy.deepcopy(_DEFAULT_SETTINGS)
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] settings.json unreadable ({e}); using defaults", file=sys.stderr)
        return copy.deepcopy(_DEFAULT_SETTINGS)
    return _deep_merge(_DEFAULT_SETTINGS, data)


_settings = _load_settings()


def get_settings_path():
    return _SETTINGS_PATH


def get_settings():
    """Return a fresh copy of the current on-disk settings."""
    return _load_settings()


# ============================================================
# Resolved values — settings.json first, env var overrides.
# ============================================================

def _s(key, env_var=None, cast=None):
    """Read a setting: env var > settings.json > default."""
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            return cast(env_val) if cast else env_val
    val = _settings.get(key, _DEFAULT_SETTINGS.get(key))
    return cast(val) if cast and val is not None else val


FLASK_PORT         = _s("flask_port",         "FLASK_PORT",         int)
PROJECTS_DIR       = os.environ.get("PROJECTS_DIR") or os.path.join(os.path.dirname(__file__), _settings.get("projects_dir", "projects"))
MAX_UPLOAD_SIZE    = _s("max_upload_size_mb",  "MAX_UPLOAD_SIZE_MB", int) * 1024 * 1024
SESSION_TTL        = _s("session_ttl",         "SESSION_TTL",        int)
MAX_WORKERS        = _s("max_workers",         "MAX_WORKERS",        int)
UNPAYWALL_EMAIL    = _s("unpaywall_email",     "UNPAYWALL_EMAIL")
SCHOLARLY_ENABLED  = _s("scholarly_enabled",    "SCHOLARLY_ENABLED",  lambda v: str(v).lower() in ("true", "1", "yes"))
PDF_CONVERTER      = (_s("pdf_converter",      "PDF_CONVERTER") or "pymupdf4llm").lower()

# API keys — env vars ONLY (secrets must not be stored in settings.json).
GOOGLE_API_KEY           = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID            = os.environ.get("GOOGLE_CSE_ID", "") or os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
OPENALEX_API_KEY         = os.environ.get("OPENALEX_API_KEY", "")


def get_pdf_converter():
    """Live value — legacy single-converter API. Kept for backward compatibility;
    prefer get_pdf_converter_pair() to respect the fast / high-quality split."""
    env_val = os.environ.get("PDF_CONVERTER")
    if env_val:
        return env_val.lower()
    s = _load_settings()
    return (s.get("pdf_converter") or s.get("pdf_converter_fast") or "pymupdf_text").lower()


def get_pdf_converter_pair():
    """Return (fast_backend, high_quality_backend, page_limit) from live settings.

    Env vars PDF_CONVERTER_FAST / PDF_CONVERTER_HIGH_QUALITY / PDF_QUALITY_PAGE_LIMIT
    override individually.
    """
    s = _load_settings()
    fast = (os.environ.get("PDF_CONVERTER_FAST")
            or s.get("pdf_converter_fast")
            or _DEFAULT_SETTINGS["pdf_converter_fast"]).lower()
    hq   = (os.environ.get("PDF_CONVERTER_HIGH_QUALITY")
            or s.get("pdf_converter_high_quality")
            or _DEFAULT_SETTINGS["pdf_converter_high_quality"]).lower()
    try:
        limit = int(os.environ.get("PDF_QUALITY_PAGE_LIMIT")
                    or s.get("pdf_quality_page_limit")
                    or _DEFAULT_SETTINGS["pdf_quality_page_limit"])
    except (TypeError, ValueError):
        limit = _DEFAULT_SETTINGS["pdf_quality_page_limit"]
    return fast, hq, limit


def get_claim_check_settings():
    """Return the live claim_check settings block, with defaults filled in."""
    s = _load_settings().get("claim_check") or {}
    merged = dict(_DEFAULT_SETTINGS["claim_check"])
    merged.update(s)
    return merged


def get_reference_match_settings():
    """Return the live reference_match settings block, with defaults filled in."""
    s = _load_settings().get("reference_match") or {}
    merged = dict(_DEFAULT_SETTINGS["reference_match"])
    merged.update(s)
    return merged


def get_openai_api_key():
    """Env var only. Returns "" if not set."""
    return os.environ.get("OPENAI_API_KEY", "")


def update_settings(partial):
    """Merge partial updates into settings.json. Returns the full updated settings.

    Does NOT allow writing API keys (safety — they belong in env vars only).
    """
    FORBIDDEN_KEYS = {"openai_api_key", "google_api_key", "google_cse_id",
                      "semantic_scholar_api_key", "openalex_api_key"}
    current = _load_settings()
    for k, v in partial.items():
        if k in FORBIDDEN_KEYS:
            continue
        if isinstance(v, dict) and isinstance(current.get(k), dict):
            for kk, vv in v.items():
                if kk in FORBIDDEN_KEYS:
                    continue
                current[k][kk] = vv
        else:
            current[k] = v
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
    except OSError as e:
        print(f"[config] Could not write settings.json: {e}", file=sys.stderr)
    return current


# ============================================================
# Startup banner — called from app.py on import
# ============================================================

def print_startup_banner():
    """Print all resolved settings + API key status to the console."""
    s = _load_settings()
    cc = get_claim_check_settings()

    # PDF converter availability check — report on both fast + high-quality slots
    fast_be, hq_be, page_limit = get_pdf_converter_pair()
    def _backend_status(name):
        if name == "docling":
            try: __import__("docling"); return "available"
            except ImportError: return "NOT INSTALLED"
        if name == "pymupdf4llm":
            try: __import__("pymupdf4llm"); return "available"
            except ImportError: return "NOT INSTALLED"
        if name == "pymupdf_text":
            try: __import__("pymupdf"); return "available"
            except ImportError: return "NOT INSTALLED"
        return "unknown"
    fast_status = _backend_status(fast_be)
    hq_status = _backend_status(hq_be)

    # OpenAI package check
    try:
        import openai  # noqa: F401
        openai_status = "available"
    except ImportError:
        openai_status = "NOT INSTALLED — run `pip install openai`"

    def _key_status(val):
        return "set" if val else "MISSING"

    print("\n" + "=" * 60)
    print("  References Checker - Configuration")
    print("=" * 60)
    print()
    print("  Settings file:          ", get_settings_path())
    print()
    print("  --- Server ---")
    print(f"  Flask port:              {FLASK_PORT}")
    print(f"  Projects dir:            {PROJECTS_DIR}")
    print(f"  Max upload size:         {MAX_UPLOAD_SIZE // (1024*1024)} MB")
    print(f"  Session TTL:             {SESSION_TTL}s")
    print(f"  Max workers:             {MAX_WORKERS}")
    print()
    print("  --- Lookup pipeline ---")
    print(f"  Unpaywall email:         {UNPAYWALL_EMAIL or 'MISSING'}")
    print(f"  Scholarly (G.Scholar):   {'enabled' if SCHOLARLY_ENABLED else 'disabled'}")
    print(f"  Semantic Scholar key:    {_key_status(SEMANTIC_SCHOLAR_API_KEY)}")
    print(f"  Google API key:          {_key_status(GOOGLE_API_KEY)}")
    print(f"  Google CSE ID:           {_key_status(GOOGLE_CSE_ID)}")
    print(f"  OpenAlex key:            {_key_status(OPENALEX_API_KEY)} (optional)")
    print()
    print("  --- Download rules ---")
    try:
        from download_rules import rules_summary
        rules = rules_summary()
        user_rules = (s.get("download") or {}).get("site_rules") or {}
        for r in rules:
            print(f"  {r['domain']:24}  builtin   ({r.get('notes', '')})")
        for domain in user_rules:
            print(f"  {domain:24}  user-override")
        if not rules and not user_rules:
            print("  (none configured)")
    except ImportError:
        print("  download_rules.py not found")
    print()
    print("  --- PDF to Markdown ---")
    print(f"  Fast backend:            {fast_be}  ({fast_status})")
    print(f"  High-quality backend:    {hq_be}  ({hq_status})")
    print(f"  Quality page limit:      {page_limit} (<=this uses high-quality; > uses fast)")
    print()
    print("  --- LLM claim check ---")
    print(f"  Enabled:                 {cc.get('enabled')}")
    print(f"  OpenAI API key:          {_key_status(get_openai_api_key())}")
    print(f"  openai package:          {openai_status}")
    print(f"  Model:                   {cc.get('openai_model')}")
    print(f"  Max reference chars:     {cc.get('max_ref_chars')}")
    print(f"  Max batch cost:          ${cc.get('max_batch_usd'):.2f}")
    print(f"  Request timeout:         {cc.get('request_timeout_s')}s")
    print(f"  Max retries:             {cc.get('max_retries')}")
    print(f"  Max parallel calls:      {cc.get('max_parallel')}")
    print()
    rm = get_reference_match_settings()
    print("  --- Reference identity match ---")
    print(f"  Enabled:                 {rm.get('enabled')}")
    print(f"  Auto-check on download:  {rm.get('auto_check_on_download')}")
    print(f"  Model:                   {rm.get('openai_model')}")
    print(f"  Excerpt chars:           {rm.get('max_chars')} (~2 pages)")
    print(f"  Max parallel calls:      {rm.get('max_parallel')}")
    print()
    print("=" * 60)
    print()
