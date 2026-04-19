import re
import logging
import requests
from http_client import get_session
from config import OPENALEX_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
_PARAMS = {"api_key": OPENALEX_API_KEY} if OPENALEX_API_KEY else {}

logger.info("OpenAlex client initialized: api_key=%s", "present" if OPENALEX_API_KEY else "MISSING")


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _reconstruct_abstract(inverted_index):
    """Reconstruct abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    try:
        words = []
        for word, positions in inverted_index.items():
            for pos in positions:
                words.append((pos, word))
        words.sort(key=lambda x: x[0])
        return " ".join(w for _, w in words)
    except Exception:
        return None


def _parse_work(data):
    """Parse an OpenAlex work object into our standard format."""
    if not data:
        return None

    title = data.get("display_name") or data.get("title")
    year = data.get("publication_year")
    cited_by = data.get("cited_by_count")
    doi = data.get("doi")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    # Authors
    authors = []
    for authorship in (data.get("authorships") or []):
        author = authorship.get("author", {})
        name = author.get("display_name")
        if name:
            authors.append(name)

    # Abstract
    abstract = _reconstruct_abstract(data.get("abstract_inverted_index"))

    # PDF URL — primary / best_oa / oa_url fallback chain
    pdf_url = None
    primary = data.get("primary_location") or {}
    if primary.get("pdf_url"):
        pdf_url = primary["pdf_url"]
    if not pdf_url:
        best_oa = data.get("best_oa_location") or {}
        if best_oa.get("pdf_url"):
            pdf_url = best_oa["pdf_url"]
    if not pdf_url:
        oa = data.get("open_access") or {}
        if oa.get("oa_url"):
            pdf_url = oa["oa_url"]

    # v6.1 §3.2: surface ALL `locations[].pdf_url` as an ordered fallback
    # list. Orchestrator walks these when the primary 403s.
    pdf_url_fallbacks = []
    for loc in (data.get("locations") or []):
        u = (loc.get("pdf_url") if isinstance(loc, dict) else None)
        if u and u not in pdf_url_fallbacks:
            pdf_url_fallbacks.append(u)

    return {
        "title": title,
        "abstract": abstract,
        "year": str(year) if year else None,
        "citation_count": cited_by,
        "pdf_url": pdf_url,
        "pdf_url_fallbacks": pdf_url_fallbacks,
        "authors": authors,
        "doi": doi,
    }


def _title_matches(query, result_title):
    """Check if titles match reasonably."""
    nq = _normalize(query)
    nr = _normalize(result_title)
    if nq == nr:
        return True
    if nq in nr or nr in nq:
        return True
    q_words = set(nq.split())
    r_words = set(nr.split())
    if q_words and r_words:
        overlap = len(q_words & r_words) / max(len(q_words), len(r_words))
        if overlap > 0.6:
            return True
    return False


def lookup_openalex(doi=None, title=None, year=None, timeout=10):
    """Search OpenAlex by DOI or title. Returns parsed work dict or None."""
    logger.debug("OpenAlex query: doi=%s title=%s year=%s", doi, title, year)

    # Try DOI lookup first (exact, fast)
    if doi:
        result = _lookup_by_doi(doi, timeout)
        if result:
            logger.debug("OpenAlex DOI hit: doi=%s", doi)
            return result

    # Fall back to title search
    if title:
        result = _search_by_title(title, year, timeout)
        if result:
            logger.debug("OpenAlex title hit: title=%s", title[:80])
            return result

    logger.debug("OpenAlex no result: doi=%s title=%s", doi, title)
    return None


def _lookup_by_doi(doi, timeout):
    try:
        url = f"{BASE_URL}/works/doi:{doi}"
        params = dict(_PARAMS)
        params["select"] = "id,doi,title,display_name,publication_year,cited_by_count,authorships,abstract_inverted_index,primary_location,best_oa_location,locations,open_access"
        resp = get_session().get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            return _parse_work(resp.json())
        logger.debug("OpenAlex DOI miss: doi=%s status=%d", doi, resp.status_code)
        return None
    except Exception as e:
        logger.debug("OpenAlex DOI error: doi=%s error=%s", doi, e)
        return None


def _search_by_title(title, year, timeout):
    try:
        url = f"{BASE_URL}/works"
        params = dict(_PARAMS)
        params["search"] = title
        params["per_page"] = 5
        params["select"] = "id,doi,title,display_name,publication_year,cited_by_count,authorships,abstract_inverted_index,primary_location,best_oa_location,locations,open_access"
        resp = get_session().get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("OpenAlex search error: status=%d", resp.status_code)
            return None

        results = resp.json().get("results", [])
        if not results:
            return None

        # Find best match
        for work in results:
            work_title = work.get("display_name") or work.get("title") or ""
            if _title_matches(title, work_title):
                parsed = _parse_work(work)
                if parsed:
                    # Prefer results matching year if provided
                    if year and parsed.get("year") and parsed["year"] != str(year):
                        continue
                    return parsed

        # If no year-matched result, return first title match
        for work in results:
            work_title = work.get("display_name") or work.get("title") or ""
            if _title_matches(title, work_title):
                return _parse_work(work)

        return None
    except Exception as e:
        logger.debug("OpenAlex search error: title=%s error=%s", title[:80], e)
        return None
