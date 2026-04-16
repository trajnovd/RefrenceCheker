import os
import re
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

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
        for suffix in ("_pdf.pdf", "_abstract.txt", "_page.html"):
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

    # If no abstract, try to extract one from the downloaded HTML page
    if not abstract and files.get("page"):
        page_path = os.path.join(project_dir, files["page"])
        try:
            with open(page_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            md = _extract_markdown(html_content)
            if md:
                result["abstract"] = md
                abstract_filename = safe_key + "_abstract.txt"
                abstract_path = os.path.join(project_dir, abstract_filename)
                with open(abstract_path, "w", encoding="utf-8") as f:
                    f.write(md)
                files["abstract"] = abstract_filename
                logger.debug("Extracted markdown abstract from HTML for %s (%d chars)", bib_key, len(md))
        except Exception as e:
            logger.debug("Failed to extract markdown from page for %s: %s", bib_key, e)

    return files


def _download_pdf(url, path):
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30, stream=True, allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("PDF download failed: url=%s status=%d", url, resp.status_code)
            return False

        # Check content length
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_PDF_SIZE:
            logger.debug("PDF too large: url=%s size=%s", url, content_length)
            return False

        # Stream and validate first bytes
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PDF_SIZE:
                logger.debug("PDF exceeded size limit during download: url=%s", url)
                return False

        content = b"".join(chunks)

        # Validate PDF magic bytes
        if not content[:5].startswith(b"%PDF"):
            logger.debug("PDF validation failed (not a PDF): url=%s first_bytes=%s", url, content[:20])
            return False

        with open(path, "wb") as f:
            f.write(content)

        logger.debug("PDF saved: %s (%d bytes)", path, len(content))
        return True

    except Exception as e:
        logger.debug("PDF download error: url=%s error=%s", url, e)
        return False


def _extract_markdown(html):
    """Extract main content from HTML and convert to simple markdown."""
    try:
        soup = BeautifulSoup(html, 'html.parser')

        # Remove junk elements
        for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                                   'aside', 'form', 'noscript', 'iframe']):
            tag.decompose()

        # Find main content container
        main = (soup.find('article')
                or soup.find('main')
                or soup.find('div', class_=re.compile(r'content|post|article|entry', re.I)))
        if not main:
            main = soup.body or soup

        lines = []
        for el in main.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                  'p', 'li', 'blockquote', 'pre']):
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
            elif tag == 'blockquote':
                lines.append('> ' + text)
            else:
                lines.append(text)
            lines.append('')

        result = '\n'.join(lines).strip()
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result if len(result) > 50 else None
    except Exception as e:
        logger.debug("Markdown extraction failed: %s", e)
        return None


def _download_page(url, path):
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Page download failed: url=%s status=%d", url, resp.status_code)
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
        return False
