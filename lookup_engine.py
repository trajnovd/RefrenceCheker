import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from api_clients.crossref import lookup_crossref
from api_clients.unpaywall import lookup_unpaywall
from api_clients import semantic_scholar as _s2_module
from api_clients.semantic_scholar import lookup_semantic_scholar
from api_clients.wikipedia import lookup_wikipedia
from api_clients import google_search as _gs_module
from api_clients.google_search import lookup_google_search
from api_clients import scholarly_client as _sch_module
from api_clients.scholarly_client import lookup_scholarly
from api_clients.arxiv_client import search_arxiv
from api_clients.openalex import lookup_openalex
from config import MAX_WORKERS, SEMANTIC_SCHOLAR_API_KEY

import re

BOOK_TYPES = {"book", "inbook", "incollection", "booklet", "proceedings", "inproceedings"}
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arXiv\.(.+)$", re.IGNORECASE)

# arXiv submission IDs encode the year:
#   - Modern (2007+): YYMM.NNNNN  →  20YY  (e.g. "1602.03032" → 2016)
#   - Legacy:         <subject>/YYMMNNN  →  19YY or 20YY  (e.g. "math/0102001" → 2001)
_ARXIV_MODERN_RE = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}", re.IGNORECASE)
_ARXIV_LEGACY_RE = re.compile(r"^[a-z\-.]+/(\d{2})(\d{2})\d{3}", re.IGNORECASE)


def _arxiv_year(arxiv_id):
    """Best-effort: extract the submission year from an arXiv id. Returns int or None.

    Used to reject title-search overrides where the year is wildly off — e.g. arXiv
    1602.03032 ("LSTM: A Search Space Odyssey", 2016) coming back for the bib's
    1997 LSTM paper. A short generic title shared between papers decades apart
    is a common wrong-paper failure mode for fuzzy title search.
    """
    if not arxiv_id:
        return None
    s = arxiv_id.strip().lower()
    m = _ARXIV_MODERN_RE.match(s)
    if m:
        yy = int(m.group(1))
        return 2000 + yy if yy < 91 else 1900 + yy   # arXiv started 1991
    m = _ARXIV_LEGACY_RE.match(s)
    if m:
        yy = int(m.group(1))
        return 2000 + yy if yy < 91 else 1900 + yy
    return None


def _years_compatible(bib_year, arxiv_year, max_gap=3):
    """Two years are compatible if both unknown, or one unknown, or within max_gap.

    A small gap (<=3 years) accommodates legitimate cases like a paper posted to
    arXiv as a preprint earlier than the journal publication, or vice-versa.
    """
    if bib_year is None or arxiv_year is None:
        return True
    try:
        return abs(int(bib_year) - int(arxiv_year)) <= max_gap
    except (TypeError, ValueError):
        return True

# Fragile-publisher + non-content domain lists live in download_rules.py
# (single source of truth — v6.1 A0.3). Re-exported here under the legacy
# names so existing callers inside this module keep working.
from download_rules import is_fragile as _is_fragile_pdf  # noqa: E402

logger = logging.getLogger(__name__)


def _log_step(bib_key, source, found, detail=""):
    tag = "OK" if found else "MISS"
    msg = f"[{bib_key}] {source}: {tag}"
    if detail:
        msg += f" — {detail}"
    logger.info(msg)


def _humanize_bib_url_failure(failure_info):
    """Compose a user-facing error message from pre_download_bib_url failure info."""
    kind = (failure_info or {}).get("kind") or "unknown"
    code = (failure_info or {}).get("http_status")
    url = (failure_info or {}).get("url") or ""
    if kind == "bot_blocked":
        return (f"Site bot-blocked us ({'HTTP ' + str(code) + ', ' if code else ''}"
                "Cloudflare/WAF challenge could not be solved automatically). "
                "Use Paste Content to add the article text manually.")
    if kind == "js_challenge":
        return ("URL serves a JS challenge that Playwright could not pass. "
                "Use Paste Content to add the article text manually.")
    if kind == "http_4xx" and code:
        return f"Bib URL returned HTTP {code} (citation is unreachable — fix the URL)"
    if kind == "http_5xx" and code:
        return f"Bib URL returned HTTP {code} (server error — fix the URL or retry later)"
    if kind == "network":
        return "Bib URL is unreachable (network error — check the URL)"
    if kind == "validation":
        detail = (failure_info or {}).get("detail") or ""
        return f"Bib URL did not return valid content ({detail or 'invalid response'})"
    return f"Bib URL is unreachable ({kind})"


def make_url_source_result(ref):
    """Build a result for a ref whose non-DOI bib URL was pre-downloaded.

    When the bib URL succeeded AND the ref has no DOI, the URL itself IS the
    citation source — running the API pipeline only invites false matches
    (vanity titles like "What We Do", "Trend-Following" fuzzy-match unrelated
    papers and pollute the result with wrong PDFs / DOIs / abstracts).

    This bypass produces a clean URL-sourced result: bib identity preserved,
    no API enrichment, no `pdf_url_fallbacks` to mislead the download
    orchestrator. Sources is `["URL"]` so the UI shows where the content came
    from. PDF detection is preserved for `.pdf` URLs (still wired through
    pre_download_bib_url's normalization)."""
    from file_downloader import _normalize_bib_url
    bib_url = ref.get("url")
    normalized = _normalize_bib_url(bib_url) if bib_url else bib_url
    is_pdf = bool(normalized) and (
        normalized.lower().endswith(".pdf") or "/pdf/" in normalized.lower()
    )
    bib_authors = ref.get("authors")
    if isinstance(bib_authors, list):
        authors = bib_authors
    elif bib_authors:
        authors = [bib_authors]
    else:
        authors = []
    return {
        "bib_key": ref["bib_key"],
        "title": ref.get("title"),
        "authors": authors,
        "year": ref.get("year"),
        "journal": ref.get("journal"),
        "doi": None,
        "abstract": None,
        "pdf_url": normalized if is_pdf else None,
        "url": bib_url,
        "citation_count": None,
        "sources": ["URL"],
        "status": "found_pdf" if is_pdf else "found_web_page",
        "error": None,
        "raw_bib": ref.get("raw_bib"),
        "files_origin": {},
        # Hard contract for download_reference_files: the bib URL IS the
        # source — never run the PDF tier orchestrator (openreview /
        # google_rescue / wayback would title-search and find unrelated
        # papers like "What We Do Not Fund" for "What We Do").
        "url_source_only": True,
    }


def make_bib_url_unreachable_result(ref, failure_info):
    """Build a result dict for a reference whose bib URL could not be downloaded.

    Used in place of process_reference when the bib supplied a URL but it 4xx'd /
    timed out / failed validation. Skipping the lookup pipeline avoids the very
    common failure where a generic title-search finds an unrelated paper and
    downloads it as the "source" (e.g. @misc{ManTrendFollowing} -> some random
    arXiv paper titled "Trend-Following").
    """
    bib_key = ref["bib_key"]
    return {
        "bib_key": bib_key,
        "title": ref.get("title"),
        "authors": ref.get("authors") if isinstance(ref.get("authors"), list)
                   else [ref.get("authors")] if ref.get("authors") else [],
        "year": ref.get("year"),
        "journal": ref.get("journal"),
        "doi": ref.get("doi"),
        "abstract": None,
        "pdf_url": None,
        "url": ref.get("url"),
        "citation_count": None,
        "sources": ["URL"],
        "status": "bib_url_unreachable",
        "error": _humanize_bib_url_failure(failure_info),
        "bib_url_failure": {
            "http_status": (failure_info or {}).get("http_status"),
            "kind": (failure_info or {}).get("kind"),
        },
        "raw_bib": ref.get("raw_bib"),
    }


def process_reference(ref, metadata_only=False):
    bib_key = ref["bib_key"]

    if ref.get("status") == "insufficient_data":
        logger.info("[%s] title=%s — skipped (insufficient data)", bib_key, ref.get("title"))
        return {
            "bib_key": bib_key,
            "title": ref.get("title"),
            "authors": [],
            "year": None,
            "journal": None,
            "doi": None,
            "abstract": None,
            "pdf_url": None,
            "url": None,
            "citation_count": None,
            "sources": [],
            "status": "insufficient_data",
            "error": "No title or DOI in .bib entry",
            "raw_bib": ref.get("raw_bib"),
        }

    title = ref.get("title")
    doi = ref.get("doi")
    authors = ref.get("authors", "")
    year = ref.get("year")
    entry_type = ref.get("entry_type", "")
    is_book = entry_type in BOOK_TYPES

    # Promote DOI from a doi.org URL when no explicit doi field is present.
    # Mirrors the bib_parser path so already-saved refs (project.json from
    # before that parser change) also benefit on re-runs.
    if not doi and ref.get("url"):
        from bib_parser import extract_doi_from_url
        extracted = extract_doi_from_url(ref["url"])
        if extracted:
            doi = extracted

    logger.info("[%s] START title=%s doi=%s type=%s", bib_key, title, doi, entry_type)

    result = {
        "bib_key": bib_key,
        "title": title,
        "authors": authors if isinstance(authors, list) else [authors] if authors else [],
        "year": year,
        "journal": ref.get("journal"),
        "doi": doi,
        "abstract": None,
        "pdf_url": None,
        "url": ref.get("url"),
        "citation_count": None,
        "sources": [],
        "status": "not_found",
        "error": None,
        # Carry raw_bib forward so the BibTeX tab keeps working after Refresh / Add Reference.
        "raw_bib": ref.get("raw_bib"),
        # v6.1 A0.5: provenance for each downloaded file. A1 tiers call
        # provenance.record_origin(result, filetype, tier, url) on success;
        # the UI then shows "Downloaded via <tier>" per artifact.
        "files_origin": {},
    }

    # Step 0: If we have an arXiv ID (from eprint, URL, or DOI), set PDF link immediately
    arxiv_id = ref.get("arxiv_id")
    if not arxiv_id and doi:
        arxiv_match = _ARXIV_DOI_RE.match(doi)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1)
    if arxiv_id:
        result["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"
        result["url"] = result["url"] or f"https://arxiv.org/abs/{arxiv_id}"
        result["sources"].append("arxiv")
        _log_step(bib_key, "arXiv", True,
                  f"id={arxiv_id} pdf=https://arxiv.org/pdf/{arxiv_id}")

    # arXiv anchor: when the bib provides an arXiv ID, arXiv IS the canonical
    # source. Subsequent API enrichment must not override pdf_url, url, or
    # authors — APIs match by title and frequently surface the wrong paper
    # for generic ML titles ("Neural Machine Translation by ..." → matches
    # Sennrich's "Neural Machine Translation of Rare Words ..."). Citation
    # count and abstract are still safe to enrich.
    arxiv_anchor = bool(arxiv_id)

    # Step 1: CrossRef + Unpaywall (if non-arXiv DOI available)
    if doi and not arxiv_id:
        cr = lookup_crossref(doi)
        if cr:
            result["sources"].append("crossref")
            result["title"] = result["title"] or cr.get("title")
            result["authors"] = cr.get("authors") or result["authors"]
            result["journal"] = result["journal"] or cr.get("journal")
            result["year"] = result["year"] or cr.get("year")
            result["url"] = result["url"] or cr.get("url")
        _log_step(bib_key, "CrossRef", cr is not None,
                  f"title={cr.get('title')}" if cr else "")

        uw = lookup_unpaywall(doi)
        if uw:
            result["sources"].append("unpaywall")
            if uw.get("pdf_url"):
                result["pdf_url"] = uw["pdf_url"]
            # v6.1 §3.1: collect alternate OA locations for the fallback walker.
            for u in (uw.get("pdf_url_fallbacks") or []):
                result.setdefault("pdf_url_fallbacks", []).append(u)
        _log_step(bib_key, "Unpaywall", uw and uw.get("pdf_url"),
                  f"pdf_url={uw.get('pdf_url')}" if uw else "")

    # Step 1.5: OpenAlex
    oa = lookup_openalex(doi=doi, title=title, year=year)
    if oa:
        result["sources"].append("openalex")
        result["abstract"] = result["abstract"] or oa.get("abstract")
        result["citation_count"] = result["citation_count"] or oa.get("citation_count")
        result["doi"] = result["doi"] or oa.get("doi")
        if not result["pdf_url"] and oa.get("pdf_url"):
            result["pdf_url"] = oa["pdf_url"]
        # v6.1 §3.2: collect alternate OA locations for the fallback walker.
        for u in (oa.get("pdf_url_fallbacks") or []):
            result.setdefault("pdf_url_fallbacks", []).append(u)
        # Authors override: only when bib didn't provide a real author list
        # AND we don't have an arXiv anchor (API title-match may be wrong).
        if (not arxiv_anchor
                and (not result["authors"] or result["authors"] == [authors])):
            result["authors"] = oa.get("authors") or result["authors"]
        result["year"] = result["year"] or oa.get("year")
    _log_step(bib_key, "OpenAlex", oa is not None,
              f"abstract={'yes' if oa and oa.get('abstract') else 'no'} "
              f"pdf={'yes' if oa and oa.get('pdf_url') else 'no'} "
              f"citations={oa.get('citation_count') if oa else None}" if oa else "")

    # Step 2: Semantic Scholar
    s2 = lookup_semantic_scholar(doi=doi, title=title, year=year, authors_hint=authors)
    if s2:
        result["sources"].append("semantic_scholar")
        result["abstract"] = result["abstract"] or s2.get("abstract")
        result["citation_count"] = s2.get("citation_count")
        result["doi"] = result["doi"] or s2.get("doi")
        if not result["pdf_url"] and s2.get("pdf_url"):
            result["pdf_url"] = s2["pdf_url"]
        # If we got a DOI from S2 and didn't have one, try to resolve it
        if result["doi"] and not doi and not result["pdf_url"]:
            s2_arxiv = _ARXIV_DOI_RE.match(result["doi"])
            if s2_arxiv:
                arxiv_id = s2_arxiv.group(1)
                result["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"
                result["url"] = result["url"] or f"https://arxiv.org/abs/{arxiv_id}"
                if "arxiv" not in result["sources"]:
                    result["sources"].append("arxiv")
                _log_step(bib_key, "arXiv (via S2 DOI)", True,
                          f"id={arxiv_id} pdf=https://arxiv.org/pdf/{arxiv_id}")
            else:
                uw = lookup_unpaywall(result["doi"])
                if uw and uw.get("pdf_url"):
                    result["pdf_url"] = uw["pdf_url"]
                    if "unpaywall" not in result["sources"]:
                        result["sources"].append("unpaywall")
                    _log_step(bib_key, "Unpaywall (via S2 DOI)", True,
                              f"pdf_url={uw['pdf_url']}")
    _log_step(bib_key, "SemanticScholar", s2 is not None,
              f"abstract={'yes' if s2 and s2.get('abstract') else 'no'} "
              f"pdf={'yes' if s2 and s2.get('pdf_url') else 'no'} "
              f"citations={s2.get('citation_count') if s2 else None}" if s2 else "")

    # Step 2.5: arXiv search by title. Runs even when we already have a pdf_url, so
    # that preprints on arXiv can replace publisher/SSRN URLs that often 403.
    # BUT: when the bib entry had a DOI AND a DOI-based source (CrossRef/Unpaywall)
    # already set the pdf_url, we TRUST that and skip the override — title-matching
    # can find different papers that share a distinctive suffix (e.g. Harvey-Liu-Zhu
    # 2016 "...and the Cross-Section of Expected Returns" vs Pinchuk 2023 "Labor
    # Income Risk and the Cross-Section of Expected Returns", 70% word overlap).
    if title and not arxiv_id:
        has_doi = bool(ref.get("doi") or result.get("doi"))
        doi_resolved_pdf = (bool(result.get("pdf_url"))
                            and has_doi
                            and any(s in ("crossref", "unpaywall") for s in result["sources"]))

        arxiv = search_arxiv(title, authors=ref.get("authors"))
        if arxiv:
            current_pdf = (result.get("pdf_url") or "").lower()
            current_url = (result.get("url") or "").lower()
            # Year-mismatch guard: arXiv title-search returns a different paper for
            # short generic titles shared across decades (e.g. bib "Long short-term
            # memory" / 1997 → arXiv 1602.03032 "LSTM: A Search Space Odyssey" / 2016).
            # The arXiv id encodes the submission year — if it's wildly off from the
            # bib year, drop the match entirely rather than override.
            ax_year = _arxiv_year(arxiv.get("arxiv_id"))
            year_ok = _years_compatible(year, ax_year)
            if not year_ok:
                logger.info("[%s] Rejecting arXiv match — year mismatch: bib=%s arxiv=%s id=%s",
                            bib_key, year, ax_year, arxiv.get("arxiv_id"))
                _log_step(bib_key, "arXiv search", False,
                          f"rejected year mismatch (bib={year}, arxiv={ax_year})")
            else:
                if arxiv.get("pdf_url") and "arxiv.org" not in current_pdf and not doi_resolved_pdf:
                    if current_pdf:
                        logger.info("[%s] Overriding pdf_url (%s) with arXiv preprint (%s)",
                                    bib_key, result.get("pdf_url"), arxiv["pdf_url"])
                    result["pdf_url"] = arxiv["pdf_url"]
                elif doi_resolved_pdf:
                    logger.debug("[%s] Skipping arXiv pdf_url override — DOI-based PDF already present", bib_key)
                if arxiv.get("abstract") and not result.get("abstract"):
                    result["abstract"] = arxiv["abstract"]
                # URL override also respects DOI-resolved URLs
                if arxiv.get("url") and ("arxiv.org" not in current_url) and not doi_resolved_pdf:
                    result["url"] = arxiv["url"]
                if "arxiv" not in result["sources"]:
                    result["sources"].append("arxiv")
                _log_step(bib_key, "arXiv search", True,
                          f"id={arxiv.get('arxiv_id')} pdf={arxiv.get('pdf_url')}")
        else:
            _log_step(bib_key, "arXiv search", False)

    # Step 3: Wikipedia (for books, when no abstract found yet)
    if is_book and not result.get("abstract") and title:
        wiki = lookup_wikipedia(title, authors=authors)
        if wiki:
            result["sources"].append("wikipedia")
            result["abstract"] = result["abstract"] or wiki.get("abstract")
            if not result["url"] and wiki.get("url"):
                result["url"] = wiki["url"]
        _log_step(bib_key, "Wikipedia", wiki is not None,
                  f"page={wiki.get('wiki_title')} url={wiki.get('url')}" if wiki else "")

    # Step 4: Google Custom Search (cheap, API-based fallback).
    # Fires when there's no PDF yet OR when the current pdf_url is on a known-fragile
    # publisher domain (Wiley / SSRN / econstor / ScienceDirect / Springer / JSTOR).
    # Those publishers claim OA via Unpaywall but bot-block anonymous downloads —
    # a university mirror / author homepage is almost always downloadable.
    #
    # Books also always trigger Google Search even when a pdf_url is set: OpenAlex's
    # `best_oa_location` for textbooks is unreliable (often points to a Zenodo
    # deposit unrelated to the work — see russell2020artificial regression), and
    # course-mirror PDFs found by Google are reliable.
    # arXiv anchor short-circuits Step 4: arXiv is the source, no Google
    # Search override (regression: bahdanau2015neural — inproceedings is in
    # BOOK_TYPES, so the book-mirror override replaced arxiv.org with iclr.cc).
    current_is_fragile = _is_fragile_pdf(result.get("pdf_url"))
    if (title and not arxiv_anchor
            and (is_book or not result.get("pdf_url") or current_is_fragile)):
        # Extract a doc identifier (Press Release 2024-137, SR 11-7, etc.) from the bib's
        # number/note fields — strong fallback when the user-composed title doesn't index well.
        from api_clients.google_search import _extract_doc_id
        all_fields = ref.get("all_fields") or {}
        doc_id = _extract_doc_id(
            number_field=all_fields.get("number"),
            note_field=all_fields.get("note"),
            title=title,
        )
        gs = lookup_google_search(title, doi=result.get("doi"),
                                   authors=result.get("authors") or ref.get("authors"),
                                   doc_id=doc_id)
        if gs:
            result["sources"].append("google_search")
            result["abstract"] = result["abstract"] or gs.get("abstract")
            gs_pdf = gs.get("pdf_url")
            # Override fragile publisher URL with a non-fragile Google find.
            if gs_pdf and not result.get("pdf_url"):
                result["pdf_url"] = gs_pdf
            elif gs_pdf and current_is_fragile and not _is_fragile_pdf(gs_pdf):
                logger.info("[%s] Overriding fragile pdf_url (%s) with Google-found alt (%s)",
                            bib_key, result["pdf_url"], gs_pdf)
                result["pdf_url"] = gs_pdf
            elif gs_pdf and is_book and not _is_fragile_pdf(gs_pdf):
                # Books: prefer course-mirror / author-page PDFs over OpenAlex's
                # `best_oa_location`, which often points to unrelated Zenodo
                # deposits or wrong-edition records.
                logger.info("[%s] Book — overriding pdf_url (%s) with Google-found mirror (%s)",
                            bib_key, result["pdf_url"], gs_pdf)
                result["pdf_url"] = gs_pdf
            if not result["url"] and gs.get("url"):
                result["url"] = gs["url"]
        _log_step(bib_key, "GoogleSearch", gs is not None,
                  f"url={gs.get('url')} pdf={gs.get('pdf_url')}" if gs else "")

    # Step 5: Google Scholar (LAST RESORT — scraping, rate-limited, bot-detected)
    # Only run if nothing else found ANY useful signal (no abstract, no pdf_url, no url).
    if (not result.get("abstract")
            and not result.get("pdf_url")
            and not result.get("url")
            and title):
        sch = lookup_scholarly(title)
        if sch:
            result["sources"].append("scholarly")
            result["abstract"] = result["abstract"] or sch.get("abstract")
            if not result["pdf_url"] and sch.get("pdf_url"):
                result["pdf_url"] = sch["pdf_url"]
            if not result["url"] and sch.get("url"):
                result["url"] = sch["url"]
        _log_step(bib_key, "GoogleScholar", sch is not None,
                  f"url={sch.get('url')} pdf={sch.get('pdf_url')}" if sch else "")

    # Step 6: arXiv preprint title search (FINAL fallback)
    # If nothing has worked so far, try arXiv one more time by title — the paper may
    # have a preprint twin even though every other source missed it. This runs even
    # if Step 2.5 already ran (arXiv may have been transiently unavailable then).
    if (not result.get("abstract")
            and not result.get("pdf_url")
            and not result.get("url")
            and title):
        arxiv = search_arxiv(title, authors=ref.get("authors"))
        if arxiv:
            ax_year = _arxiv_year(arxiv.get("arxiv_id"))
            if not _years_compatible(year, ax_year):
                logger.info("[%s] Rejecting arXiv preprint — year mismatch: bib=%s arxiv=%s id=%s",
                            bib_key, year, ax_year, arxiv.get("arxiv_id"))
                _log_step(bib_key, "arXiv preprint", False,
                          f"rejected year mismatch (bib={year}, arxiv={ax_year})")
            else:
                if "arxiv_preprint" not in result["sources"]:
                    result["sources"].append("arxiv_preprint")
                if arxiv.get("pdf_url"):
                    result["pdf_url"] = arxiv["pdf_url"]
                if arxiv.get("abstract"):
                    result["abstract"] = arxiv["abstract"]
                if arxiv.get("url") and not result.get("url"):
                    result["url"] = arxiv["url"]
                _log_step(bib_key, "arXiv preprint", True,
                          f"id={arxiv.get('arxiv_id')} pdf={arxiv.get('pdf_url')}")
        else:
            _log_step(bib_key, "arXiv preprint", False)

    # metadata_only mode: the bib entry's own URL was successfully downloaded,
    # so it is the authoritative content source. Restore URL fields, clear the
    # abstract, and restore identity fields (title/authors/year/journal) from the
    # bib — APIs occasionally return a different paper for fuzzy title matches
    # (e.g. "Reinforcement Learning for Trade Execution with Market Impact"
    # matched the older Nevmyvaka-Feng-Kearns "...for Optimized Trade Execution"
    # paper on OpenAlex, replacing Cheridito-Weiss with the wrong authors).
    # API-derived enrichment kept: citation_count, DOI (when bib lacked one), sources.
    if metadata_only:
        bib_url = ref.get("url")
        # Apply the same landing-page → direct-content normalization that
        # pre_download_bib_url uses, so result.pdf_url points at the actual
        # downloaded file (arxiv.org/abs/X became arxiv.org/pdf/X on download).
        from file_downloader import _normalize_bib_url
        normalized = _normalize_bib_url(bib_url) if bib_url else bib_url
        is_bib_pdf = normalized and (
            normalized.lower().endswith(".pdf") or "/pdf/" in normalized.lower()
        )
        if is_bib_pdf:
            result["pdf_url"] = normalized
            result["url"] = bib_url  # keep human-readable original (abs page) for the UI
        else:
            result["url"] = bib_url
            result["pdf_url"] = None
        result["abstract"] = None
        # Restore identity fields from the bib (canonical when bib URL works).
        if title:
            result["title"] = title
        bib_authors = ref.get("authors")
        if bib_authors:
            result["authors"] = (bib_authors if isinstance(bib_authors, list)
                                 else [bib_authors])
        if year:
            result["year"] = year
        if ref.get("journal"):
            result["journal"] = ref.get("journal")
        # Tag the reference as sourced from the bib entry's URL — put "URL" FIRST
        # in sources so the UI surfaces it prominently as the primary tag.
        result["sources"] = ["URL"] + [s for s in result["sources"] if s != "URL"]
        logger.info("[%s] metadata_only: tagged as URL (%s), restored bib identity fields, cleared abstract",
                    bib_key, "PDF" if is_bib_pdf else "HTML")

    # Determine final status
    if result["pdf_url"]:
        result["status"] = "found_pdf"
    elif result["abstract"]:
        result["status"] = "found_abstract"
    elif result["url"]:
        result["status"] = "found_web_page"
    else:
        result["status"] = "not_found"

    logger.info("[%s] DONE status=%s sources=%s", bib_key, result["status"], result["sources"])
    return result


def _reset_api_blocks():
    """Reset blocked/disabled flags so each upload starts fresh."""
    _s2_module._blocked = False
    _s2_module._DELAY = 1.05 if SEMANTIC_SCHOLAR_API_KEY else 3.5
    _gs_module._disabled = False
    _sch_module._disabled = False
    _sch_module._consecutive_failures = 0
    logger.info("API block flags reset for new session")


def process_all(refs, callback=None, max_workers=None, process_fn=None):
    _reset_api_blocks()
    workers = max_workers or MAX_WORKERS
    fn = process_fn or process_reference
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {
            executor.submit(fn, ref): i
            for i, ref in enumerate(refs)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "bib_key": refs[idx]["bib_key"],
                    "title": refs[idx].get("title"),
                    "authors": [],
                    "year": None,
                    "journal": None,
                    "doi": None,
                    "abstract": None,
                    "pdf_url": None,
                    "url": None,
                    "citation_count": None,
                    "sources": [],
                    "status": "not_found",
                    "error": str(e),
                }
            results.append(result)
            if callback:
                callback(idx, result)

    return results
