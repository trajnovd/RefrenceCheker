import logging
import requests

logger = logging.getLogger(__name__)

API_URL = "https://en.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "ReferencesChecker/1.0 (academic reference lookup tool)"}


def lookup_wikipedia(title, authors=None, timeout=10):
    if not title:
        return None

    # Build search query: title + first author surname for better matching
    query = title
    if authors:
        first = authors.split(",")[0].strip() if isinstance(authors, str) else authors[0] if authors else ""
        if first:
            surname = first.split()[-1] if first else ""
            if surname:
                query = f"{title} {surname}"

    logger.debug("Wikipedia query: q=%s", query)

    # Step 1: Search for the page
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 5,
        "format": "json",
    }

    try:
        resp = requests.get(API_URL, params=search_params, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Wikipedia search failed: status=%d body=%s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            logger.debug("Wikipedia no results: title=%s", title)
            return None

        # Pick the best match
        page_id = None
        page_title = None
        title_lower = title.lower()
        for r in results:
            rt = r.get("title", "").lower()
            # Prefer exact or substring match
            if title_lower in rt or rt in title_lower:
                page_id = r["pageid"]
                page_title = r["title"]
                break

        # Fall back to first result
        if not page_id:
            page_id = results[0]["pageid"]
            page_title = results[0]["title"]

        logger.debug("Wikipedia matched page: id=%d title=%s", page_id, page_title)

    except Exception as e:
        logger.debug("Wikipedia search error: title=%s error=%s", title, e)
        return None

    # Step 2: Get the extract (summary) and page URL
    try:
        extract_params = {
            "action": "query",
            "pageids": page_id,
            "prop": "extracts|info",
            "exintro": True,
            "explaintext": True,
            "exsectionformat": "plain",
            "inprop": "url",
            "format": "json",
        }

        resp = requests.get(API_URL, params=extract_params, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Wikipedia extract failed: page_id=%d status=%d", page_id, resp.status_code)
            return None

        pages = resp.json().get("query", {}).get("pages", {})
        page = pages.get(str(page_id), {})

        extract = page.get("extract", "").strip()
        url = page.get("fullurl", f"https://en.wikipedia.org/?curid={page_id}")

        if not extract:
            logger.debug("Wikipedia empty extract: page_id=%d title=%s", page_id, page_title)
            return None

        # Truncate very long extracts
        if len(extract) > 1500:
            extract = extract[:1500].rsplit(".", 1)[0] + "."

        logger.debug("Wikipedia result: page=%s url=%s extract_len=%d", page_title, url, len(extract))
        return {
            "abstract": extract,
            "url": url,
            "wiki_title": page_title,
        }

    except Exception as e:
        logger.debug("Wikipedia extract error: page_id=%d error=%s", page_id, e)
        return None
