import re
import logging
import requests
from http_client import get_session
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _last_names(authors):
    """Extract a set of normalized last-name tokens from various author formats.

    Handles:
      - list of strings (["Baddeley, Alan", "Hitch, Graham J."])
      - "A and B and C" string (BibTeX style)
      - "Last, First" or "First Last" each
      - empty/None → empty set
    """
    if not authors:
        return set()
    items = authors if isinstance(authors, list) else None
    if items is None:
        s = str(authors).strip()
        # Split on " and " (BibTeX) or ";"
        for sep in (" and ", ";"):
            if sep in s:
                items = [p.strip() for p in s.split(sep)]
                break
        else:
            items = [s]
    last_names = set()
    for item in items:
        if not item:
            continue
        name = str(item).strip().strip("{}").strip()
        if not name:
            continue
        # "Last, First" → "Last"
        if "," in name:
            last = name.split(",", 1)[0].strip()
        else:
            # "First M. Last" → last word
            parts = name.split()
            last = parts[-1] if parts else ""
        last = _normalize(last)
        if last and len(last) >= 2:  # 1-letter "tokens" are noise
            last_names.add(last)
    return last_names


def search_arxiv(title, authors=None, max_results=3, timeout=10):
    """Search arXiv by title. Returns dict with pdf_url, abstract, arxiv_id or None.

    When `authors` is provided (bib author field — string or list), an arXiv
    hit must share at least one author last-name with the bib OR have a very
    high title similarity. Without this, generic 2-word titles like "Working
    memory" (Baddeley) match unrelated papers like "Working Memory Graphs"
    (Loynd et al., 1911.07141) by substring and the wrong PDF gets downloaded.
    """
    if not title:
        return None

    try:
        params = {
            "search_query": f'ti:"{title}"',
            "max_results": max_results,
            "sortBy": "relevance",
        }
        resp = get_session().get(ARXIV_API, params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("arXiv API error: status=%d", resp.status_code)
            return None

        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        norm_query = _normalize(title)
        bib_lastnames = _last_names(authors)

        for entry in root.findall("atom:entry", ns):
            entry_title = entry.findtext("atom:title", default="", namespaces=ns).strip()
            entry_title = re.sub(r"\s+", " ", entry_title)

            # Title-match strength classification:
            #   exact_match   → titles equal after normalization
            #   strong_match  → ≥0.85 word overlap (rare extra word ok)
            #   weak_match    → query is substring of longer result (or ≥0.7 overlap)
            #   no_match      → reject
            norm_result = _normalize(entry_title)
            q_words = set(norm_query.split())
            r_words = set(norm_result.split())
            exact_match = norm_query == norm_result
            strong_match = False
            weak_match = False
            if not exact_match:
                if q_words and r_words:
                    overlap = len(q_words & r_words) / max(len(q_words), len(r_words))
                    strong_match = overlap >= 0.85
                    weak_match = (overlap >= 0.7
                                  or norm_query in norm_result
                                  or norm_result in norm_query)
                if not (strong_match or weak_match):
                    continue
            # Author guard: weak title matches REQUIRE author overlap when bib
            # provides authors. Exact / strong matches are allowed without
            # (different author orderings, single-author shortcuts).
            if bib_lastnames and not exact_match and not strong_match:
                entry_lastnames = _result_lastnames(entry, ns)
                if entry_lastnames and not (bib_lastnames & entry_lastnames):
                    logger.debug(
                        "arXiv rejecting weak title match — author mismatch: "
                        "bib=%s arxiv_authors=%s title=%r",
                        sorted(bib_lastnames), sorted(entry_lastnames), entry_title[:80])
                    continue

            # Extract arXiv ID from entry id URL
            entry_id = entry.findtext("atom:id", default="", namespaces=ns)
            arxiv_id_match = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", entry_id)
            arxiv_id = arxiv_id_match.group(1) if arxiv_id_match else None

            abstract = entry.findtext("atom:summary", default="", namespaces=ns).strip()
            abstract = re.sub(r"\s+", " ", abstract)

            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf" or (link.get("type") == "application/pdf"):
                    pdf_url = link.get("href")
                    break
            if not pdf_url and arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

            logger.debug("arXiv found: title=%s arxiv_id=%s", entry_title[:80], arxiv_id)
            return {
                "arxiv_id": arxiv_id,
                "title": entry_title,
                "abstract": abstract or None,
                "pdf_url": pdf_url,
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
            }

        logger.debug("arXiv no match for: %s", title[:80])
        return None

    except Exception as e:
        logger.debug("arXiv search error: %s", e)
        return None


def _result_lastnames(entry, ns):
    """Pull last-name tokens from an arXiv atom:entry's authors."""
    out = set()
    for author in entry.findall("atom:author", ns):
        name = author.findtext("atom:name", default="", namespaces=ns).strip()
        if not name:
            continue
        # arXiv authors are "First M. Last" — last word is the surname
        parts = name.split()
        if not parts:
            continue
        last = _normalize(parts[-1])
        if last and len(last) >= 2:
            out.add(last)
    return out
