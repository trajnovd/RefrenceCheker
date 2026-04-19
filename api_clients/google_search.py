import threading
import time
import re
import logging
import requests
from http_client import get_session
from config import GOOGLE_API_KEY, GOOGLE_CSE_ID

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call = 0.0
_DELAY = 0.2

_ENABLED = bool(GOOGLE_API_KEY and GOOGLE_CSE_ID)
_disabled = False

SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# Fragile / non-content domain classification lives in download_rules.py
# (single source of truth — v6.1 A0.3). Re-exported here under local names
# so existing callers + pinned tests keep working.
from download_rules import is_fragile as _is_fragile_pdf_url  # noqa: E402
from download_rules import is_noncontent as _is_noncontent_url  # noqa: E402
from download_rules import is_html_paywall as _is_html_paywall_url  # noqa: E402
from download_rules import FRAGILE_PDF_DOMAINS as _FRAGILE_PDF_DOMAINS  # noqa: E402
from download_rules import NONCONTENT_DOMAINS as _NONCONTENT_DOMAINS  # noqa: E402


def _rate_limit():
    global _last_call
    with _lock:
        now = time.time()
        wait = _DELAY - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


_DOC_ID_RE = re.compile(r"(\d{4}-\d{1,5})|(SR\s+\d+-\d+)|(Release\s+No\.\s*\d{2,5}-\d{1,5})", re.IGNORECASE)


def _extract_doc_id(number_field=None, note_field=None, title=None):
    """Find a stable document identifier (Fed SR letter, SEC press release, etc.)
    in the bib's `number`, `note`, or `title` fields.

    Returns the matched id string (e.g. "2024-137", "SR 11-7") or None.
    """
    for source in (number_field, note_field, title):
        if not source:
            continue
        m = _DOC_ID_RE.search(str(source))
        if m:
            return m.group(0).strip()
    return None


def lookup_google_search(title, doi=None, authors=None, doc_id=None, timeout=10, max_retries=3):
    """Multi-pass Google Custom Search with graceful fallback.

    Query precedence (first non-empty result wins):
      1. `"<title>" <last_name>`     — title + first-author surname (most precise)
      2. `"<title>" <doi>`           — title + DOI (if present)
      3. `"<title>"`                 — title only (broadest text search)
      4. `"<title>" filetype:pdf`    — explicit PDF-only search

    If step 3 returns a result but no pdf_url, step 4 is tried as a supplement
    (keeping the step-3 url/abstract, just adding the pdf_url).

    Author names are a strong disambiguating keyword; adding them to the first
    attempt improves precision for common titles ("Attention", "Introduction"...)
    without hurting precise titles (Google treats extra terms as soft constraints).

    Returns: {url, pdf_url, abstract} or None.
    """
    global _disabled
    if not _ENABLED or _disabled or not title:
        return None

    quoted_title = f'"{title}"'
    first_last = _first_author_last_name(authors)

    result = None

    # Pass 1a: title + first-author surname (most precise; corporate authors return None)
    if first_last:
        result = _run_query(f"{quoted_title} {first_last}", title, timeout, max_retries)

    # Pass 1b: DOI-qualified query
    if result is None and doi:
        result = _run_query(f"{quoted_title} {doi}", title, timeout, max_retries)

    # Pass 1c: title-only fallback
    if result is None:
        result = _run_query(quoted_title, title, timeout, max_retries)

    # Pass 1d: colon-split relaxed query. Many bib titles are user-composed
    # ("SR 11-7: Guidance on Model Risk Management") while the official document
    # has a different exact title ("Supervisory Guidance on Model Risk Management").
    # Quoting only the prefix lets Google find the canonical document.
    if (result is None or not result.get("pdf_url")) and ":" in title:
        prefix, suffix = title.split(":", 1)
        prefix = prefix.strip()
        suffix = suffix.strip()
        if prefix and suffix:
            relaxed = _run_query(f'"{prefix}" {suffix}', title, timeout, max_retries)
            if relaxed:
                if result is None:
                    result = relaxed
                elif relaxed.get("pdf_url") and not result.get("pdf_url"):
                    # Relaxed found a PDF — its source is likely the canonical one.
                    # Promote both url and pdf_url over the prior commentary-site best_url.
                    result["pdf_url"] = relaxed["pdf_url"]
                    if relaxed.get("url"):
                        result["url"] = relaxed["url"]
                    if relaxed.get("abstract") and not result.get("abstract"):
                        result["abstract"] = relaxed["abstract"]

    # Pass 1e: doc-identifier search. For press releases, supervisory letters,
    # standards, etc. the bib often has a stable identifier in `number` or `note`
    # ("Press Release 2024-137", "SR 11-7"). When the title-based passes return
    # nothing (long descriptive titles often miss exact-phrase indexing), this
    # finds the canonical issuer page reliably.
    if (result is None or not result.get("pdf_url")) and doc_id:
        # Pair the id with a few title keywords for relevance scoring on the parser side
        title_words = " ".join(title.split()[:4]) if title else ""
        id_query = f'"{doc_id}" {title_words}'.strip()
        id_result = _run_query(id_query, title or doc_id, timeout, max_retries)
        if id_result:
            if result is None:
                result = id_result
            elif id_result.get("pdf_url") and not result.get("pdf_url"):
                result["pdf_url"] = id_result["pdf_url"]
                if id_result.get("url"):
                    result["url"] = id_result["url"]
                if id_result.get("abstract") and not result.get("abstract"):
                    result["abstract"] = id_result["abstract"]
            elif not result.get("url") and id_result.get("url"):
                result["url"] = id_result["url"]

    # Pass 2: explicit PDF search if we have a hit but no PDF yet
    if result is not None and not result.get("pdf_url"):
        pdf_result = _run_query(f"{quoted_title} filetype:pdf", title, timeout, max_retries)
        if pdf_result and pdf_result.get("pdf_url"):
            logger.debug("GoogleSearch pass-2 filetype:pdf found PDF: %s", pdf_result["pdf_url"])
            result["pdf_url"] = pdf_result["pdf_url"]
            if not result.get("url"):
                result["url"] = pdf_result.get("url")
    elif result is None:
        result = _run_query(f"{quoted_title} filetype:pdf", title, timeout, max_retries)

    # Pass 3 (last resort): RELAXED bare-words query.
    # Earlier passes all use exact-phrase quoting around the title — that's
    # precise but brittle. For old books and reprints, the exact phrase often
    # doesn't appear verbatim on legitimate content hosts (author faculty
    # pages reword subtitles, course pages truncate, mirrors abbreviate).
    # When we still have no usable URL or PDF after the strict passes,
    # drop the quotes and let Google's relevance ranking surface candidates
    # we'd otherwise miss. The author surname stays as a strong anchor so
    # the search doesn't drift to unrelated papers sharing keywords.
    #
    # Regression: Hasbrouck2007 — strict passes returned only Amazon / RG /
    # Google Books (all filtered by the parser); the relaxed pass surfaces
    # Hasbrouck's NYU faculty page, which has chapter excerpts.
    needs_rescue = (result is None
                    or not result.get("pdf_url"))
    if needs_rescue and first_last:
        # Title as bare words — strip quotes/colons/punctuation that would
        # otherwise re-introduce phrase matching.
        bare_title = re.sub(r'[":,.;]', " ", title)
        bare_title = re.sub(r"\s+", " ", bare_title).strip()
        relaxed_query = f"{bare_title} {first_last}"
        logger.debug("GoogleSearch pass-3 relaxed bare-words query: q=%s", relaxed_query)
        relaxed = _run_query(relaxed_query, title, timeout, max_retries)
        if relaxed:
            if result is None:
                result = relaxed
            else:
                # Merge — only fill in missing fields, never overwrite a
                # better hit from an earlier strict pass.
                if relaxed.get("url") and not result.get("url"):
                    result["url"] = relaxed["url"]
                if relaxed.get("pdf_url") and not result.get("pdf_url"):
                    result["pdf_url"] = relaxed["pdf_url"]
                if relaxed.get("abstract") and not result.get("abstract"):
                    result["abstract"] = relaxed["abstract"]

    return result


# Corporate/institutional author markers. When any of these tokens appears in the
# author field, we skip the "first surname" keyword pass — adding "System" to the
# query for "Board of Governors of the Federal Reserve System" pushes the real Fed
# page out of Google's top results.
_CORPORATE_TOKENS = {
    "board", "bank", "bureau", "commission", "council", "society", "association",
    "institute", "department", "agency", "reserve", "government", "foundation",
    "center", "centre", "office", "authority", "committee", "service", "services",
    "ministry", "treasury", "fund", "organization", "organisation", "group",
    "consortium", "alliance", "federation", "union", "corporation", "company",
    "inc", "corp", "ltd", "llc", "co",
}


def _is_corporate_author(name):
    """Heuristic: detect institutional/corporate authors where last-word ≠ surname.
    e.g. 'Board of Governors of the Federal Reserve System' → True (last word 'System' is not a surname)
         'Andrew W. Lo' → False (normal personal name)
    """
    if not name:
        return False
    words = str(name).lower().split()
    return any(w.strip(".,;") in _CORPORATE_TOKENS for w in words)


def _first_author_last_name(authors):
    """Extract the first author's surname from various author-field shapes.

    Handles: list of strings, "A and B and C" string, "A; B; C" string,
    "Last, First" name format, and plain "First Last" format.
    Returns the surname, or None for empty input or corporate/institutional authors
    (e.g. 'Board of Governors of the Federal Reserve System').
    """
    if not authors:
        return None
    if isinstance(authors, list):
        first = authors[0] if authors else None
    else:
        s = str(authors).strip()
        # Check the WHOLE string for corporate tokens BEFORE splitting on " and " —
        # corporate names like "Securities and Exchange Commission" use "and" as
        # an internal connector, not a list separator.
        if _is_corporate_author(s):
            return None
        # Personal-name list: split on " and " or ";"
        for sep in [" and ", ";"]:
            if sep in s:
                first = s.split(sep, 1)[0].strip()
                break
        else:
            first = s.strip()
    if not first:
        return None
    first = str(first).strip().strip("{}").strip()
    # Skip corporate authors — their "last word" isn't a surname
    if _is_corporate_author(first):
        return None
    # "Last, First" form (BibTeX convention) → "Last"
    if "," in first:
        return first.split(",", 1)[0].strip()
    # "First M. Last" form → take last word
    parts = first.split()
    return parts[-1] if parts else None


def _run_query(query, title, timeout, max_retries):
    """Run one Google Custom Search query and parse results. Returns dict or None."""
    global _disabled
    if _disabled:
        return None
    logger.debug("GoogleSearch query: q=%s", query)
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": 5,
    }
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = get_session().get(SEARCH_URL, params=params, timeout=timeout)
            logger.debug("GoogleSearch response: status=%d title=%s", resp.status_code, title)
            if resp.status_code == 429:
                logger.debug("GoogleSearch rate-limited (429): title=%s attempt=%d", title, attempt)
                if attempt == max_retries - 1:
                    _disabled = True
                    logger.warning("Google Search persistently rate-limited (quota exceeded). Disabling for session.")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.debug("GoogleSearch failed: title=%s status=%d body=%s", title, resp.status_code, resp.text[:300])
                return None
            assume_pdf = "filetype:pdf" in query.lower()
            result = _parse_results(resp.json(), title, assume_pdf=assume_pdf)
            logger.debug("GoogleSearch result: title=%s found=%s url=%s pdf=%s",
                          title, result is not None,
                          result.get("url") if result else None,
                          result.get("pdf_url") if result else None)
            return result
        except Exception as e:
            logger.warning(f"Google Search lookup failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            continue
    return None


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _parse_results(data, query_title, assume_pdf=False):
    """Parse Google Custom Search results.

    assume_pdf: when True (pass 2 with `filetype:pdf`), Google already filtered
    server-side so every item is a PDF regardless of URL extension. We then pick
    the first relevance-matching item as pdf_url.
    """
    items = data.get("items", [])
    if not items:
        logger.debug("GoogleSearch no items returned: title=%s", query_title)
        return None

    norm_query = _normalize(query_title)
    pdf_url = None
    best_url = None
    abstract = None

    for item in items:
        link = item.get("link", "")
        item_title = item.get("title", "")
        snippet = item.get("snippet", "")

        # Check title-or-snippet relevance.
        #
        # Title alone is too brittle: Google's `title` for a faculty-hosted
        # PDF is usually just the filename ("Hasbrouck's book.pdf",
        # "lecture-notes.pdf"), which has ~0 overlap with a long bib title.
        # The snippet contains text extracted from the PDF body itself —
        # much richer signal. Set-based overlap means a long snippet can't
        # artificially inflate the match (each word counts once).
        #
        # Regression: Hasbrouck2007EmpiricalMicrostructure — Google's first
        # result for the manual search is a Buffalo .edu PDF whose Google
        # title is just "Hasbrouck's book"; only the snippet mentions
        # "Empirical Market Microstructure".
        query_words = set(norm_query.split())
        if not query_words:
            continue
        combined = (item_title or "") + " " + (snippet or "")
        combined_words = set(_normalize(combined).split())
        overlap = len(query_words & combined_words) / len(query_words)
        if overlap < 0.5:
            continue

        # Skip commercial-listing domains (Amazon, Goodreads, etc.) — they have no
        # content to feed to claim-checking, and users already see the book on the
        # shopping site via the normal web.
        if _is_noncontent_url(link):
            logger.debug("GoogleSearch skipping non-content domain: %s", link)
            continue

        # Grab first PDF link. When assume_pdf=True (filetype:pdf query), every
        # relevance-matching item is a PDF candidate.
        # Skip fragile publisher domains — Google ranking surfaces them, but they'll
        # bot-block on download. We want a non-fragile alternate (university mirror,
        # NBER, author page, arXiv).
        link_lower = link.lower()
        is_pdf_link = link_lower.endswith(".pdf") or "/pdf/" in link_lower or assume_pdf
        if not pdf_url and is_pdf_link and not _is_fragile_pdf_url(link):
            pdf_url = link

        # Grab first relevant page URL — but skip HTML-paywall hosts.
        # JSTOR / Wiley / Oxford Academic / T&F / ResearchGate return 200 with
        # a captcha or "request full text" teaser; saving those as content
        # poisons the .md and breaks ref_match. Fall through to the next item
        # (university mirror, author homepage, etc.).
        if not best_url and not _is_html_paywall_url(link):
            best_url = link

        # Use snippet as abstract if it's substantial
        if not abstract and snippet and len(snippet) > 80:
            abstract = snippet.strip()

    if not best_url and not pdf_url:
        logger.debug("GoogleSearch no relevant matches: title=%s items_checked=%d", query_title, len(items))
        return None

    return {
        "url": best_url,
        "pdf_url": pdf_url,
        "abstract": abstract,
    }
