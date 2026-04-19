"""Tests for provenance tracking (v6.1 A0.5).

Pins:
- record_origin writes a complete origin entry under result.files_origin[filetype]
- Calling with different tier/url for the same filetype overwrites (re-download)
- get_origin returns the entry or None
- clear_origin(filetype) drops one entry; clear_origin() drops all
- record_origin is robust to bad input (None result, empty tier) — doesn't raise
- process_reference seeds result.files_origin = {} at init
- Tier-0 `download_reference_files` stamps "direct" for PDF / HTML successes
"""

import os
from unittest.mock import patch

import pytest

from provenance import record_origin, get_origin, clear_origin


class TestRecordOrigin:
    def test_writes_complete_entry(self):
        result = {}
        record_origin(result, "pdf", "openreview", "https://openreview.net/pdf?id=X")
        origin = result["files_origin"]["pdf"]
        assert origin["tier"] == "openreview"
        assert origin["url"] == "https://openreview.net/pdf?id=X"
        assert origin["host"] == "openreview.net"
        assert origin["captured_at"]  # iso8601 stamp
        assert origin["captured_at"].startswith("20")  # 2026, 2027...

    def test_different_tier_overwrites_prior(self):
        """Second download via a different tier replaces the first."""
        result = {}
        record_origin(result, "pdf", "direct", "https://example.com/x.pdf")
        record_origin(result, "pdf", "wayback", "https://web.archive.org/...")
        assert result["files_origin"]["pdf"]["tier"] == "wayback"

    def test_filetypes_are_independent(self):
        result = {}
        record_origin(result, "pdf", "direct", "https://a.com/x.pdf")
        record_origin(result, "page", "wayback", "https://web.archive.org/...")
        assert result["files_origin"]["pdf"]["tier"] == "direct"
        assert result["files_origin"]["page"]["tier"] == "wayback"

    def test_missing_url_still_records(self):
        """Manual uploads have no URL — should still record the tier."""
        result = {}
        record_origin(result, "pdf", "manual_upload", None)
        assert result["files_origin"]["pdf"]["tier"] == "manual_upload"
        assert result["files_origin"]["pdf"]["url"] is None
        assert result["files_origin"]["pdf"]["host"] == ""

    def test_bad_input_doesnt_raise(self):
        # None result is silently ignored — tier implementations can't break the pipeline
        record_origin(None, "pdf", "direct", "https://x")  # no raise
        record_origin({}, "", "direct", "https://x")       # empty filetype
        record_origin({}, "pdf", "", "https://x")          # empty tier


class TestGetOrigin:
    def test_returns_entry(self):
        result = {"files_origin": {"pdf": {"tier": "openreview"}}}
        assert get_origin(result, "pdf") == {"tier": "openreview"}

    def test_missing_returns_none(self):
        assert get_origin({}, "pdf") is None
        assert get_origin({"files_origin": {}}, "pdf") is None

    def test_none_result_returns_none(self):
        assert get_origin(None, "pdf") is None


class TestClearOrigin:
    def test_clear_one_filetype(self):
        result = {"files_origin": {"pdf": {"tier": "a"}, "page": {"tier": "b"}}}
        clear_origin(result, "pdf")
        assert "pdf" not in result["files_origin"]
        assert "page" in result["files_origin"]

    def test_clear_all(self):
        result = {"files_origin": {"pdf": {"tier": "a"}, "page": {"tier": "b"}}}
        clear_origin(result)
        assert result["files_origin"] == {}

    def test_clear_missing_filetype_is_noop(self):
        result = {"files_origin": {"pdf": {"tier": "a"}}}
        clear_origin(result, "page")  # no such entry
        assert "pdf" in result["files_origin"]


class TestProcessReferenceSeedsOrigin:
    """process_reference must initialize result['files_origin'] = {} at creation
    so tiers can safely call record_origin without an existence-check every time."""

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_files_origin_initialized(self, *mocks):
        from lookup_engine import process_reference
        ref = {
            "bib_key": "k", "title": "T", "doi": None,
            "authors": "", "year": "2024", "journal": None,
            "url": None, "arxiv_id": None, "entry_type": "article",
            "status": None, "raw_bib": None,
        }
        result = process_reference(ref, metadata_only=False)
        assert "files_origin" in result
        assert result["files_origin"] == {}


class TestTier0StampsDirect:
    """Tier 0 (the baseline requests.get path) stamps 'direct' on the result
    so even pre-A1 the UI can show 'Downloaded via: direct' consistently."""

    def test_direct_pdf_origin(self, tmp_path):
        """v6.1 A1: download_reference_files routes through the fallback
        orchestrator; on Tier-0 (direct) success the orchestrator stamps
        provenance automatically."""
        from file_downloader import download_reference_files
        import file_downloader_fallback as fdf
        from file_downloader_fallback import FetchResult
        result = {
            "bib_key": "k",
            "pdf_url": "https://example.com/paper.pdf",
            "url": None, "abstract": None,
        }
        def fake_direct(ctx):
            # Write a valid PDF to the target path so downstream steps see a file
            with open(ctx.target_path, "wb") as f:
                f.write(b"%PDF-1.4 body")
            return FetchResult(ok=True, final_url=ctx.url, http_status=200, elapsed_ms=50)
        with patch.object(fdf, "_tier_direct", side_effect=fake_direct):
            download_reference_files(str(tmp_path), "k", result)
        origin = get_origin(result, "pdf")
        assert origin is not None
        assert origin["tier"] == "direct"
        assert origin["url"] == "https://example.com/paper.pdf"
        assert origin["host"] == "example.com"

    def test_direct_html_origin(self, tmp_path):
        from file_downloader import download_reference_files
        result = {
            "bib_key": "k",
            "pdf_url": None,
            "url": "https://example.com/page",
            "abstract": None,
        }
        with patch("file_downloader._download_page") as mock_page:
            def fake(url, path, **kwargs):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("<html><body>long enough content for extraction</body></html>")
                return True
            mock_page.side_effect = fake
            download_reference_files(str(tmp_path), "k", result)
        origin = get_origin(result, "page")
        assert origin["tier"] == "direct"
        assert origin["url"] == "https://example.com/page"

    def test_no_origin_when_download_fails(self, tmp_path):
        """Failed downloads must NOT leave a stale origin entry."""
        from file_downloader import download_reference_files
        import file_downloader_fallback as fdf
        from file_downloader_fallback import FetchResult
        result = {"bib_key": "k", "pdf_url": "https://example.com/x.pdf",
                  "url": None, "abstract": None}
        fail = FetchResult(ok=False, kind="http_4xx", http_status=403)
        # Mock EVERY tier so none stamp provenance
        with patch.object(fdf, "_tier_direct", return_value=fail), \
             patch.object(fdf, "_tier_oa_fallbacks", return_value=fail), \
             patch.object(fdf, "_tier_doi_negotiation", return_value=fail), \
             patch.object(fdf, "_tier_openreview", return_value=fail), \
             patch.object(fdf, "_tier_wayback", return_value=fail):
            download_reference_files(str(tmp_path), "k", result)
        assert get_origin(result, "pdf") is None


class TestManualSourceReplacementTags:
    """Set Link / Upload PDF / Paste Content each stamp their own tier."""

    def test_set_link_pdf_stamps_manual_set_link(self, tmp_path):
        from file_downloader import replace_reference_source
        result = {"bib_key": "k", "files": {}, "pdf_url": None, "url": None}
        with patch("file_downloader._download_pdf") as mock_pdf:
            def fake(url, path, **kwargs):
                with open(path, "wb") as f: f.write(b"%PDF-1.4 body")
                return True
            mock_pdf.side_effect = fake
            replace_reference_source(str(tmp_path), "k", result,
                                      "https://example.com/paper.pdf")
        assert get_origin(result, "pdf")["tier"] == "manual_set_link"

    def test_upload_pdf_stamps_manual_upload(self, tmp_path):
        from file_downloader import set_uploaded_pdf
        result = {"bib_key": "k", "files": {}, "pdf_url": None, "url": None,
                  "files_origin": {"pdf": {"tier": "direct"}}}  # prior stamp
        set_uploaded_pdf(str(tmp_path), "k", result, b"%PDF-1.4 bytes")
        assert get_origin(result, "pdf")["tier"] == "manual_upload"

    def test_paste_content_stamps_manual_paste(self, tmp_path):
        from file_downloader import set_pasted_content
        result = {"bib_key": "k", "files": {}, "pdf_url": None, "url": None,
                  "files_origin": {"page": {"tier": "direct"}}}  # prior stamp
        set_pasted_content(str(tmp_path), "k", result, "some long enough content")
        # Paste replaces everything → old page origin gone; pasted stamped.
        assert get_origin(result, "page") is None
        assert get_origin(result, "pasted")["tier"] == "manual_paste"
