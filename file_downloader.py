import os
import re
import logging
import threading
import requests
from bs4 import BeautifulSoup

from config import get_pdf_converter, get_pdf_converter_pair

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None

logger = logging.getLogger(__name__)

_docling_converter = None
_docling_lock = threading.Lock()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _headers_for(url):
    """Return request headers with per-site rules applied.

    Rules come from two sources, merged in precedence order:
      1. Built-in rules in download_rules.BUILTIN_RULES (ships with the app)
      2. User overrides in settings.json → "download"."site_rules"
    """
    from download_rules import resolve_headers
    from config import UNPAYWALL_EMAIL, get_settings
    user_rules = (get_settings().get("download") or {}).get("site_rules") or {}
    return resolve_headers(url, _HEADERS, user_rules=user_rules,
                           contact_email=UNPAYWALL_EMAIL or "")

MAX_PDF_SIZE = 50 * 1024 * 1024   # 50MB
MAX_PAGE_SIZE = 5 * 1024 * 1024   # 5MB


def _safe_filename(bib_key):
    safe = re.sub(r'[<>:"/\\|?*]', '_', bib_key).strip('. ')
    return safe[:80]


def download_reference_files(project_dir, bib_key, result, force=False):
    """Download available artifacts for a reference. Returns dict of saved filenames."""
    safe_key = _safe_filename(bib_key)
    files = {}

    # Delete existing files on force refresh
    if force:
        for suffix in ("_pdf.pdf", "_abstract.txt", "_page.html", "_pasted.md", ".md"):
            path = os.path.join(project_dir, safe_key + suffix)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # Download PDF
    pdf_url = result.get("pdf_url")
    if pdf_url:
        filename = safe_key + "_pdf.pdf"
        path = os.path.join(project_dir, filename)
        if force or not os.path.exists(path):
            if _download_pdf(pdf_url, path):
                files["pdf"] = filename
        elif os.path.exists(path):
            files["pdf"] = filename

    # Save abstract
    abstract = result.get("abstract")
    if abstract:
        filename = safe_key + "_abstract.txt"
        path = os.path.join(project_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(abstract)
            files["abstract"] = filename
        except OSError as e:
            logger.debug("Failed to save abstract for %s: %s", bib_key, e)

    # Download web page
    url = result.get("url")
    if url:
        filename = safe_key + "_page.html"
        path = os.path.join(project_dir, filename)
        if force or not os.path.exists(path):
            if _download_page(url, path):
                files["page"] = filename
        elif os.path.exists(path):
            files["page"] = filename

    # Build consolidated .md for reference checking
    # (HTML content becomes the .md body, not the abstract — see _build_reference_md.)
    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename

    return files


_ARXIV_ABS_RE = re.compile(r"https?://arxiv\.org/abs/([\w./-]+?)(?:v\d+)?/?$", re.IGNORECASE)
_ARXIV_HTML_RE = re.compile(r"https?://arxiv\.org/html/([\w./-]+?)(?:v\d+)?/?$", re.IGNORECASE)


def _normalize_bib_url(url):
    """Rewrite known landing-page URLs to their direct-content variants.

    - arxiv.org/abs/<id>   →  arxiv.org/pdf/<id>  (abstract landing page → PDF)
    - arxiv.org/html/<id>  →  arxiv.org/pdf/<id>  (HTML rendition → PDF, more reliable
                                                   for downstream extraction)
    """
    if not url:
        return url
    m = _ARXIV_ABS_RE.match(url) or _ARXIV_HTML_RE.match(url)
    if m:
        arxiv_id = m.group(1)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        logger.debug("Normalizing arXiv landing URL %s -> %s", url, pdf_url)
        return pdf_url
    return url


def pre_download_bib_url(project_dir, bib_key, url):
    """Try to download content from a bib entry's URL before running the lookup pipeline.

    Returns:
        - {} when no URL was provided.
        - {"pdf": filename} or {"page": filename} on successful download.
        - {"error": True, "http_status": int|None, "kind": str, "url": str} on failure.
          `kind` is one of: "http_4xx", "http_5xx", "network", "validation", "unknown".
          A bib URL that fails this way means the citation is unreachable — callers
          should mark the reference as broken rather than fall through to a generic
          title-based search (which would find unrelated papers).

    arXiv abstract URLs (arxiv.org/abs/...) are auto-rewritten to the PDF URL
    so we download the actual paper instead of the metadata page.
    """
    if not url:
        return {}
    url = _normalize_bib_url(url)
    safe_key = _safe_filename(bib_key)
    is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()

    status = {}
    if is_pdf:
        filename = safe_key + "_pdf.pdf"
        path = os.path.join(project_dir, filename)
        if _download_pdf(url, path, status_out=status):
            logger.info("[%s] Pre-downloaded PDF from bib URL: %s", bib_key, url)
            return {"pdf": filename}
    else:
        filename = safe_key + "_page.html"
        path = os.path.join(project_dir, filename)
        if _download_page(url, path, status_out=status):
            logger.info("[%s] Pre-downloaded HTML from bib URL: %s", bib_key, url)
            return {"page": filename}

    logger.info("[%s] Bib URL unreachable: %s status=%s kind=%s",
                bib_key, url, status.get("http_status"), status.get("kind"))
    return {
        "error": True,
        "http_status": status.get("http_status"),
        "kind": status.get("kind") or "unknown",
        "url": url,
    }


def replace_reference_source(project_dir, bib_key, result, new_url):
    """Replace this reference's content source with `new_url` and refresh derived files.

    Behavior:
    - HTML link: deletes any existing PDF, downloads HTML, extracts an abstract from it,
      then rebuilds the .md.
    - PDF link: deletes any existing HTML page, downloads the PDF, then rebuilds the .md.

    Returns dict: {"is_pdf": bool, "downloaded": bool, "files": <updated files dict>}.
    Mutates `result` in place: clears the opposing url field, sets the new one,
    updates abstract if extracted from new HTML.
    """
    safe_key = _safe_filename(bib_key)
    files = dict(result.get("files") or {})
    is_pdf = new_url.lower().endswith(".pdf") or "/pdf/" in new_url.lower()

    # Helper: drop a file from disk + dict
    def _drop(file_key):
        fname = files.pop(file_key, None)
        if fname:
            path = os.path.join(project_dir, fname)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    logger.debug("Removed stale %s for %s: %s", file_key, bib_key, fname)
                except OSError as e:
                    logger.debug("Could not remove %s: %s", path, e)

    downloaded = False

    if is_pdf:
        # Clear opposing HTML page (the new source is a PDF)
        _drop("page")
        result["url"] = None
        result["pdf_url"] = new_url
        pdf_filename = safe_key + "_pdf.pdf"
        if _download_pdf(new_url, os.path.join(project_dir, pdf_filename)):
            files["pdf"] = pdf_filename
            downloaded = True
        # Note: don't touch existing abstract (it may have come from S2/Crossref).
    else:
        # Clear opposing PDF (the new source is an HTML page)
        _drop("pdf")
        # Invalidate the abstract: it was tied to the OLD source and is now stale.
        # The HTML content itself becomes the .md body (via _build_reference_md), not an
        # abstract. Abstract stays None unless something else (S2/Crossref) provides one.
        _drop("abstract")
        result["abstract"] = None
        result["pdf_url"] = None
        result["url"] = new_url
        page_filename = safe_key + "_page.html"
        page_path = os.path.join(project_dir, page_filename)
        if _download_page(new_url, page_path):
            files["page"] = page_filename
            downloaded = True

    # Drop stale .md (will be rebuilt below)
    _drop("md")

    # Rebuild the consolidated .md from current state
    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename

    result["files"] = files
    return {"is_pdf": is_pdf, "downloaded": downloaded, "files": files}


def set_uploaded_pdf(project_dir, bib_key, result, pdf_bytes):
    """Save user-uploaded PDF bytes as the new source. Mirrors replace_reference_source.

    Validates the magic bytes ('%PDF'). Drops opposing HTML page + pasted content
    + abstract. Rebuilds {key}.md from the PDF.
    Returns dict {ok, files, reason?}.
    """
    if not pdf_bytes or not pdf_bytes[:5].startswith(b"%PDF"):
        return {"ok": False, "files": result.get("files") or {},
                "reason": "Uploaded file is not a valid PDF (missing %PDF header)"}
    if len(pdf_bytes) > MAX_PDF_SIZE:
        return {"ok": False, "files": result.get("files") or {},
                "reason": f"PDF exceeds maximum size of {MAX_PDF_SIZE // (1024*1024)}MB"}

    safe_key = _safe_filename(bib_key)
    files = dict(result.get("files") or {})

    def _drop(file_key):
        fname = files.pop(file_key, None)
        if fname:
            path = os.path.join(project_dir, fname)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # New source is a PDF — drop everything else
    _drop("page")
    _drop("pasted")
    _drop("abstract")
    _drop("md")
    result["url"] = None
    result["pdf_url"] = None  # local upload, not a URL
    result["abstract"] = None

    pdf_filename = safe_key + "_pdf.pdf"
    pdf_path = os.path.join(project_dir, pdf_filename)
    try:
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
    except OSError as e:
        return {"ok": False, "files": files, "reason": f"Failed to save PDF: {e}"}
    files["pdf"] = pdf_filename

    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename
    result["files"] = files
    return {"ok": True, "files": files}


def set_pasted_content(project_dir, bib_key, result, content):
    """Save user-pasted content as the new source.

    Stored verbatim as {key}_pasted.md and used as the body of {key}.md by
    _build_reference_md. Drops opposing PDF + HTML page + abstract.
    Returns dict {ok, files, reason?}.
    """
    if not content or not content.strip():
        return {"ok": False, "files": result.get("files") or {},
                "reason": "Pasted content is empty"}

    safe_key = _safe_filename(bib_key)
    files = dict(result.get("files") or {})

    def _drop(file_key):
        fname = files.pop(file_key, None)
        if fname:
            path = os.path.join(project_dir, fname)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # Replace all derived sources — user-pasted content is the new source of truth
    _drop("pdf")
    _drop("page")
    _drop("abstract")
    _drop("md")
    result["pdf_url"] = None
    result["url"] = None
    result["abstract"] = None

    pasted_filename = safe_key + "_pasted.md"
    pasted_path = os.path.join(project_dir, pasted_filename)
    try:
        with open(pasted_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return {"ok": False, "files": files, "reason": f"Failed to save pasted content: {e}"}
    files["pasted"] = pasted_filename

    # Also write a viewable HTML wrapper so the right-panel HTML tab can render it.
    page_filename = safe_key + "_page.html"
    page_path = os.path.join(project_dir, page_filename)
    try:
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(_pasted_to_html(content, bib_key))
        files["page"] = page_filename
    except OSError as e:
        logger.debug("Failed to write pasted HTML wrapper for %s: %s", bib_key, e)

    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename
    result["files"] = files
    return {"ok": True, "files": files}


def _pasted_to_html(content, bib_key):
    """Wrap pasted text in a styled HTML shell so it renders nicely in the iframe.

    If the pasted content already looks like a full HTML document, pass it through
    unchanged (the user clearly intended HTML). Otherwise wrap it in <pre> with
    basic typography.
    """
    head = (content or "").lstrip()[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return content

    import html
    escaped = html.escape(content)
    # bib_key shown in the title for context when the iframe is opened standalone
    title = html.escape(str(bib_key) or "Pasted content")
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        f"  <meta charset=\"utf-8\">\n"
        f"  <title>Pasted content — {title}</title>\n"
        "  <style>\n"
        "    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;\n"
        "           max-width: 820px; margin: 1.5em auto; padding: 0 1em; line-height: 1.55;\n"
        "           color: #222; background: #fff; }\n"
        "    .meta { color: #888; font-size: 0.78rem; padding: 0.4em 0.6em;\n"
        "            background: #f5f5fb; border-radius: 4px; margin-bottom: 1em; }\n"
        "    pre { white-space: pre-wrap; word-wrap: break-word;\n"
        "          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;\n"
        "          font-size: 0.92em; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"  <div class=\"meta\">Pasted content for <code>{title}</code></div>\n"
        f"  <pre>{escaped}</pre>\n"
        "</body>\n"
        "</html>\n"
    )


def rebuild_reference_md(project_dir, bib_key, result):
    """Rebuild {key}.md from already-downloaded files (no re-download).

    Reads current files dict from `result`, drops entries whose files no longer exist
    on disk, then re-runs the consolidator. Returns the updated files dict (mutated copy).
    """
    safe_key = _safe_filename(bib_key)
    files = dict(result.get("files") or {})

    # Drop stale file pointers
    for fkey in list(files.keys()):
        path = os.path.join(project_dir, files[fkey])
        if not os.path.exists(path):
            files.pop(fkey, None)

    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename
    else:
        files.pop("md", None)

    return files


def _build_reference_md(project_dir, safe_key, bib_key, result, files):
    """Build a consolidated markdown file for the reference.

    Contains: metadata header + abstract + full PDF body (or HTML-extracted fallback).
    Returns the filename written, or None if there was no content to write.
    """
    body = ""

    # Highest priority: user-pasted content (verbatim, not extracted)
    pasted_file = files.get("pasted")
    if pasted_file:
        pasted_path = os.path.join(project_dir, pasted_file)
        try:
            with open(pasted_path, "r", encoding="utf-8") as f:
                body = f.read()
        except OSError as e:
            logger.debug("Failed to read pasted content for %s: %s", bib_key, e)

    # Next: full text extracted from the PDF
    pdf_file = files.get("pdf")
    if not body and pdf_file:
        pdf_path = os.path.join(project_dir, pdf_file)
        body = extract_pdf_markdown(pdf_path, bib_key=bib_key) or ""

    # Fallback: markdown extracted from the HTML page
    if not body and files.get("page"):
        page_path = os.path.join(project_dir, files["page"])
        try:
            with open(page_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            body = _extract_markdown(html_content) or ""
        except Exception as e:
            logger.debug("HTML markdown fallback failed for %s: %s", bib_key, e)

    abstract = result.get("abstract") or ""

    # Skip if we have nothing useful
    if not body and not abstract:
        return None

    header = _format_md_header(bib_key, result)
    sections = [header]
    if abstract:
        sections.append("## Abstract\n\n" + abstract.strip())
    if body:
        sections.append("## Full text\n\n" + body.strip())

    content = "\n\n".join(sections).rstrip() + "\n"
    filename = safe_key + ".md"
    path = os.path.join(project_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Reference md saved: %s (%d chars)", path, len(content))
        return filename
    except OSError as e:
        logger.debug("Failed to write reference md for %s: %s", bib_key, e)
        return None


def _format_md_header(bib_key, result):
    title = result.get("title") or bib_key
    authors = result.get("authors") or []
    if isinstance(authors, list):
        authors_str = ", ".join(a for a in authors if a)
    else:
        authors_str = str(authors)

    lines = [f"# {title}", ""]
    meta = [
        ("Bib key", bib_key),
        ("Authors", authors_str),
        ("Year", result.get("year")),
        ("Journal", result.get("journal")),
        ("DOI", result.get("doi")),
        ("URL", result.get("url")),
        ("PDF URL", result.get("pdf_url")),
        ("Citation count", result.get("citation_count")),
        ("Sources", ", ".join(result.get("sources") or [])),
        ("Status", result.get("status")),
    ]
    for label, value in meta:
        if value in (None, "", []):
            continue
        lines.append(f"- **{label}:** {value}")
    return "\n".join(lines)


def _http_failure_kind(status_code):
    """Map an HTTP response code to a coarse failure category."""
    if 400 <= status_code < 500:
        return "http_4xx"
    if 500 <= status_code < 600:
        return "http_5xx"
    return "http_other"


def _record_failure(status_out, http_status, kind, detail=None):
    """Helper: write failure info into a caller-supplied dict (no-op when None)."""
    if status_out is None:
        return
    status_out["http_status"] = http_status
    status_out["kind"] = kind
    if detail:
        status_out["detail"] = detail


def _download_pdf(url, path, status_out=None):
    """Download a PDF to `path`. Returns True on success, False otherwise.

    When `status_out` is provided, on failure it is populated with keys
    `http_status`, `kind` ("http_4xx"/"http_5xx"/"network"/"validation"),
    and (optionally) `detail` so callers can distinguish failure modes.
    """
    try:
        resp = requests.get(url, headers=_headers_for(url), timeout=30, stream=True, allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("PDF download failed: url=%s status=%d", url, resp.status_code)
            _record_failure(status_out, resp.status_code, _http_failure_kind(resp.status_code))
            return False

        # Check content length
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_PDF_SIZE:
            logger.debug("PDF too large: url=%s size=%s", url, content_length)
            _record_failure(status_out, 200, "validation", "exceeds_max_size")
            return False

        # Stream and validate first bytes
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PDF_SIZE:
                logger.debug("PDF exceeded size limit during download: url=%s", url)
                _record_failure(status_out, 200, "validation", "exceeds_max_size")
                return False

        content = b"".join(chunks)

        # Validate PDF magic bytes
        if not content[:5].startswith(b"%PDF"):
            logger.debug("PDF validation failed (not a PDF): url=%s first_bytes=%s", url, content[:20])
            _record_failure(status_out, 200, "validation", "not_a_pdf")
            return False

        with open(path, "wb") as f:
            f.write(content)

        logger.debug("PDF saved: %s (%d bytes)", path, len(content))
        return True

    except Exception as e:
        logger.debug("PDF download error: url=%s error=%s", url, e)
        _record_failure(status_out, None, "network", str(e))
        return False


def _extract_markdown(html):
    """Extract main content from HTML and convert to simple markdown.

    Strategy:
    1. Strip junk tags (scripts, ads, nav, etc.).
    2. Walk semantic containers in priority order: <article> → <main> → <body>.
       (We deliberately do NOT use class-name regex matching — it matches things
       like Bootstrap's "justify-content-center" and locks onto empty ad slots.)
    3. Try structured extraction (headings + paragraphs + lists + definition terms).
    4. If structured extraction yields too little, fall back to the container's
       plain visible text — many pages put content in <span>s the structured pass
       skips (e.g. dictionary entries, single-page apps).
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')

        # Remove junk elements
        for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                                   'aside', 'form', 'noscript', 'iframe', 'svg',
                                   'button']):
            tag.decompose()

        # Find a content container — prefer semantic tags only.
        main = soup.find('article') or soup.find('main') or soup.body or soup

        # Pass 1: structured extraction
        lines = []
        for el in main.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                  'p', 'li', 'blockquote', 'pre',
                                  'dt', 'dd', 'td']):
            text = el.get_text(separator=' ', strip=True)
            if not text or len(text) < 3:
                continue
            tag = el.name
            if tag == 'h1':
                lines.append('# ' + text)
            elif tag == 'h2':
                lines.append('## ' + text)
            elif tag == 'h3':
                lines.append('### ' + text)
            elif tag in ('h4', 'h5', 'h6'):
                lines.append('#### ' + text)
            elif tag == 'li':
                lines.append('- ' + text)
            elif tag == 'dt':
                lines.append('**' + text + '**')
            elif tag == 'blockquote':
                lines.append('> ' + text)
            else:
                lines.append(text)
            lines.append('')

        structured = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines).strip())

        # Pass 2 (fallback): plain visible text from the container.
        # Used when the structured pass is suspiciously thin — common on pages
        # whose content lives in <span>s, JS-rendered shells with minimal tags, etc.
        if len(structured) < 200:
            plain_lines = [ln.strip() for ln in main.get_text(separator='\n').splitlines()]
            plain = '\n'.join(ln for ln in plain_lines if ln)
            plain = re.sub(r'\n{3,}', '\n\n', plain)
            if len(plain) > len(structured):
                result = plain
            else:
                result = structured
        else:
            result = structured

        return result if len(result) > 50 else None
    except Exception as e:
        logger.debug("Markdown extraction failed: %s", e)
        return None


def _download_page(url, path, status_out=None):
    """Download an HTML page to `path`. Returns True on success, False otherwise.

    See `_download_pdf` for `status_out` semantics.
    """
    try:
        resp = requests.get(url, headers=_headers_for(url), timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Page download failed: url=%s status=%d", url, resp.status_code)
            _record_failure(status_out, resp.status_code, _http_failure_kind(resp.status_code))
            return False

        content = resp.text
        if len(content.encode("utf-8")) > MAX_PAGE_SIZE:
            content = content[:MAX_PAGE_SIZE]

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.debug("Page saved: %s (%d chars)", path, len(content))
        return True

    except Exception as e:
        logger.debug("Page download error: url=%s error=%s", url, e)
        _record_failure(status_out, None, "network", str(e))
        return False


def _run_backend(backend, pdf_path, bib_key):
    """Dispatch a backend name to its extractor. Never raises; returns a string."""
    if backend == "docling":
        return _extract_with_docling(pdf_path, bib_key)
    if backend == "pymupdf4llm":
        return _extract_with_pymupdf4llm(pdf_path, bib_key)
    if backend == "pymupdf_text":
        return _extract_with_pymupdf_text(pdf_path, bib_key)
    logger.warning("Unknown PDF converter %r, using pymupdf_text", backend)
    return _extract_with_pymupdf_text(pdf_path, bib_key)


def _pdf_page_count(pdf_path):
    """Return the page count of a PDF, or None if the file can't be opened.
    pymupdf.open().page_count is O(1) — reads only the XRef table."""
    try:
        import pymupdf
        with pymupdf.open(pdf_path) as doc:
            return doc.page_count
    except Exception as e:
        logger.debug("Could not read page count for %s: %s", pdf_path, e)
        return None


def extract_pdf_markdown(pdf_path, bib_key=None):
    """Extract a PDF into text, choosing backend by page count.

    Small PDFs (<= pdf_quality_page_limit pages) use pdf_converter_high_quality
    — layout-aware, slower, but better structure for short focused papers.
    Large PDFs use pdf_converter_fast — raw text, linear scaling, no OOM on books.

    If the chosen backend fails or returns empty, falls back to the other one,
    and finally to raw pymupdf_text.
    Returns a string (possibly empty) — never raises.
    """
    fast, hq, page_limit = get_pdf_converter_pair()

    pages = _pdf_page_count(pdf_path)
    if pages is None:
        # Can't introspect — be conservative and use the fast backend
        logger.info("[%s] could not count pages, using fast backend (%s)", bib_key, fast)
        chosen = fast
    elif pages <= page_limit:
        chosen = hq
        logger.info("[%s] %d pages <= %d, using high-quality backend (%s)", bib_key, pages, page_limit, hq)
    else:
        chosen = fast
        logger.info("[%s] %d pages > %d, using fast backend (%s)", bib_key, pages, page_limit, fast)

    text = _run_backend(chosen, pdf_path, bib_key)
    if text:
        return text

    # Chosen backend failed — try the other one, then raw pymupdf_text as last resort
    other = fast if chosen == hq else hq
    if other != chosen:
        logger.info("[%s] %s failed/empty, trying %s", bib_key, chosen, other)
        text = _run_backend(other, pdf_path, bib_key)
        if text:
            return text
    if chosen != "pymupdf_text" and other != "pymupdf_text":
        logger.info("[%s] both configured backends failed, falling back to pymupdf_text", bib_key)
        return _extract_with_pymupdf_text(pdf_path, bib_key)
    return ""


def _extract_with_pymupdf_text(pdf_path, bib_key=None):
    """Fastest backend: raw pymupdf.get_text() per page, concatenated.

    No layout analysis → ~40 MB/s. Handles very large PDFs without memory issues.
    """
    try:
        import pymupdf
    except ImportError:
        logger.debug("pymupdf not installed; skipping PDF extraction for %s", bib_key)
        return ""
    try:
        doc = pymupdf.open(pdf_path)
        parts = []
        for page in doc:
            parts.append(page.get_text())
        doc.close()
        return "\n".join(parts)
    except Exception as e:
        logger.debug("pymupdf.get_text extraction failed for %s: %s", bib_key, e)
        return ""


def _extract_with_pymupdf4llm(pdf_path, bib_key=None):
    if pymupdf4llm is None:
        logger.debug("pymupdf4llm not installed; skipping PDF extraction for %s", bib_key)
        return ""
    try:
        return pymupdf4llm.to_markdown(pdf_path) or ""
    except Exception as e:
        logger.debug("pymupdf4llm extraction failed for %s: %s", bib_key, e)
        return ""


def _get_docling_converter():
    """Lazy-init a singleton Docling DocumentConverter.

    Docling loads layout/table models on first construction (slow + network download),
    so we build one per process and reuse it across references.
    """
    global _docling_converter
    if _docling_converter is not None:
        return _docling_converter
    with _docling_lock:
        if _docling_converter is not None:
            return _docling_converter
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            logger.debug("docling not installed")
            return None
        try:
            _docling_converter = DocumentConverter()
        except Exception as e:
            logger.warning("Failed to initialize Docling converter: %s", e)
            return None
        return _docling_converter


def _extract_with_docling(pdf_path, bib_key=None):
    converter = _get_docling_converter()
    if converter is None:
        return ""
    try:
        result = converter.convert(pdf_path)
        return result.document.export_to_markdown() or ""
    except Exception as e:
        logger.debug("docling extraction failed for %s: %s", bib_key, e)
        return ""
