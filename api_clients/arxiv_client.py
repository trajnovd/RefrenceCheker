import re
import logging
import requests
from http_client import get_session
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"


def _normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def search_arxiv(title, max_results=3, timeout=10):
    """Search arXiv by title. Returns dict with pdf_url, abstract, arxiv_id or None."""
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

        for entry in root.findall("atom:entry", ns):
            entry_title = entry.findtext("atom:title", default="", namespaces=ns).strip()
            entry_title = re.sub(r"\s+", " ", entry_title)

            # Check title match
            norm_result = _normalize(entry_title)
            if norm_query != norm_result:
                # Allow substring match
                if norm_query not in norm_result and norm_result not in norm_query:
                    # Check word overlap
                    q_words = set(norm_query.split())
                    r_words = set(norm_result.split())
                    if not q_words or not r_words:
                        continue
                    overlap = len(q_words & r_words) / max(len(q_words), len(r_words))
                    if overlap < 0.7:
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
