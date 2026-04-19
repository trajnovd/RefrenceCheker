import os
import re
import logging
import threading
import requests
from bs4 import BeautifulSoup

from config import get_pdf_converter, get_pdf_converter_pair
from http_client import get_session

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
    """Download available artifacts for a reference. Returns dict of saved filenames.

    v6.1 A1: for PDFs, invokes the tiered orchestrator (direct → OA fallbacks
    → DOI content-negotiation → OpenReview → Wayback). Tier that wins is
    stamped on result.files_origin.pdf. If the primary URL succeeds, we
    short-circuit at the first tier (no extra work).
    """
    from provenance import record_origin
    from file_downloader_fallback import download_with_fallback
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

    # Download PDF — via tier chain. When a result already has a pdf_url we
    # feed it as the primary; missing pdf_url is OK too (tiers like OpenReview
    # can discover by title) — UNLESS the caller explicitly marked this a
    # URL-source-only ref (make_url_source_result). In that case the bib URL
    # IS the citation source; title-search tiers would find unrelated papers
    # ("What We Do" → "What We Do Not Fund" from California Arts Council),
    # so we must NOT search at all. Only attempt download when the bib URL
    # itself was a PDF (pdf_url set via pre-fetch + normalization).
    pdf_url = result.get("pdf_url")
    url_source_only = bool(result.get("url_source_only"))
    if url_source_only:
        # Honor only a concrete pdf_url (already-normalized bib URL pointing
        # at an actual PDF file). No discovery, no fallbacks.
        if pdf_url:
            filename = safe_key + "_pdf.pdf"
            path = os.path.join(project_dir, filename)
            if os.path.exists(path):
                files["pdf"] = filename
        # No pdf_url → no PDF to fetch. That's the whole point.
    elif pdf_url or result.get("doi") or result.get("title"):
        filename = safe_key + "_pdf.pdf"
        path = os.path.join(project_dir, filename)
        if force or not os.path.exists(path):
            outcome = download_with_fallback(
                pdf_url, path, bib_key=bib_key, result=result,
                title=result.get("title"), doi=result.get("doi"),
                headers_fn=_headers_for,
            )
            # Persist a compact download_log (§11.11) capped at 10 entries.
            result["download_log"] = (outcome.get("log") or [])[:10]
            if outcome.get("ok"):
                files["pdf"] = filename
                # provenance already recorded by the orchestrator
        elif os.path.exists(path):
            files["pdf"] = filename  # pre-existing; origin (if any) already persisted

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
                record_origin(result, "page", "direct", url)
        elif os.path.exists(path):
            files["page"] = filename

    # Build consolidated .md for reference checking
    # (HTML content becomes the .md body, not the abstract — see _build_reference_md.)
    md_filename = _build_reference_md(project_dir, safe_key, bib_key, result, files)
    if md_filename:
        files["md"] = md_filename

    # Status truthiness: lookup_engine sets status=found_pdf as soon as a candidate
    # pdf_url exists, but a candidate URL is not the same as a downloaded file.
    # When every tier failed (no PDF on disk), downgrade so the UI doesn't claim
    # we have a PDF that the user can't open.
    if result.get("status") == "found_pdf" and "pdf" not in files:
        if result.get("abstract"):
            result["status"] = "found_abstract"
        elif result.get("url"):
            result["status"] = "found_web_page"
        else:
            result["status"] = "not_found"
        logger.info("[%s] status downgraded to %s — pdf_url present but no tier downloaded a PDF",
                    bib_key, result["status"])

    return files


def _normalize_bib_url(url):
    """Rewrite known landing-page URLs to their direct-content variants.

    Thin wrapper over `url_normalizers.normalize(url)` — the actual rewriting
    rules (arXiv abs/html, OpenReview forum, and future A1 additions) live
    in `url_normalizers.py` as a pluggable registry.
    """
    from url_normalizers import normalize
    return normalize(url)


def pre_download_bib_url(project_dir, bib_key, url):
    """Try to download content from a bib entry's URL before running the lookup pipeline.

    Returns:
        - {} when no URL was provided.
        - {"pdf": filename, "tier": str, ...} or {"page": filename, "tier": str, ...}
           on success. `tier` is "direct" / "curl_cffi" / "playwright" / "wayback".
           Wayback success also includes "snapshot_url" + "captured_at" so callers
           can stamp provenance with the snapshot's actual timestamp.
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
            logger.info("[%s] Pre-downloaded PDF from bib URL: %s (tier=%s)",
                        bib_key, url, status.get("source_tier"))
            return _success_payload({"pdf": filename}, status, url)
    else:
        filename = safe_key + "_page.html"
        path = os.path.join(project_dir, filename)
        if _download_page(url, path, status_out=status):
            logger.info("[%s] Pre-downloaded HTML from bib URL: %s (tier=%s)",
                        bib_key, url, status.get("source_tier"))
            return _success_payload({"page": filename}, status, url)

    logger.info("[%s] Bib URL unreachable: %s status=%s kind=%s",
                bib_key, url, status.get("http_status"), status.get("kind"))
    return {
        "error": True,
        "http_status": status.get("http_status"),
        "kind": status.get("kind") or "unknown",
        "url": url,
    }


def _success_payload(base, status, requested_url):
    """Compose a pre_download_bib_url success dict, threading tier metadata."""
    out = dict(base)
    out["tier"] = status.get("source_tier") or "direct"
    out["url"] = status.get("snapshot_url") or requested_url
    captured_at = status.get("captured_at")
    if captured_at:
        out["captured_at"] = captured_at
    return out


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

    from provenance import record_origin, clear_origin

    if is_pdf:
        # Clear opposing HTML page (the new source is a PDF)
        _drop("page")
        clear_origin(result, "page")
        result["url"] = None
        result["pdf_url"] = new_url
        pdf_filename = safe_key + "_pdf.pdf"
        if _download_pdf(new_url, os.path.join(project_dir, pdf_filename)):
            files["pdf"] = pdf_filename
            record_origin(result, "pdf", "manual_set_link", new_url)
            downloaded = True
        # Note: don't touch existing abstract (it may have come from S2/Crossref).
    else:
        # Clear opposing PDF (the new source is an HTML page)
        _drop("pdf")
        clear_origin(result, "pdf")
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
            record_origin(result, "page", "manual_set_link", new_url)
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
    from provenance import record_origin, clear_origin
    clear_origin(result)  # wholesale replace — no prior origin applies
    record_origin(result, "pdf", "manual_upload", None)

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
    from provenance import record_origin, clear_origin
    clear_origin(result)  # fresh manual source — drop any prior tier stamps
    record_origin(result, "pasted", "manual_paste", None)

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
    body_filetype = None  # which artifact provided the body — drives the Wayback note

    # Highest priority: user-pasted content (verbatim, not extracted)
    pasted_file = files.get("pasted")
    if pasted_file:
        pasted_path = os.path.join(project_dir, pasted_file)
        try:
            with open(pasted_path, "r", encoding="utf-8") as f:
                body = f.read()
            body_filetype = "pasted"
        except OSError as e:
            logger.debug("Failed to read pasted content for %s: %s", bib_key, e)

    # Next: full text extracted from the PDF
    pdf_file = files.get("pdf")
    if not body and pdf_file:
        pdf_path = os.path.join(project_dir, pdf_file)
        body = extract_pdf_markdown(pdf_path, bib_key=bib_key) or ""
        if body:
            body_filetype = "pdf"

    # Fallback: markdown extracted from the HTML page
    if not body and files.get("page"):
        page_path = os.path.join(project_dir, files["page"])
        try:
            with open(page_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            body = _extract_markdown(html_content) or ""
            if body:
                body_filetype = "page"
        except Exception as e:
            logger.debug("HTML markdown fallback failed for %s: %s", bib_key, e)

    abstract = result.get("abstract") or ""

    # Skip if we have nothing useful
    if not body and not abstract:
        return None

    header = _format_md_header(bib_key, result)
    sections = [header]
    # Wayback annotation: if the body's source artifact was retrieved from the
    # Internet Archive, prepend a note so downstream readers (LLM claim-checker,
    # human reviewer) know the content is archival, not live.
    note = _wayback_note(result, body_filetype)
    if note:
        sections.append(note)
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


def _wayback_note(result, body_filetype):
    """Return a markdown note when the body artifact came from Wayback, else None.

    Reads result.files_origin[body_filetype] for the tier stamp. Surfaces the
    snapshot date so the reader knows when the archived content was captured."""
    if not body_filetype:
        return None
    origin = (result.get("files_origin") or {}).get(body_filetype) or {}
    if origin.get("tier") != "wayback":
        return None
    captured = origin.get("captured_at") or ""
    captured_short = captured[:10] if len(captured) >= 10 else captured  # YYYY-MM-DD
    snapshot_url = origin.get("url") or ""
    parts = ["> **Note:** This content was retrieved from the Internet Archive Wayback Machine"]
    if captured_short:
        parts.append(f" (snapshot captured {captured_short})")
    parts.append(", not from the live URL. The original page may be unavailable, moved, or bot-blocked.")
    if snapshot_url:
        parts.append(f"\n>\n> Snapshot: {snapshot_url}")
    return "".join(parts)


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

    Fallback chain on failure: same as `_download_page`. Bot-block statuses
    (202 / 403 / 429) and JS-challenge hosts route to curl_cffi → Playwright.
    Dead/blocked URLs fall through to the Wayback Machine tier (which can
    serve archived PDFs verbatim).

    On success, populates `status_out["source_tier"]` ("direct" / "curl_cffi" /
    "playwright" / "wayback"); for Wayback also `snapshot_url` + `captured_at`.

    When `status_out` is provided, on failure it is populated with keys
    `http_status`, `kind` ("http_4xx"/"http_5xx"/"network"/"validation"),
    and (optionally) `detail` so callers can distinguish failure modes.
    """
    from download_rules import is_js_challenge
    if is_js_challenge(url):
        logger.debug("PDF download: %s is JS-challenge host, going straight to heavy fallback", url)
        tier = _try_heavy_pdf_fallback(url, path)
        if tier:
            _record_success(status_out, tier)
            return True
        if _try_wayback_pdf_fallback(url, path, status_out):
            return True
        _record_failure(status_out, None, "js_challenge",
                        "JS-challenge host, no heavy fallback available")
        return False

    try:
        resp = get_session().get(url, headers=_headers_for(url), timeout=30,
                                   stream=True, allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("PDF download failed: url=%s status=%d", url, resp.status_code)
            if resp.status_code in _HEAVY_RETRY_STATUSES:
                tier = _try_heavy_pdf_fallback(url, path)
                if tier:
                    _record_success(status_out, tier)
                    return True
            if _try_wayback_pdf_fallback(url, path, status_out):
                return True
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
        _record_success(status_out, "direct")
        return True

    except Exception as e:
        logger.debug("PDF download error: url=%s error=%s", url, e)
        if _try_wayback_pdf_fallback(url, path, status_out):
            return True
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


# HTTP status codes that signal "bot block / WAF" rather than a true content
# absence — worth retrying via curl_cffi (TLS impersonation) and Playwright.
# 202 = EUR-Lex / JS interstitial. 403 = Cloudflare / generic bot block.
# 429 = rate-limited (real browser may have a fresh quota).
_HEAVY_RETRY_STATUSES = (202, 403, 429)


def _download_page(url, path, status_out=None):
    """Download an HTML page to `path`. Returns True on success, False otherwise.

    Fallback chain on failure:
      1. JS-challenge host (eur-lex / europa.eu / elsevier) → skip direct, go heavy.
      2. Direct fetch returns a "bot block" status (202 / 403 / 429) → heavy
         (curl_cffi → Playwright). Both gated by settings.
      3. Heavy fails → Wayback Machine snapshot of the same URL.

    On success, populates `status_out["source_tier"]` ("direct" / "curl_cffi" /
    "playwright" / "wayback"), and for Wayback also `status_out["snapshot_url"]`
    + `status_out["captured_at"]` so callers can stamp provenance.

    See `_download_pdf` for `status_out` failure semantics.
    """
    from download_rules import is_js_challenge
    if is_js_challenge(url):
        logger.debug("Page download: %s is JS-challenge host, going straight to heavy fallback", url)
        tier = _try_heavy_html_fallback(url, path, status_out)
        if tier:
            _record_success(status_out, tier)
            return True
        if _try_wayback_html_fallback(url, path, status_out):
            return True
        _record_failure(status_out, None, "js_challenge",
                        "JS-challenge host, no heavy fallback available")
        return False

    try:
        resp = get_session().get(url, headers=_headers_for(url), timeout=20,
                                   allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Page download failed: url=%s status=%d", url, resp.status_code)
            # Bot-block statuses → heavy retry, then Wayback.
            heavy_attempted = False
            if resp.status_code in _HEAVY_RETRY_STATUSES:
                heavy_attempted = True
                tier = _try_heavy_html_fallback(url, path, status_out)
                if tier:
                    _record_success(status_out, tier)
                    return True
            # Regardless of status, dead URLs may live in Wayback.
            if _try_wayback_html_fallback(url, path, status_out):
                return True
            # If we hit a bot-block status and even Playwright couldn't pass
            # (e.g. Cloudflare Turnstile), surface that explicitly so the user
            # knows to use Paste Content instead of treating it as a dead URL.
            if heavy_attempted and resp.status_code in (403, 429):
                _record_failure(status_out, resp.status_code, "bot_blocked",
                                "site bot-blocked (Cloudflare/WAF) — use Paste Content")
            else:
                _record_failure(status_out, resp.status_code, _http_failure_kind(resp.status_code))
            return False

        content = resp.text
        if len(content.encode("utf-8")) > MAX_PAGE_SIZE:
            content = content[:MAX_PAGE_SIZE]

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.debug("Page saved: %s (%d chars)", path, len(content))
        _record_success(status_out, "direct")
        return True

    except Exception as e:
        logger.debug("Page download error: url=%s error=%s", url, e)
        if _try_wayback_html_fallback(url, path, status_out):
            return True
        _record_failure(status_out, None, "network", str(e))
        return False


def _record_success(status_out, source_tier, snapshot_url=None, captured_at=None):
    """Helper: write success metadata into a caller-supplied dict."""
    if status_out is None:
        return
    status_out["source_tier"] = source_tier
    if snapshot_url:
        status_out["snapshot_url"] = snapshot_url
    if captured_at:
        status_out["captured_at"] = captured_at


def _try_wayback_html_fallback(url, path, status_out=None):
    """Save a Wayback snapshot of `url` as HTML. Returns True on success.

    Populates status_out with source_tier='wayback', snapshot_url, captured_at.
    Used as the last-resort HTML fetch — catches dead URLs (404/410), moved
    pages, and Cloudflare-blocked sites that even Playwright can't bypass."""
    ok, snapshot_url, captured_at = _fetch_html_via_wayback(url, path)
    if not ok:
        return False
    logger.info("Heavy HTML fallback: Wayback snapshot succeeded for %s (captured %s)",
                url, captured_at)
    _record_success(status_out, "wayback", snapshot_url=snapshot_url,
                    captured_at=captured_at)
    return True


def _try_wayback_pdf_fallback(url, path, status_out=None):
    """Save a Wayback Machine snapshot of `url` as PDF. Returns True on success.

    Reuses the orchestrator's `_tier_wayback` so PDF and HTML pre-fetch share
    the same Wayback discovery + id_-variant fetch logic. Stamps source_tier
    + snapshot_url + captured_at on status_out so callers can record provenance."""
    try:
        import file_downloader_fallback as fdf
        from file_downloader_fallback import FetchContext
    except ImportError:
        return False
    ctx = FetchContext(url=url, target_path=path, bib_key="", result={},
                       headers_fn=_headers_for)
    try:
        r = fdf._tier_wayback(ctx)
    except Exception as e:
        logger.debug("Wayback PDF fallback raised: %s", e)
        return False
    if not r.ok:
        return False
    logger.info("Heavy PDF fallback: Wayback snapshot succeeded for %s", url)
    # _tier_wayback sets final_url to the user-facing snapshot URL. The CDX
    # timestamp is encoded in that URL — best-effort extract for captured_at.
    captured_at = None
    if r.final_url:
        m = re.search(r"/web/(\d{8,14})", r.final_url)
        if m:
            captured_at = _wayback_ts_to_iso(m.group(1))
    _record_success(status_out, "wayback",
                    snapshot_url=r.final_url, captured_at=captured_at)
    return True


def _try_heavy_pdf_fallback(url, path):
    """Walk curl_cffi → Playwright for PDF downloads. Returns the winning
    tier name ("curl_cffi" / "playwright") on success, or None on failure.

    Reuses the orchestrator tier functions from file_downloader_fallback so
    pre-fetch and lookup-pipeline downloads share the same WAF-defeat logic.
    """
    try:
        import file_downloader_fallback as fdf
        from file_downloader_fallback import FetchContext
    except ImportError:
        return None
    ctx = FetchContext(url=url, target_path=path, bib_key="", result={},
                       headers_fn=_headers_for)
    try:
        r = fdf._tier_curl_cffi(ctx)
    except Exception as e:
        logger.debug("curl_cffi PDF fallback raised: %s", e)
        r = None
    if r is not None and r.ok:
        logger.info("Heavy PDF fallback: curl_cffi succeeded for %s", url)
        return "curl_cffi"
    try:
        r = fdf._tier_playwright(ctx)
    except Exception as e:
        logger.debug("Playwright PDF fallback raised: %s", e)
        r = None
    if r is not None and r.ok:
        logger.info("Heavy PDF fallback: Playwright succeeded for %s", url)
        return "playwright"
    return None


def _try_heavy_html_fallback(url, path, status_out=None):
    """Walk curl_cffi → Playwright for HTML pages. Returns the winning
    tier name on success, or None on failure.

    Each helper is a no-op when its fallback isn't enabled / installed, so this
    cleanly degrades on stripped-down installs. Used by `_download_page` and
    `pre_download_bib_url` when the direct fetch hits a JS challenge or bot block."""
    if _fetch_html_via_curl_cffi(url, path):
        logger.info("Heavy HTML fallback: curl_cffi succeeded for %s", url)
        return "curl_cffi"
    if _fetch_html_via_playwright(url, path):
        logger.info("Heavy HTML fallback: Playwright succeeded for %s", url)
        return "playwright"
    return None


_WAYBACK_CDX_URL = "https://archive.org/wayback/available"


def _fetch_html_via_wayback(url, path):
    """Save the closest Wayback Machine snapshot of `url` as HTML.

    Returns (ok: bool, snapshot_url: str|None, captured_at: str|None).
    captured_at is the snapshot's Wayback timestamp (YYYY-MM-DDTHH:MM:SS+00:00),
    NOT the current time — that's what the user actually got served.

    Always tries — there's no settings gate — because Wayback is free, light,
    and the right answer for dead URLs and Cloudflare-blocked sites alike."""
    if not url:
        return (False, None, None)
    try:
        resp = get_session().get(_WAYBACK_CDX_URL, params={"url": url}, timeout=15)
    except Exception as e:
        logger.debug("Wayback CDX query failed: url=%s err=%s", url, e)
        return (False, None, None)
    if resp.status_code != 200:
        logger.debug("Wayback CDX non-200: url=%s status=%d", url, resp.status_code)
        return (False, None, None)
    try:
        snap = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
    except Exception:
        return (False, None, None)
    snapshot_url = snap.get("url")
    raw_ts = snap.get("timestamp")  # "20230415123000"
    if not snapshot_url:
        logger.debug("Wayback CDX no snapshot for: %s", url)
        return (False, None, None)
    # Fetch the raw (id_) variant so Wayback serves the original bytes without
    # their toolbar HTML wrapper.
    raw_url = re.sub(r"/web/(\d+)/", r"/web/\1id_/", snapshot_url, count=1)
    try:
        r = get_session().get(raw_url, timeout=30, allow_redirects=True)
    except Exception as e:
        logger.debug("Wayback snapshot fetch failed: url=%s err=%s", raw_url, e)
        return (False, None, None)
    if r.status_code != 200:
        logger.debug("Wayback snapshot non-200: url=%s status=%d", raw_url, r.status_code)
        return (False, None, None)
    try:
        text = r.text or ""
    except Exception:
        return (False, None, None)
    if not text or len(text) < 100:
        return (False, None, None)
    if len(text.encode("utf-8")) > MAX_PAGE_SIZE:
        text = text[:MAX_PAGE_SIZE]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        logger.debug("Wayback HTML write error: %s", e)
        return (False, None, None)
    captured_at = _wayback_ts_to_iso(raw_ts) if raw_ts else None
    return (True, snapshot_url, captured_at)


def _wayback_ts_to_iso(ts):
    """Convert a Wayback timestamp (YYYYMMDDhhmmss) → ISO 8601 UTC."""
    if not ts or len(ts) < 8:
        return None
    try:
        return (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
                + (f"T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}+00:00"
                   if len(ts) >= 14 else "T00:00:00+00:00"))
    except Exception:
        return None


def _fetch_html_via_curl_cffi(url, path):
    """TLS-impersonating HTML fetch via curl_cffi. Returns True on success.

    Defeats fingerprinting WAFs that 4xx anonymous python-requests but accept
    a real Chrome's TLS ClientHello. No-ops when curl_cffi isn't enabled in
    settings or isn't installed.
    """
    try:
        from config import get_settings
        s = get_settings().get("download") or {}
        if not s.get("use_curl_cffi_fallback"):
            return False
    except Exception:
        return False
    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.debug("curl_cffi not installed; HTML heavy fallback skipped")
        return False
    impersonate = s.get("curl_cffi_impersonate", "chrome120")
    timeout = int(s.get("curl_cffi_timeout_s", 30))
    try:
        with cf_requests.Session() as sess:
            r = sess.get(url, impersonate=impersonate, timeout=timeout,
                         allow_redirects=True)
    except Exception as e:
        logger.debug("curl_cffi HTML fetch error: url=%s err=%s", url, e)
        return False
    if r.status_code != 200:
        logger.debug("curl_cffi HTML fetch non-200: url=%s status=%d", url, r.status_code)
        return False
    try:
        text = r.text or ""
    except Exception:
        text = ""
    if not text or len(text) < 100:
        logger.debug("curl_cffi HTML fetch empty/short body: url=%s len=%d", url, len(text))
        return False
    # Cloudflare may return 200 with the challenge page. Detect and defer to
    # the next tier (Playwright with anti-fingerprint, then Wayback).
    if _looks_like_challenge_page(text):
        logger.debug("curl_cffi captured a challenge page for %s — deferring", url)
        return False
    if len(text.encode("utf-8")) > MAX_PAGE_SIZE:
        text = text[:MAX_PAGE_SIZE]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        logger.debug("curl_cffi HTML fetch write error: %s", e)
        return False
    return True


_CHALLENGE_TITLE_MARKERS = (
    "just a moment",          # Cloudflare classic
    "checking your browser",  # Cloudflare older
    "verifying you are human",
    "attention required",     # Cloudflare WAF
    "ddos-guard",
    "please wait",
)

_CHALLENGE_BODY_MARKERS = (
    "challenge-error-text",       # Cloudflare Turnstile challenge ID
    "cf-browser-verification",
    "cf-challenge-running",
    "/cdn-cgi/challenge-platform/",
    "_cf_chl_opt",
)

# Anti-fingerprint init script: clear navigator.webdriver and a few other
# canonical "headless Chromium" tells. Defeats Cloudflare's basic bot checks
# without requiring playwright-stealth as an extra dependency.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""

_REAL_CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _looks_like_challenge_page(content):
    """True if the HTML looks like a bot-check / Cloudflare challenge interstitial."""
    if not content:
        return False
    lower = content[:5000].lower()  # markers always live in <head>/early body
    if any(m in lower for m in _CHALLENGE_BODY_MARKERS):
        return True
    # Title-tag check (most reliable for Cloudflare's "Just a moment...")
    import re as _re
    title_match = _re.search(r"<title[^>]*>([^<]+)</title>", lower, _re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
        if any(m in title for m in _CHALLENGE_TITLE_MARKERS):
            return True
    return False


def _fetch_html_via_playwright(url, path):
    """JS-rendering HTML fetch via Playwright. Returns True on success.

    Handles JS interstitials (EUR-Lex), SPA publisher portals, JS-rendered
    text, and Cloudflare Turnstile challenges. No-ops when Playwright isn't
    enabled in settings or isn't installed.

    Each call owns its own `sync_playwright()` lifecycle. Playwright's sync
    API binds Browser/Page objects to the calling thread; a long-lived shared
    BrowserPool fails with "cannot switch to a different thread" when Flask's
    short-lived refresh threads try to reuse it. Per-call setup costs ~1-2s
    (Chromium launch) but this fallback is off the hot path.

    Cloudflare handling: launches with anti-fingerprint flags, after goto
    detects challenge titles ("Just a moment...") and waits for them to
    resolve, validates the final content isn't still a challenge page.
    """
    try:
        from config import get_settings
        s = get_settings().get("download") or {}
        if not s.get("use_playwright_fallback"):
            return False
    except Exception:
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright not installed; HTML heavy fallback skipped")
        return False
    timeout = int(s.get("playwright_timeout_s", 30))
    challenge_wait_ms = int(s.get("playwright_challenge_wait_s", 25)) * 1000
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx_pw = browser.new_context(
                    user_agent=_REAL_CHROME_UA,
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )
                try:
                    try: ctx_pw.add_init_script(_STEALTH_INIT_SCRIPT)
                    except Exception: pass
                    page = ctx_pw.new_page()
                    # Apply playwright-stealth if installed (much stronger
                    # anti-fingerprint than our hand-rolled init script).
                    # Defeats Cloudflare's Turnstile in most cases.
                    try:
                        from playwright_stealth import Stealth
                        Stealth().apply_stealth_sync(page)
                    except ImportError:
                        pass
                    except Exception as e:
                        logger.debug("playwright-stealth apply failed: %s", e)
                    try:
                        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                    except Exception as e:
                        logger.debug("Playwright networkidle timeout, retrying domcontentloaded: %s", e)
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                        except Exception as e2:
                            logger.debug("Playwright HTML fetch goto failed: url=%s err=%s", url, e2)
                            return False
                    # Cloudflare challenge: wait for the title to change.
                    try:
                        title = (page.title() or "").lower()
                    except Exception:
                        title = ""
                    if title and any(m in title for m in _CHALLENGE_TITLE_MARKERS):
                        logger.info("Cloudflare-style challenge detected for %s (title=%r), waiting up to %ds",
                                    url, title, challenge_wait_ms // 1000)
                        try:
                            page.wait_for_function(
                                """() => {
                                    const t = (document.title || '').toLowerCase();
                                    return !['just a moment','checking your browser','verifying you are human','attention required','ddos-guard','please wait'].some(m => t.includes(m));
                                }""",
                                timeout=challenge_wait_ms,
                            )
                            # Give the post-challenge page a moment to fully render
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                pass
                        except Exception:
                            logger.warning("Cloudflare challenge did not resolve for %s — leaving for next tier", url)
                            return False
                    try:
                        content = page.content()
                    except Exception as e:
                        logger.debug("Playwright page.content() failed: url=%s err=%s", url, e)
                        return False
                    if not content or len(content) < 100:
                        logger.debug("Playwright HTML fetch empty/short body: url=%s len=%d", url, len(content or ""))
                        return False
                    # Final guard: don't save a challenge page as the source.
                    if _looks_like_challenge_page(content):
                        logger.warning("Playwright captured a challenge page for %s — not saving, deferring to next tier", url)
                        return False
                    if len(content.encode("utf-8")) > MAX_PAGE_SIZE:
                        content = content[:MAX_PAGE_SIZE]
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(content)
                    except OSError as e:
                        logger.debug("Playwright HTML fetch write error: %s", e)
                        return False
                    return True
                finally:
                    try: ctx_pw.close()
                    except Exception: pass
            finally:
                try: browser.close()
                except Exception: pass
    except Exception as e:
        logger.debug("Playwright HTML fetch outer error: url=%s err=%s", url, e)
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
