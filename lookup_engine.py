from concurrent.futures import ThreadPoolExecutor, as_completed
from api_clients.crossref import lookup_crossref
from api_clients.unpaywall import lookup_unpaywall
from api_clients.semantic_scholar import lookup_semantic_scholar
from api_clients.scholarly_client import lookup_scholarly
from config import MAX_WORKERS


def process_reference(ref):
    if ref.get("status") == "insufficient_data":
        return {
            "bib_key": ref["bib_key"],
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

    result = {
        "bib_key": ref["bib_key"],
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

    # Step 1: CrossRef + Unpaywall (if DOI available)
    if doi:
        cr = lookup_crossref(doi)
        if cr:
            result["sources"].append("crossref")
            result["title"] = result["title"] or cr.get("title")
            result["authors"] = cr.get("authors") or result["authors"]
            result["journal"] = result["journal"] or cr.get("journal")
            result["year"] = result["year"] or cr.get("year")
            result["url"] = result["url"] or cr.get("url")

        uw = lookup_unpaywall(doi)
        if uw:
            result["sources"].append("unpaywall")
            if uw.get("pdf_url"):
                result["pdf_url"] = uw["pdf_url"]

    # Step 2: Semantic Scholar
    s2 = lookup_semantic_scholar(doi=doi, title=title, year=year, authors_hint=authors)
    if s2:
        result["sources"].append("semantic_scholar")
        result["abstract"] = result["abstract"] or s2.get("abstract")
        result["citation_count"] = s2.get("citation_count")
        result["doi"] = result["doi"] or s2.get("doi")
        if not result["pdf_url"] and s2.get("pdf_url"):
            result["pdf_url"] = s2["pdf_url"]
        # If we got a DOI from S2 and didn't have one, try Unpaywall now
        if result["doi"] and not doi and not result["pdf_url"]:
            uw = lookup_unpaywall(result["doi"])
            if uw and uw.get("pdf_url"):
                result["pdf_url"] = uw["pdf_url"]
                if "unpaywall" not in result["sources"]:
                    result["sources"].append("unpaywall")

    # Step 3: Google Scholar fallback (when no abstract AND no PDF found yet)
    if not result.get("abstract") and not result.get("pdf_url") and title:
        sch = lookup_scholarly(title)
        if sch:
            result["sources"].append("scholarly")
            result["abstract"] = result["abstract"] or sch.get("abstract")
            if not result["pdf_url"] and sch.get("pdf_url"):
                result["pdf_url"] = sch["pdf_url"]
            if not result["url"] and sch.get("url"):
                result["url"] = sch["url"]

    # Determine final status
    if result["pdf_url"]:
        result["status"] = "found_pdf"
    elif result["abstract"]:
        result["status"] = "found_abstract"
    else:
        result["status"] = "not_found"

    return result


def process_all(refs, callback=None, max_workers=None):
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
