"""URL normalizer registry.

Rewrites known landing-page URLs to their direct-content variants before
download so the pipeline sees a PDF URL instead of an abstract page.

Example: `https://arxiv.org/abs/2308.00016` → `https://arxiv.org/pdf/2308.00016`.

Before v6.1 A0, `_normalize_bib_url` in file_downloader.py was a
hard-coded pair of arxiv regexes. Every new landing-page type (PubMed,
OpenReview forum pages, DOI-org, etc.) required editing the function.

Now: normalizers self-register via `register(regex, rewriter)`; a single
`normalize(url)` walks the list (first match wins) and returns either a
rewritten URL or the input unchanged.

Extending:
    @register_normalizer(re.compile(r"..."))
    def _rewrite_foo(match):
        return f"https://foo.example.com/pdf/{match.group(1)}"
"""

import logging
import re

logger = logging.getLogger(__name__)


# ============================================================
# Registry
# ============================================================

# List of (compiled_regex, rewriter_fn). Order matters — first match wins.
# Rewriter signature: `fn(match: re.Match) -> str` returning the rewritten URL.
_NORMALIZERS = []


def register_normalizer(pattern):
    """Decorator: register a URL rewriter under the given regex pattern.

    The regex is matched against the full URL with `.match()` (anchored
    at the start). Use `.search()`-like anchoring only if truly needed.
    """
    def _wrap(fn):
        _NORMALIZERS.append((pattern, fn))
        return fn
    return _wrap


def normalize(url):
    """Walk the registry, return the first rewritten URL or the input unchanged.

    None / empty input is returned as-is. Rewriters that raise are skipped
    (logged at debug) rather than propagating — a bad normalizer must not
    block the fallback pipeline.
    """
    if not url:
        return url
    for pattern, fn in _NORMALIZERS:
        m = pattern.match(url)
        if not m:
            continue
        try:
            rewritten = fn(m)
        except Exception as e:
            logger.debug("normalizer %s raised on %s: %s", fn.__name__, url, e)
            continue
        if rewritten and rewritten != url:
            logger.debug("Normalized %s -> %s (via %s)", url, rewritten, fn.__name__)
            return rewritten
    return url


def _reset_for_tests():
    """Clear the registry. Only for test setup."""
    _NORMALIZERS.clear()


def registered_count():
    """Number of normalizers currently registered — useful in tests."""
    return len(_NORMALIZERS)


# ============================================================
# Built-in normalizers
# ============================================================

# arxiv.org/abs/<id>    → arxiv.org/pdf/<id>
# arxiv.org/abs/<id>v3  → arxiv.org/pdf/<id>   (strip version)
_ARXIV_ABS_RE = re.compile(
    r"https?://arxiv\.org/abs/([\w./-]+?)(?:v\d+)?/?$", re.IGNORECASE
)

# arxiv.org/html/<id>vN → arxiv.org/pdf/<id>
# The HTML rendition often breaks downstream PDF extraction; prefer the
# canonical PDF.
_ARXIV_HTML_RE = re.compile(
    r"https?://arxiv\.org/html/([\w./-]+?)(?:v\d+)?/?$", re.IGNORECASE
)


@register_normalizer(_ARXIV_ABS_RE)
def _rewrite_arxiv_abs(m):
    return f"https://arxiv.org/pdf/{m.group(1)}"


@register_normalizer(_ARXIV_HTML_RE)
def _rewrite_arxiv_html(m):
    return f"https://arxiv.org/pdf/{m.group(1)}"


# --- Stubs for A1 phases — registered here so the shape is set.
# These rewriters return None today (no-op) because the endpoints they
# target aren't wired yet. A1 replaces these with real rewrites.

# openreview.net/forum?id=<id> → openreview.net/pdf?id=<id>
_OPENREVIEW_FORUM_RE = re.compile(
    r"https?://openreview\.net/forum\?id=([\w-]+)", re.IGNORECASE
)


@register_normalizer(_OPENREVIEW_FORUM_RE)
def _rewrite_openreview_forum(m):
    return f"https://openreview.net/pdf?id={m.group(1)}"
