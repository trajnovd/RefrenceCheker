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

logger = logging.getLogger(__name__)


def _log_step(bib_key, source, found, detail=""):
    tag = "OK" if found else "MISS"
    msg = f"[{bib_key}] {source}: {tag}"
    if detail:
        msg += f" — {detail}"
    logger.info(msg)


def process_reference(ref):
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
        }

    title = ref.get("title")
    doi = ref.get("doi")
    authors = ref.get("authors", "")
    year = ref.get("year")
    entry_type = ref.get("entry_type", "")
    is_book = entry_type in BOOK_TYPES

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
        if not result["authors"] or result["authors"] == [authors]:
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

    # Step 2.5: arXiv search (if no PDF found yet, search by title)
    if not result.get("pdf_url") and title and not arxiv_id:
        arxiv = search_arxiv(title)
        if arxiv:
            if arxiv.get("pdf_url"):
                result["pdf_url"] = arxiv["pdf_url"]
            if arxiv.get("abstract") and not result.get("abstract"):
                result["abstract"] = arxiv["abstract"]
            if arxiv.get("url") and not result.get("url"):
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

    # Step 4: Google Scholar fallback (when no abstract AND no PDF found yet)
    if not result.get("abstract") and not result.get("pdf_url") and title:
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

    # Step 5: Google Custom Search (last resort)
    if not result.get("abstract") and not result.get("pdf_url") and title:
        gs = lookup_google_search(title, doi=result.get("doi"))
        if gs:
            result["sources"].append("google_search")
            result["abstract"] = result["abstract"] or gs.get("abstract")
            if not result["pdf_url"] and gs.get("pdf_url"):
                result["pdf_url"] = gs["pdf_url"]
            if not result["url"] and gs.get("url"):
                result["url"] = gs["url"]
        _log_step(bib_key, "GoogleSearch", gs is not None,
                  f"url={gs.get('url')} pdf={gs.get('pdf_url')}" if gs else "")

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


def process_all(refs, callback=None, max_workers=None):
    _reset_api_blocks()
    workers = max_workers or MAX_WORKERS
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {
            executor.submit(process_reference, ref): i
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
