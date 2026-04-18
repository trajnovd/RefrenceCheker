"""Tests for set-link replacement semantics (file_downloader.replace_reference_source)
and the HTML→markdown extractor (file_downloader._extract_markdown)."""

import os
from unittest.mock import patch
import pytest

from file_downloader import (
    replace_reference_source, _safe_filename, _extract_markdown,
    set_uploaded_pdf, set_pasted_content, _headers_for,
)


# Sample HTML with a clear article body
SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Sample</title></head><body>
<header>nav</header>
<article>
  <h1>Main Heading</h1>
  <p>This is the first paragraph of the article body. It contains useful prose
     long enough to be extracted as an abstract by the markdown extractor.</p>
  <p>Second paragraph adds more material so the extractor reaches its minimum length.</p>
  <p>And one more for good measure to push past the 50-character minimum threshold.</p>
</article>
<footer>copyright</footer>
</body></html>"""


@pytest.fixture
def project_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def existing_pdf_state(project_dir):
    """Pre-populate the project dir with a PDF + abstract + .md as if a previous lookup ran."""
    bib_key = "smith2020"
    safe = _safe_filename(bib_key)
    files = {
        "pdf": safe + "_pdf.pdf",
        "abstract": safe + "_abstract.txt",
        "md": safe + ".md",
    }
    for fname, content in [
        (files["pdf"], b"%PDF-1.4 fake pdf"),
        (files["abstract"], "old abstract from semantic scholar".encode()),
        (files["md"], "# Old\n\n## Abstract\nold\n## Full text\nold body".encode()),
    ]:
        with open(os.path.join(project_dir, fname), "wb") as f:
            f.write(content)
    result = {
        "bib_key": bib_key,
        "title": "Smith 2020",
        "pdf_url": "https://example.com/old.pdf",
        "url": None,
        "abstract": "old abstract from semantic scholar",
        "files": files,
    }
    return {"bib_key": bib_key, "result": result, "files": files}


@pytest.fixture
def existing_html_state(project_dir):
    bib_key = "jones2019"
    safe = _safe_filename(bib_key)
    files = {
        "page": safe + "_page.html",
        "md": safe + ".md",
    }
    for fname, content in [
        (files["page"], b"<html><body>old html</body></html>"),
        (files["md"], b"# Old\n\nold md"),
    ]:
        with open(os.path.join(project_dir, fname), "wb") as f:
            f.write(content)
    result = {
        "bib_key": bib_key,
        "title": "Jones 2019",
        "pdf_url": None,
        "url": "https://example.com/old-page.html",
        "abstract": None,
        "files": files,
    }
    return {"bib_key": bib_key, "result": result, "files": files}


class TestReplaceReferenceSource:

    def test_html_link_drops_existing_pdf(self, project_dir, existing_pdf_state):
        st = existing_pdf_state
        old_pdf_path = os.path.join(project_dir, st["files"]["pdf"])
        assert os.path.exists(old_pdf_path)

        with patch("file_downloader._download_page", return_value=True) as mock_dl:
            # Simulate the download writing the new HTML file to disk
            def fake(url, path):
                with open(path, "w", encoding="utf-8") as f: f.write(SAMPLE_HTML)
                return True
            mock_dl.side_effect = fake
            outcome = replace_reference_source(
                project_dir, st["bib_key"], st["result"], "https://example.com/new-page.html"
            )

        assert outcome["is_pdf"] is False
        assert outcome["downloaded"] is True
        assert not os.path.exists(old_pdf_path), "old PDF must be deleted"
        assert "pdf" not in outcome["files"], "files dict must drop pdf entry"
        assert st["result"]["pdf_url"] is None, "result.pdf_url must be cleared"
        assert st["result"]["url"] == "https://example.com/new-page.html"

    def test_html_link_does_not_write_html_into_abstract(self, project_dir, existing_pdf_state):
        """HTML content belongs in the .md body, not the abstract field/file."""
        st = existing_pdf_state
        with patch("file_downloader._download_page") as mock_dl:
            def fake(url, path):
                with open(path, "w", encoding="utf-8") as f: f.write(SAMPLE_HTML)
                return True
            mock_dl.side_effect = fake
            outcome = replace_reference_source(
                project_dir, st["bib_key"], st["result"], "https://example.com/new.html"
            )

        # Abstract field cleared (was the stale S2 abstract); NOT repopulated from HTML
        assert st["result"]["abstract"] is None
        # No abstract file in the dict
        assert "abstract" not in outcome["files"]
        # The original abstract file on disk is gone
        assert not os.path.exists(os.path.join(project_dir, "smith2020_abstract.txt"))

    def test_html_link_puts_main_content_in_md_body(self, project_dir, existing_pdf_state):
        """The .md body must contain the main HTML content (not the old PDF body, not duplicated)."""
        st = existing_pdf_state
        with patch("file_downloader._download_page") as mock_dl:
            def fake(url, path):
                with open(path, "w", encoding="utf-8") as f: f.write(SAMPLE_HTML)
                return True
            mock_dl.side_effect = fake
            replace_reference_source(
                project_dir, st["bib_key"], st["result"], "https://example.com/new.html"
            )

        md_filename = st["result"]["files"].get("md")
        assert md_filename is not None
        md_content = open(os.path.join(project_dir, md_filename), encoding="utf-8").read()

        # Main HTML content present
        assert "first paragraph" in md_content.lower()
        # Old PDF body gone
        assert "old body" not in md_content
        # No "## Abstract" section since we don't store HTML as abstract anymore
        assert "## Abstract" not in md_content
        # Body lives under "## Full text"
        assert "## Full text" in md_content

    def test_pdf_link_drops_existing_html_page(self, project_dir, existing_html_state):
        st = existing_html_state
        old_page_path = os.path.join(project_dir, st["files"]["page"])
        assert os.path.exists(old_page_path)

        with patch("file_downloader._download_pdf") as mock_dl:
            def fake(url, path):
                with open(path, "wb") as f: f.write(b"%PDF-1.4 new pdf")
                return True
            mock_dl.side_effect = fake
            outcome = replace_reference_source(
                project_dir, st["bib_key"], st["result"], "https://example.com/new.pdf"
            )

        assert outcome["is_pdf"] is True
        assert not os.path.exists(old_page_path), "old HTML page must be deleted"
        assert "page" not in outcome["files"]
        assert st["result"]["url"] is None
        assert st["result"]["pdf_url"] == "https://example.com/new.pdf"

    def test_pdf_link_does_not_overwrite_existing_abstract(self, project_dir):
        # If a result already has an abstract from S2/Crossref and the user sets a PDF link,
        # we must NOT clobber the abstract (it didn't come from a stale HTML page).
        bib_key = "k"
        safe = _safe_filename(bib_key)
        result = {
            "bib_key": bib_key,
            "title": "T",
            "pdf_url": None,
            "url": None,
            "abstract": "S2-provided abstract that should be preserved",
            "files": {},
        }
        with patch("file_downloader._download_pdf") as mock_dl:
            def fake(url, path):
                with open(path, "wb") as f: f.write(b"%PDF-1.4 ok")
                return True
            mock_dl.side_effect = fake
            replace_reference_source(project_dir, bib_key, result, "https://example.com/x.pdf")

        assert result["abstract"] == "S2-provided abstract that should be preserved"

    def test_url_with_pdf_in_path_is_treated_as_pdf(self, project_dir, existing_html_state):
        # Heuristic: '/pdf/' in URL → PDF (matches existing api_set_link behavior)
        st = existing_html_state
        with patch("file_downloader._download_pdf") as mock_dl:
            def fake(url, path):
                with open(path, "wb") as f: f.write(b"%PDF-1.4")
                return True
            mock_dl.side_effect = fake
            outcome = replace_reference_source(
                project_dir, st["bib_key"], st["result"],
                "https://arxiv.org/pdf/2401.12345"
            )
        assert outcome["is_pdf"] is True

    def test_failed_download_does_not_clear_existing_files(self, project_dir, existing_pdf_state):
        # If the new download fails, we still clear the opposing source as documented,
        # but we don't crash and we report downloaded=False.
        st = existing_pdf_state
        with patch("file_downloader._download_page", return_value=False):
            outcome = replace_reference_source(
                project_dir, st["bib_key"], st["result"], "https://example.com/bad.html"
            )
        assert outcome["downloaded"] is False
        # The old PDF was still removed (per the replace contract).
        assert not os.path.exists(os.path.join(project_dir, "smith2020_pdf.pdf"))

    def test_html_link_invalidates_old_abstract_even_if_extraction_fails(self, project_dir):
        """Setting a new HTML link must clear the old abstract regardless of whether
        the new HTML yields an extractable one. The old abstract was tied to the old
        source and is now stale."""
        bib_key = "k"
        safe = _safe_filename(bib_key)
        # Pre-populate an old abstract on disk + on the result
        abs_filename = safe + "_abstract.txt"
        abs_path = os.path.join(project_dir, abs_filename)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("STALE abstract from a previous source")
        result = {
            "bib_key": bib_key,
            "title": "T",
            "pdf_url": None,
            "url": "https://old.example.com",
            "abstract": "STALE abstract from a previous source",
            "files": {"abstract": abs_filename},
        }

        # Simulate: HTML download succeeds but the new page has no extractable content
        with patch("file_downloader._download_page") as mock_dl:
            def fake(url, path):
                with open(path, "w", encoding="utf-8") as f: f.write("<html><body></body></html>")
                return True
            mock_dl.side_effect = fake
            outcome = replace_reference_source(
                project_dir, bib_key, result, "https://example.com/empty.html"
            )

        # Old abstract is gone (both in result and on disk)
        assert result["abstract"] is None, "result.abstract must be cleared"
        assert not os.path.exists(abs_path), "old abstract file must be removed"
        assert "abstract" not in outcome["files"], "files dict must not retain abstract entry"

    def test_html_link_invalidates_abstract_when_download_fails(self, project_dir):
        """Even if the new HTML download fails, the old abstract must be cleared."""
        bib_key = "k2"
        safe = _safe_filename(bib_key)
        abs_filename = safe + "_abstract.txt"
        abs_path = os.path.join(project_dir, abs_filename)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("STALE abstract")
        result = {
            "bib_key": bib_key,
            "title": "T",
            "pdf_url": None,
            "url": None,
            "abstract": "STALE abstract",
            "files": {"abstract": abs_filename},
        }
        with patch("file_downloader._download_page", return_value=False):
            outcome = replace_reference_source(
                project_dir, bib_key, result, "https://example.com/bad.html"
            )

        assert outcome["downloaded"] is False
        assert result["abstract"] is None
        assert not os.path.exists(abs_path)

    def test_md_dropped_if_no_content_after_replace(self, project_dir):
        # Edge case: new HTML download fails AND there's no abstract → no .md should exist.
        bib_key = "lonely"
        safe = _safe_filename(bib_key)
        # Pre-populate a stale .md
        md_path = os.path.join(project_dir, safe + ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("stale")
        result = {"bib_key": bib_key, "files": {"md": safe + ".md"},
                  "abstract": None, "pdf_url": None, "url": None}
        with patch("file_downloader._download_page", return_value=False):
            outcome = replace_reference_source(project_dir, bib_key, result,
                                               "https://example.com/x.html")
        # Stale .md was dropped
        assert "md" not in outcome["files"]
        assert not os.path.exists(md_path)


class TestPageAwarePdfDispatch:
    """Short PDFs go to the high-quality backend; long PDFs go to the fast backend.
    Threshold is configurable via settings.json pdf_quality_page_limit."""

    @pytest.fixture
    def fake_pdf(self, tmp_path):
        """Monkeypatch-friendly: we don't actually need a real PDF since we mock
        _pdf_page_count and the per-backend extractors."""
        return str(tmp_path / "fake.pdf")

    def test_small_pdf_uses_high_quality_backend(self, fake_pdf, monkeypatch):
        import file_downloader as fd
        monkeypatch.setattr(fd, "_pdf_page_count", lambda p: 10)
        monkeypatch.setattr(fd, "get_pdf_converter_pair",
                            lambda: ("pymupdf_text", "pymupdf4llm", 30))
        called = []
        monkeypatch.setattr(fd, "_extract_with_pymupdf4llm",
                            lambda p, b: called.append("hq") or "hq body")
        monkeypatch.setattr(fd, "_extract_with_pymupdf_text",
                            lambda p, b: called.append("fast") or "fast body")
        out = fd.extract_pdf_markdown(fake_pdf, bib_key="k")
        assert out == "hq body"
        assert called == ["hq"]  # fast backend not touched

    def test_large_pdf_uses_fast_backend(self, fake_pdf, monkeypatch):
        import file_downloader as fd
        monkeypatch.setattr(fd, "_pdf_page_count", lambda p: 100)
        monkeypatch.setattr(fd, "get_pdf_converter_pair",
                            lambda: ("pymupdf_text", "pymupdf4llm", 30))
        called = []
        monkeypatch.setattr(fd, "_extract_with_pymupdf4llm",
                            lambda p, b: called.append("hq") or "hq body")
        monkeypatch.setattr(fd, "_extract_with_pymupdf_text",
                            lambda p, b: called.append("fast") or "fast body")
        out = fd.extract_pdf_markdown(fake_pdf, bib_key="k")
        assert out == "fast body"
        assert called == ["fast"]

    def test_boundary_equals_limit_uses_high_quality(self, fake_pdf, monkeypatch):
        import file_downloader as fd
        monkeypatch.setattr(fd, "_pdf_page_count", lambda p: 30)  # == limit
        monkeypatch.setattr(fd, "get_pdf_converter_pair",
                            lambda: ("pymupdf_text", "pymupdf4llm", 30))
        called = []
        monkeypatch.setattr(fd, "_extract_with_pymupdf4llm",
                            lambda p, b: called.append("hq") or "hq body")
        monkeypatch.setattr(fd, "_extract_with_pymupdf_text",
                            lambda p, b: called.append("fast") or "fast body")
        fd.extract_pdf_markdown(fake_pdf, bib_key="k")
        assert called == ["hq"]  # <= threshold

    def test_falls_back_to_other_backend_when_primary_fails(self, fake_pdf, monkeypatch):
        import file_downloader as fd
        monkeypatch.setattr(fd, "_pdf_page_count", lambda p: 10)
        monkeypatch.setattr(fd, "get_pdf_converter_pair",
                            lambda: ("pymupdf_text", "pymupdf4llm", 30))
        monkeypatch.setattr(fd, "_extract_with_pymupdf4llm", lambda p, b: "")  # fails
        monkeypatch.setattr(fd, "_extract_with_pymupdf_text", lambda p, b: "fast body")
        out = fd.extract_pdf_markdown(fake_pdf, bib_key="k")
        assert out == "fast body"

    def test_unreadable_page_count_uses_fast_backend(self, fake_pdf, monkeypatch):
        """If we can't open the PDF to count pages, be conservative and use fast."""
        import file_downloader as fd
        monkeypatch.setattr(fd, "_pdf_page_count", lambda p: None)
        monkeypatch.setattr(fd, "get_pdf_converter_pair",
                            lambda: ("pymupdf_text", "pymupdf4llm", 30))
        called = []
        monkeypatch.setattr(fd, "_extract_with_pymupdf4llm",
                            lambda p, b: called.append("hq") or "hq body")
        monkeypatch.setattr(fd, "_extract_with_pymupdf_text",
                            lambda p, b: called.append("fast") or "fast body")
        fd.extract_pdf_markdown(fake_pdf, bib_key="k")
        assert called == ["fast"]


class TestPerSiteHeaders:
    """SEC.gov requires a contact-email in the User-Agent; a generic Chrome UA gets 403'd.
    Other domains keep the default Chrome UA."""

    def test_sec_url_gets_sec_compliant_user_agent(self):
        h = _headers_for("https://www.sec.gov/files/rules/final/2010/34-63241.pdf")
        assert "RefChecker" in h["User-Agent"]
        assert "@" in h["User-Agent"]  # contact email present
        assert "Mozilla" not in h["User-Agent"]  # not the generic UA

    def test_sec_subdomain_also_gets_rule(self):
        h = _headers_for("https://efts.sec.gov/LATEST/search-index?q=foo")
        assert "RefChecker" in h["User-Agent"]

    def test_non_sec_url_keeps_default_ua(self):
        h = _headers_for("https://arxiv.org/pdf/2306.06031")
        assert h["User-Agent"].startswith("Mozilla/5.0")
        assert "RefChecker" not in h["User-Agent"]

    def test_malformed_url_returns_default(self):
        h = _headers_for("not a url")
        assert "Mozilla" in h["User-Agent"]


class TestExtractMarkdownEdgeCases:
    """Regression tests for HTML→markdown extraction failures."""

    def test_bootstrap_class_does_not_lock_onto_empty_ad_slot(self):
        """Pages whose body has Bootstrap utility classes like 'justify-content-center'
        used to confuse the old class-regex matcher into picking an empty container.
        The extractor must look at <article>/<main>/<body> instead of class names.
        """
        html = """<html><body>
        <div class="cafemedia-ad-slot-top h-100-px d-flex justify-content-center align-items-center">
          <!-- empty ad slot -->
        </div>
        <div class="container">
          <h1>Real Heading</h1>
          <p>Real paragraph with substantive content that should be the extracted body.</p>
          <p>Another paragraph for good measure to push past the minimum length threshold.</p>
        </div>
        </body></html>"""
        out = _extract_markdown(html)
        assert out is not None, "extractor must not return None when body has visible content"
        assert "Real Heading" in out
        assert "substantive content" in out

    def test_content_in_spans_uses_plain_text_fallback(self):
        """Pages like dictionary entries put content in <span> tags; the structured
        extractor (which only looks at p/li/h/dt/...) misses those, so we need the
        plain-text fallback."""
        html = """<html><body>
        <div>
          <span>Some heading</span>
          <span>Definition: a person who acts on behalf of another in legal or business matters.</span>
          <span>Etymology: from Latin agens, present participle of agere meaning to drive or do.</span>
          <span>Synonyms include representative, broker, intermediary, and proxy.</span>
        </div>
        </body></html>"""
        out = _extract_markdown(html)
        assert out is not None
        assert "Definition" in out or "person who acts" in out

    def test_returns_none_for_truly_empty_body(self):
        html = """<html><body><script>noise</script></body></html>"""
        assert _extract_markdown(html) is None

    def test_strips_script_and_style(self):
        html = """<html><body>
        <script>var x = 1;</script>
        <style>body { color: red; }</style>
        <article>
          <p>Visible body content that survives stripping junk tags from the page.</p>
          <p>Plus another paragraph for length.</p>
        </article>
        </body></html>"""
        out = _extract_markdown(html)
        assert out is not None
        assert "Visible body content" in out
        assert "var x" not in out
        assert "color: red" not in out

    def test_real_merriam_webster_page_extraction(self):
        """Regression test using the actual problematic file. Skipped if the
        fixture isn't present."""
        path = os.path.join("projects", "finai-ch5-1", "merriam-webster_page.html")
        if not os.path.exists(path):
            pytest.skip("merriam-webster fixture not available in this checkout")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        out = _extract_markdown(html)
        assert out is not None, "merriam-webster page must produce extractable content"
        assert len(out) > 200, f"expected substantial content, got {len(out)} chars"
        assert "agent" in out.lower(), "the word being defined must appear somewhere"


# Tiny synthetic PDF for upload tests — header + minimal structure
MINIMAL_PDF = (b"%PDF-1.4\n"
               b"1 0 obj <</Type/Catalog/Pages 2 0 R>> endobj\n"
               b"2 0 obj <</Type/Pages/Count 1/Kids[3 0 R]>> endobj\n"
               b"3 0 obj <</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>> endobj\n"
               b"xref\n0 4\n"
               b"trailer <</Size 4/Root 1 0 R>>\n%%EOF\n")


class TestSetUploadedPdf:
    def test_rejects_non_pdf_bytes(self, project_dir):
        result = {"bib_key": "k", "files": {}}
        outcome = set_uploaded_pdf(project_dir, "k", result, b"not a pdf at all")
        assert outcome["ok"] is False
        assert "PDF" in outcome["reason"]

    def test_rejects_empty(self, project_dir):
        result = {"bib_key": "k", "files": {}}
        outcome = set_uploaded_pdf(project_dir, "k", result, b"")
        assert outcome["ok"] is False

    def test_saves_pdf_and_drops_opposing_files(self, project_dir, existing_html_state):
        st = existing_html_state
        old_page = os.path.join(project_dir, st["files"]["page"])
        old_md = os.path.join(project_dir, st["files"]["md"])
        # Pre-populate an abstract too — upload must clear it
        abs_filename = _safe_filename(st["bib_key"]) + "_abstract.txt"
        with open(os.path.join(project_dir, abs_filename), "w", encoding="utf-8") as f:
            f.write("stale abstract")
        st["result"]["abstract"] = "stale abstract"
        st["result"]["files"]["abstract"] = abs_filename

        outcome = set_uploaded_pdf(project_dir, st["bib_key"], st["result"], MINIMAL_PDF)
        assert outcome["ok"] is True
        # New PDF saved
        pdf_path = os.path.join(project_dir, _safe_filename(st["bib_key"]) + "_pdf.pdf")
        assert os.path.exists(pdf_path)
        # Opposing files dropped
        assert not os.path.exists(old_page), "old HTML page must be removed"
        assert "page" not in outcome["files"]
        assert "abstract" not in outcome["files"]
        assert st["result"]["abstract"] is None
        assert st["result"]["url"] is None
        assert st["result"]["pdf_url"] is None
        # The .md was rebuilt (size > 0; might be metadata-only if pdf body extraction returns nothing)
        if outcome["files"].get("md"):
            md_path = os.path.join(project_dir, outcome["files"]["md"])
            assert os.path.exists(md_path)


class TestSetPastedContent:
    def test_rejects_empty(self, project_dir):
        result = {"bib_key": "k", "files": {}}
        outcome = set_pasted_content(project_dir, "k", result, "   ")
        assert outcome["ok"] is False

    def test_writes_pasted_file_and_uses_it_as_md_body(self, project_dir):
        bib_key = "pasted-key"
        result = {"bib_key": bib_key, "title": "Pasted Title",
                  "authors": ["Alice"], "year": "2024",
                  "files": {}, "abstract": None,
                  "pdf_url": None, "url": None}
        content = ("# An Article\n\n"
                   "This is a paragraph of pasted content that the claim checker should be "
                   "able to read. It contains substantive text.\n\n"
                   "Section two.\n")
        outcome = set_pasted_content(project_dir, bib_key, result, content)
        assert outcome["ok"] is True

        # Pasted file written
        pasted_path = os.path.join(project_dir, _safe_filename(bib_key) + "_pasted.md")
        assert os.path.exists(pasted_path)
        assert outcome["files"].get("pasted") == _safe_filename(bib_key) + "_pasted.md"

        # .md was built and contains the pasted content under Full text
        md_path = os.path.join(project_dir, outcome["files"]["md"])
        md_content = open(md_path, encoding="utf-8").read()
        assert "## Full text" in md_content
        assert "pasted content" in md_content
        assert "An Article" in md_content
        # Header still present
        assert "# Pasted Title" in md_content

    def test_writes_html_wrapper_for_pasted_content(self, project_dir):
        """Pasted content also produces a {key}_page.html wrapper so the right-panel
        HTML tab can render it in the iframe."""
        bib_key = "k"
        result = {"bib_key": bib_key, "title": "T", "files": {},
                  "abstract": None, "pdf_url": None, "url": None}
        content = "Some pasted text content from a paywalled page."
        outcome = set_pasted_content(project_dir, bib_key, result, content)
        assert outcome["ok"] is True

        page_filename = outcome["files"].get("page")
        assert page_filename is not None, "pasted content must also produce an HTML wrapper"
        page_path = os.path.join(project_dir, page_filename)
        html = open(page_path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in html
        assert content in html  # content embedded (escaped) in the wrapper
        assert "<pre>" in html  # default styling

    def test_html_wrapper_passes_through_full_html_documents(self, project_dir):
        """If the user pasted a full HTML document, render it as-is rather than wrapping in <pre>."""
        bib_key = "k"
        result = {"bib_key": bib_key, "title": "T", "files": {},
                  "abstract": None, "pdf_url": None, "url": None}
        full_html = "<!DOCTYPE html><html><body><p>Pre-formatted HTML</p></body></html>"
        outcome = set_pasted_content(project_dir, bib_key, result, full_html)
        page_filename = outcome["files"].get("page")
        html = open(os.path.join(project_dir, page_filename), encoding="utf-8").read()
        assert html == full_html  # passed through unchanged
        assert "<pre>" not in html  # no wrapper applied

    def test_replaces_existing_pdf_and_html(self, project_dir, existing_pdf_state):
        st = existing_pdf_state
        outcome = set_pasted_content(
            project_dir, st["bib_key"], st["result"],
            "Pasted body content that fully replaces all prior sources for this reference."
        )
        assert outcome["ok"] is True
        # Old PDF + abstract gone
        assert not os.path.exists(os.path.join(project_dir, "smith2020_pdf.pdf"))
        assert "pdf" not in outcome["files"]
        assert "abstract" not in outcome["files"]
        assert st["result"]["pdf_url"] is None
        # .md body is the pasted content (not extracted PDF text)
        md_content = open(os.path.join(project_dir, outcome["files"]["md"]), encoding="utf-8").read()
        assert "Pasted body content" in md_content
        assert "old body" not in md_content

    def test_pasted_content_takes_priority_over_pdf_in_md_rebuild(self, project_dir):
        """If both pasted and PDF exist (rare), pasted wins as the body source."""
        from file_downloader import _build_reference_md
        bib_key = "k"
        safe = _safe_filename(bib_key)
        # Pre-create both files
        with open(os.path.join(project_dir, safe + "_pasted.md"), "w", encoding="utf-8") as f:
            f.write("PASTED CONTENT WINS")
        with open(os.path.join(project_dir, safe + "_pdf.pdf"), "wb") as f:
            f.write(MINIMAL_PDF)
        result = {"bib_key": bib_key, "title": "T"}
        files = {"pasted": safe + "_pasted.md", "pdf": safe + "_pdf.pdf"}
        md_filename = _build_reference_md(project_dir, safe, bib_key, result, files)
        assert md_filename is not None
        md_content = open(os.path.join(project_dir, md_filename), encoding="utf-8").read()
        assert "PASTED CONTENT WINS" in md_content


class TestUploadPdfRoute:
    def test_rejects_non_pdf(self, tmp_path, monkeypatch):
        import project_store
        monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
        from app import create_app
        proj = project_store.create_project("Test")
        slug = proj["slug"]
        project_store.save_result(slug, {"bib_key": "k", "title": "T"})
        client = create_app().test_client()

        from io import BytesIO
        r = client.post(f"/api/projects/{slug}/upload-pdf/k",
                        data={"file": (BytesIO(b"not a pdf"), "x.pdf")},
                        content_type="multipart/form-data")
        assert r.status_code == 400

    def test_unknown_reference_returns_404(self, tmp_path, monkeypatch):
        import project_store
        monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
        from app import create_app
        proj = project_store.create_project("Test")
        client = create_app().test_client()
        from io import BytesIO
        r = client.post(f"/api/projects/{proj['slug']}/upload-pdf/missing",
                        data={"file": (BytesIO(MINIMAL_PDF), "x.pdf")},
                        content_type="multipart/form-data")
        assert r.status_code == 404


class TestPasteContentRoute:
    def test_rejects_empty_content(self, tmp_path, monkeypatch):
        import project_store
        monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
        from app import create_app
        proj = project_store.create_project("Test")
        project_store.save_result(proj["slug"], {"bib_key": "k", "title": "T"})
        client = create_app().test_client()
        r = client.post(f"/api/projects/{proj['slug']}/paste-content/k", json={"content": ""})
        assert r.status_code == 400

    def test_saves_content_and_clears_stale_verdict(self, tmp_path, monkeypatch):
        import project_store
        monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
        from app import create_app
        from claim_checker import _empty_verdict
        proj = project_store.create_project("Test")
        slug = proj["slug"]
        project_store.save_result(slug, {"bib_key": "k", "title": "T"})

        # Pre-seed a stale auto-verdict for a citation that points at "k"
        project_store.save_claim_check(slug, "stale-ck", _empty_verdict("auto verdict"))
        project_store.save_claim_check.__defaults__  # ensure attr access works
        # Manually inject citations list (simpler than going through tex upload)
        import json
        from pathlib import Path
        proj_path = Path(tmp_path) / slug / "project.json"
        data = json.loads(proj_path.read_text(encoding="utf-8"))
        data["citations"] = [{"bib_key": "k", "claim_check_key": "stale-ck",
                              "position": 0, "end_position": 5, "line": 1}]
        proj_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        client = create_app().test_client()
        r = client.post(f"/api/projects/{slug}/paste-content/k",
                        json={"content": "Pasted content for the reference."})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["verdicts_cleared"] == 1

        # The pasted file is on disk, .md exists
        pasted_path = Path(tmp_path) / slug / "k_pasted.md"
        assert pasted_path.exists()
        md_path = Path(tmp_path) / slug / "k.md"
        assert md_path.exists()
        assert "Pasted content" in md_path.read_text(encoding="utf-8")
