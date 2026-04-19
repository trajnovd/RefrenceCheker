"""Tests for bib-URL-first download flow.

Covers:
- pre_download_bib_url (PDF vs HTML detection, success/failure)
- process_reference(metadata_only=True) preserves bib URL, still extracts metadata
- process_reference(metadata_only=False) is unchanged (full pipeline)
- process_all with custom process_fn
- Integration: bib URL download succeeds → metadata-only mode used, APIs don't override URL
"""

import os
from unittest.mock import patch, MagicMock
import pytest

from file_downloader import pre_download_bib_url, _safe_filename
from lookup_engine import process_reference, process_all


# ============================================================
# pre_download_bib_url
# ============================================================

class TestNormalizeBibUrl:
    """arXiv abs URLs should be rewritten to the PDF URL before download."""

    def test_arxiv_abs_to_pdf(self):
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://arxiv.org/abs/2308.00016") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_arxiv_abs_with_version_stripped(self):
        from file_downloader import _normalize_bib_url
        # /abs/<id>v3 → /pdf/<id> (we drop the version, arXiv serves the latest)
        assert _normalize_bib_url("https://arxiv.org/abs/2308.00016v3") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_arxiv_html_to_pdf(self):
        """Regression: cheridito2025 case. arxiv.org/html/<id> is the HTML rendition;
        downstream extraction works much better against the canonical PDF."""
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://arxiv.org/html/2507.06345v2") == \
               "https://arxiv.org/pdf/2507.06345"

    def test_arxiv_html_without_version(self):
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://arxiv.org/html/2507.06345") == \
               "https://arxiv.org/pdf/2507.06345"

    def test_arxiv_pdf_url_unchanged(self):
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://arxiv.org/pdf/2308.00016") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_non_arxiv_unchanged(self):
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://example.com/paper.pdf") == \
               "https://example.com/paper.pdf"


class TestPreDownloadBibUrl:
    def test_returns_empty_for_no_url(self, tmp_path):
        assert pre_download_bib_url(str(tmp_path), "k", None) == {}
        assert pre_download_bib_url(str(tmp_path), "k", "") == {}

    def test_arxiv_abs_url_pre_downloads_as_pdf(self, tmp_path):
        """Regression: alphagpt2023 case. bib has arxiv abstract URL; we should
        download the PDF, not the abstract page."""
        from unittest.mock import patch
        with patch("file_downloader._download_pdf") as mock_pdf, \
             patch("file_downloader._download_page") as mock_page:
            def fake_pdf(url, path, **kwargs):
                with open(path, "wb") as f: f.write(b"%PDF-1.4 content")
                return True
            mock_pdf.side_effect = fake_pdf
            result = pre_download_bib_url(str(tmp_path), "k",
                                          "https://arxiv.org/abs/2308.00016")
        assert "pdf" in result
        # Verify _download_pdf was called with the PDF URL (not the abs URL)
        called_url = mock_pdf.call_args[0][0]
        assert "/pdf/" in called_url
        assert "/abs/" not in called_url
        # _download_page should NOT have been called
        mock_page.assert_not_called()

    def test_pdf_url_downloads_pdf(self, tmp_path):
        with patch("file_downloader._download_pdf") as mock_dl:
            def fake(url, path, **kwargs):
                with open(path, "wb") as f:
                    f.write(b"%PDF-1.4 test")
                return True
            mock_dl.side_effect = fake
            result = pre_download_bib_url(str(tmp_path), "k", "https://example.com/paper.pdf")
        assert "pdf" in result
        assert os.path.exists(os.path.join(str(tmp_path), result["pdf"]))

    def test_html_url_downloads_page(self, tmp_path):
        with patch("file_downloader._download_page") as mock_dl:
            def fake(url, path, **kwargs):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("<html><body>content</body></html>")
                return True
            mock_dl.side_effect = fake
            result = pre_download_bib_url(str(tmp_path), "k", "https://example.com/page")
        assert "page" in result

    def test_arxiv_pdf_url_detected_as_pdf(self, tmp_path):
        with patch("file_downloader._download_pdf") as mock_dl:
            mock_dl.return_value = True
            result = pre_download_bib_url(str(tmp_path), "k",
                                          "https://arxiv.org/pdf/2401.12345")
        assert "pdf" in result
        mock_dl.assert_called_once()

    def test_failed_download_returns_error_info(self, tmp_path):
        """When the bib URL fails, return rich error info so callers can mark the
        reference as broken instead of falling through to a wrong-paper title search."""
        # Simulate a 403 response in _download_page (Man Group / corporate site case)
        def page_403(url, path, status_out=None):
            if status_out is not None:
                status_out["http_status"] = 403
                status_out["kind"] = "http_4xx"
            return False
        with patch("file_downloader._download_page", side_effect=page_403):
            result = pre_download_bib_url(str(tmp_path), "k", "https://example.com/page")
        assert result.get("error") is True
        assert result.get("http_status") == 403
        assert result.get("kind") == "http_4xx"
        assert result.get("url") == "https://example.com/page"
        # And not a "success" key
        assert "page" not in result and "pdf" not in result

    def test_failed_pdf_download_returns_error_info(self, tmp_path):
        def pdf_404(url, path, status_out=None):
            if status_out is not None:
                status_out["http_status"] = 404
                status_out["kind"] = "http_4xx"
            return False
        with patch("file_downloader._download_pdf", side_effect=pdf_404):
            result = pre_download_bib_url(str(tmp_path), "k", "https://example.com/x.pdf")
        assert result.get("error") is True
        assert result.get("http_status") == 404
        assert result.get("kind") == "http_4xx"

    def test_network_error_returns_error_info(self, tmp_path):
        """Connection refused / DNS failure / timeout — all surface as kind=network."""
        def page_network(url, path, status_out=None):
            if status_out is not None:
                status_out["http_status"] = None
                status_out["kind"] = "network"
            return False
        with patch("file_downloader._download_page", side_effect=page_network):
            result = pre_download_bib_url(str(tmp_path), "k", "https://example.com/page")
        assert result.get("error") is True
        assert result.get("http_status") is None
        assert result.get("kind") == "network"


class TestDownloadFailureClassification:
    """_download_pdf and _download_page populate status_out with HTTP status + kind."""

    def test_pdf_4xx_classification(self, tmp_path):
        from file_downloader import _download_pdf
        # 403 also triggers heavy fallback + Wayback in the new pipeline; mock
        # them to off so the test isolates the direct-fetch classification.
        with patch("file_downloader.get_session") as mock_session, \
             patch("file_downloader._try_heavy_pdf_fallback", return_value=None), \
             patch("file_downloader._try_wayback_pdf_fallback", return_value=False):
            mock_get = MagicMock()
            mock_session.return_value.get = mock_get
            resp = MagicMock()
            resp.status_code = 403
            mock_get.return_value = resp
            status = {}
            ok = _download_pdf("https://example.com/x.pdf",
                               str(tmp_path / "out.pdf"), status_out=status)
        assert ok is False
        assert status["http_status"] == 403
        assert status["kind"] == "http_4xx"

    def test_pdf_5xx_classification(self, tmp_path):
        from file_downloader import _download_pdf
        with patch("file_downloader.get_session") as mock_session, \
             patch("file_downloader._try_wayback_pdf_fallback", return_value=False):
            mock_get = MagicMock()
            mock_session.return_value.get = mock_get
            resp = MagicMock()
            resp.status_code = 503
            mock_get.return_value = resp
            status = {}
            _download_pdf("https://example.com/x.pdf",
                          str(tmp_path / "out.pdf"), status_out=status)
        assert status["http_status"] == 503
        assert status["kind"] == "http_5xx"

    def test_pdf_network_error_classification(self, tmp_path):
        from file_downloader import _download_pdf
        import requests
        with patch("file_downloader.get_session") as mock_session, \
             patch("file_downloader._try_wayback_pdf_fallback", return_value=False):
            mock_session.return_value.get.side_effect = requests.ConnectionError("DNS failure")
            status = {}
            ok = _download_pdf("https://example.com/x.pdf",
                               str(tmp_path / "out.pdf"), status_out=status)
        assert ok is False
        assert status["http_status"] is None
        assert status["kind"] == "network"

    def test_page_4xx_classification(self, tmp_path):
        from file_downloader import _download_page
        # Wayback HTML fallback is always tried after a non-200; mock to off
        # so the test isolates the direct-fetch classification.
        with patch("file_downloader.get_session") as mock_session, \
             patch("file_downloader._try_wayback_html_fallback", return_value=False):
            mock_get = MagicMock()
            mock_session.return_value.get = mock_get
            resp = MagicMock()
            resp.status_code = 404
            mock_get.return_value = resp
            status = {}
            ok = _download_page("https://example.com/page",
                                str(tmp_path / "out.html"), status_out=status)
        assert ok is False
        assert status["http_status"] == 404
        assert status["kind"] == "http_4xx"

    def test_pdf_validation_failure_when_not_a_pdf(self, tmp_path):
        """Server returned 200 with non-PDF content (e.g. HTML error page)."""
        from file_downloader import _download_pdf
        with patch("file_downloader.get_session") as mock_session:
            mock_get = MagicMock()
            mock_session.return_value.get = mock_get
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            resp.iter_content.return_value = [b"<!DOCTYPE html>"]
            mock_get.return_value = resp
            status = {}
            ok = _download_pdf("https://example.com/x.pdf",
                               str(tmp_path / "out.pdf"), status_out=status)
        assert ok is False
        assert status["kind"] == "validation"


# ============================================================
# process_reference metadata_only mode
# ============================================================

class TestProcessReferenceMetadataOnly:
    """Test that metadata_only=True preserves bib URL and still enriches metadata."""

    def _make_ref(self, url=None, **overrides):
        ref = {
            "bib_key": "test2024",
            "title": "Test Paper",
            "doi": None,
            "authors": "Smith, J.",
            "year": "2024",
            "journal": None,
            "url": url,
            "arxiv_id": None,
            "entry_type": "article",
            "status": None,
            "raw_bib": "@article{test2024, title={Test Paper}}",
        }
        ref.update(overrides)
        return ref

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value={
        "abstract": "S2 abstract here", "citation_count": 42, "pdf_url": "https://s2.example.com/paper.pdf",
    })
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_metadata_only_preserves_html_bib_url(self, *mocks):
        """When bib URL is HTML and metadata_only=True, APIs enrich citation_count
        but do NOT override url and do NOT keep abstract (the downloaded content
        is the text source — an API abstract would be redundant)."""
        ref = self._make_ref(url="https://my-bib-url.example.com/page")
        result = process_reference(ref, metadata_only=True)
        # Bib URL preserved
        assert result["url"] == "https://my-bib-url.example.com/page"
        # S2's pdf_url NOT set (metadata_only restores it to None for HTML bib URL)
        assert result["pdf_url"] is None
        # Abstract cleared — the downloaded content IS the text
        assert result["abstract"] is None
        # But other metadata IS extracted
        assert result["citation_count"] == 42
        # "URL" tag is placed first in sources so the UI shows it as the primary badge
        assert "URL" in result["sources"]
        assert result["sources"][0] == "URL"

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value={
        "abstract": "S2 abstract", "citation_count": 10,
    })
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_metadata_only_preserves_pdf_bib_url(self, *mocks):
        """When bib URL is a PDF, metadata_only sets pdf_url to the bib URL.
        result.url is also kept (= the human-readable original URL) so the right panel
        can show provenance. For arXiv abs URLs, normalization rewrites to the PDF URL."""
        ref = self._make_ref(url="https://example.com/paper.pdf")
        result = process_reference(ref, metadata_only=True)
        assert result["pdf_url"] == "https://example.com/paper.pdf"
        # url is kept (the human-readable bib URL); same as pdf_url here since they're the same
        assert result["url"] == "https://example.com/paper.pdf"
        assert result["status"] == "found_pdf"

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_metadata_only_arxiv_abs_url_normalized_to_pdf(self, *mocks):
        """Regression: alphagpt2023. arxiv.org/abs/<id> bib URL must be normalized
        so result.pdf_url points to the PDF (matching what pre_download_bib_url
        actually downloaded). result.url keeps the human-readable abs URL."""
        ref = self._make_ref(url="https://arxiv.org/abs/2308.00016")
        result = process_reference(ref, metadata_only=True)
        assert result["pdf_url"] == "https://arxiv.org/pdf/2308.00016"
        assert result["url"] == "https://arxiv.org/abs/2308.00016"
        assert result["status"] == "found_pdf"

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value={
        "abstract": "S2 abstract", "pdf_url": "https://s2.example.com/paper.pdf",
    })
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_metadata_only_false_allows_api_url_override(self, *mocks):
        """Standard mode (metadata_only=False): S2's pdf_url IS used."""
        ref = self._make_ref(url="https://my-bib-url.example.com/page")
        result = process_reference(ref, metadata_only=False)
        # S2's pdf_url is set (standard behavior)
        assert result["pdf_url"] == "https://s2.example.com/paper.pdf"

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value={
        # OpenAlex returns a fuzzy title match for an UNRELATED older paper —
        # different authors, different year, same/similar title.
        "abstract": "old paper abstract",
        "citation_count": 257,
        "authors": ["Yuriy Nevmyvaka", "Yi Feng", "Michael Kearns"],
        "year": "2006",
        "doi": "10.1145/1143844.1143929",
    })
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_metadata_only_preserves_bib_identity_fields(self, *mocks):
        """Regression: cheridito2025 case. The bib URL gave the right PDF, but
        OpenAlex matched the title fuzzily to a 2006 Nevmyvaka et al. paper and
        overwrote authors/year. In metadata_only mode the bib IS canonical for
        identity — title/authors/year/journal must come from the bib."""
        ref = self._make_ref(
            url="https://arxiv.org/pdf/2507.06345",
            title="Reinforcement Learning for Trade Execution with Market Impact",
            authors="Cheridito, Patrick and Weiss, Moritz",
            year="2025",
            journal="arXiv",
        )
        result = process_reference(ref, metadata_only=True)
        # Identity fields preserved from bib, NOT from OpenAlex's fuzzy match
        assert result["title"] == "Reinforcement Learning for Trade Execution with Market Impact"
        assert result["authors"] == ["Cheridito, Patrick and Weiss, Moritz"]
        assert result["year"] == "2025"
        assert result["journal"] == "arXiv"
        # API enrichment kept for fields the bib didn't have
        assert result["citation_count"] == 257
        assert result["doi"] == "10.1145/1143844.1143929"

    def test_metadata_only_preserves_raw_bib(self):
        ref = self._make_ref(url="https://example.com/page")
        ref["raw_bib"] = "@article{test2024, title={X}}"
        result = process_reference(ref, metadata_only=True)
        assert result.get("raw_bib") == "@article{test2024, title={X}}"


# ============================================================
# process_all with custom process_fn
# ============================================================

class TestArxivPreferredOverPublisher:
    """When both arXiv and a publisher (SSRN/Elsevier/...) have a paper, prefer arXiv.
    Publisher PDFs often 403 on anonymous downloads; arXiv is always accessible."""

    def _make_ref(self, **overrides):
        ref = {
            "bib_key": "yang2023fingpt",
            "title": "FinGPT: Open-Source Financial Large Language Models",
            "doi": None, "authors": "Hongyang Yang", "year": "2023",
            "journal": "Proceedings of the IJCAI Workshop on Financial Technology",
            "url": None, "arxiv_id": None,
            "entry_type": "article", "status": None, "raw_bib": None,
        }
        ref.update(overrides)
        return ref

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value={
        "pdf_url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4489826",
        "abstract": "S2 abstract", "citation_count": 50,
    })
    @patch("lookup_engine.search_arxiv", return_value={
        "pdf_url": "https://arxiv.org/pdf/2306.06031v2",
        "url": "https://arxiv.org/abs/2306.06031",
        "abstract": "arXiv abstract", "arxiv_id": "2306.06031",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_arxiv_overrides_ssrn_pdf(self, *mocks):
        """S2 returns an SSRN URL, arXiv also has the paper — arXiv should win."""
        result = process_reference(self._make_ref(), metadata_only=False)
        assert "arxiv.org" in result["pdf_url"]
        assert "ssrn.com" not in result["pdf_url"]
        assert "arxiv" in result["sources"]

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value={
        "pdf_url": "https://arxiv.org/pdf/already-arxiv.pdf",
        "abstract": "S2 abstract",
    })
    @patch("lookup_engine.search_arxiv", return_value={
        "pdf_url": "https://arxiv.org/pdf/2306.06031v2",
        "url": "https://arxiv.org/abs/2306.06031", "abstract": "arXiv abstract",
        "arxiv_id": "2306.06031",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_arxiv_does_not_override_existing_arxiv_pdf(self, *mocks):
        """If pdf_url is already an arXiv URL, don't clobber it with the search result."""
        result = process_reference(self._make_ref(), metadata_only=False)
        assert result["pdf_url"] == "https://arxiv.org/pdf/already-arxiv.pdf"

    @patch("lookup_engine.lookup_crossref", return_value={
        "title": "A Reality Check for Data Snooping",
        "authors": ["White"], "year": "2000",
        "journal": "Econometrica",
        "doi": "10.1111/1468-0262.00152",
        "url": "https://doi.org/10.1111/1468-0262.00152",
    })
    @patch("lookup_engine.lookup_unpaywall", return_value={
        # Unpaywall claims this is OA, but the Wiley URL is bot-blocked by Cloudflare
        "pdf_url": "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/1468-0262.00152",
    })
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value=None)  # not on arXiv
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value={
        # Google finds a university mirror (Bruce Hansen's page at Wisconsin)
        "pdf_url": "https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf",
        "url": "https://www.ssc.wisc.edu/~bhansen/718/",
        "abstract": None,
    })
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    def test_fragile_wiley_pdf_overridden_by_google_found_mirror(self, *mocks):
        """Regression: Wiley/SSRN/econstor URLs bot-block despite Unpaywall claiming OA.
        When the current pdf_url is fragile AND Google Search finds a non-fragile
        alternate (university, author page, arXiv), override."""
        ref = {
            "bib_key": "White2000RealityCheck",
            "title": "A Reality Check for Data Snooping",
            "doi": "10.1111/1468-0262.00152",
            "authors": "White, Halbert", "year": "2000",
            "journal": "Econometrica",
            "url": None, "arxiv_id": None,
            "entry_type": "article", "status": None, "raw_bib": None,
        }
        result = process_reference(ref, metadata_only=False)
        assert "wisc.edu" in result["pdf_url"]
        assert "wiley.com" not in result["pdf_url"]
        assert "google_search" in result["sources"]

    @patch("lookup_engine.lookup_crossref", return_value={
        "title": "X", "authors": [], "year": "2024",
        "doi": "10.1000/x", "url": "https://doi.org/10.1000/x",
    })
    @patch("lookup_engine.lookup_unpaywall", return_value={
        "pdf_url": "https://some-university.edu/papers/x.pdf",  # NOT fragile
    })
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value=None)
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_google_search")
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    def test_non_fragile_pdf_does_not_trigger_google_search(self, mock_scholarly, mock_gs, *_):
        """Negative: if pdf_url is already on a healthy domain, Step 4 should NOT run
        (saves Google CSE quota)."""
        ref = {"bib_key": "k", "title": "X", "doi": "10.1000/x",
               "authors": "A", "year": "2024", "url": None, "arxiv_id": None,
               "entry_type": "article", "status": None, "raw_bib": None}
        process_reference(ref, metadata_only=False)
        mock_gs.assert_not_called()

    @patch("lookup_engine.lookup_crossref", return_value={
        "title": "...and the Cross-Section of Expected Returns",
        "authors": ["Harvey", "Liu", "Zhu"], "year": "2016",
        "journal": "Review of Financial Studies",
        "doi": "10.1093/rfs/hhv059",
        "url": "https://doi.org/10.1093/rfs/hhv059",
    })
    @patch("lookup_engine.lookup_unpaywall", return_value={
        "pdf_url": "https://academic.oup.com/rfs/article-pdf/29/1/5/hhv059.pdf",
    })
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value={
        "pdf_url": "https://arxiv.org/pdf/2301.09173v1",
        "url": "https://arxiv.org/abs/2301.09173",
        "abstract": "Labor Income Risk paper (wrong paper).",
        "arxiv_id": "2301.09173",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_doi_resolved_pdf_protected_from_arxiv_title_match(self, *mocks):
        """Regression: the Harvey-Liu-Zhu (2016) case. When the bib has a DOI AND
        CrossRef/Unpaywall resolved a PDF via that DOI, an arXiv title search with
        fuzzy (70%+) overlap must NOT override — it can find a different paper."""
        ref = {
            "bib_key": "HarveyLiuZhu2016",
            "title": "...and the Cross-Section of Expected Returns",
            "doi": "10.1093/rfs/hhv059",
            "authors": "Harvey, Liu, Zhu", "year": "2016",
            "journal": "Review of Financial Studies",
            "url": None, "arxiv_id": None,
            "entry_type": "article", "status": None, "raw_bib": None,
        }
        result = process_reference(ref, metadata_only=False)
        # Unpaywall's DOI-based PDF URL must be preserved
        assert "academic.oup.com" in result["pdf_url"]
        assert "2301.09173" not in (result.get("pdf_url") or "")
        # arXiv match (id 2301.09173 → year 2023) is rejected by the year-mismatch
        # guard since bib year is 2016 (>3 year gap), so "arxiv" is not in sources.
        # This is stricter — and more correct — than the prior "tag-but-don't-override"
        # behavior: a different paper is not a legitimate source for this citation.
        assert "arxiv" not in result["sources"]
        assert "crossref" in result["sources"]
        assert "unpaywall" in result["sources"]


class TestArxivYearMismatchGuard:
    """Regression: hochreiter1997long. arXiv title-search returned id=1602.03032
    (Greff et al. 2016 'LSTM: A Search Space Odyssey') for the bib's
    'Long short-term memory' / 1997 paper. The arXiv id encodes year (1602 → 2016)
    so a 19-year gap is detectable — reject the override rather than download the
    wrong paper."""

    def _ref(self, **overrides):
        ref = {
            "bib_key": "hochreiter1997long",
            "title": "Long short-term memory",
            "doi": None,
            "authors": "Hochreiter, Sepp and Schmidhuber, Jurgen",
            "year": "1997",
            "journal": "Neural computation",
            "url": None, "arxiv_id": None,
            "entry_type": "article", "status": None, "raw_bib": None,
        }
        ref.update(overrides)
        return ref

    def test_arxiv_year_extraction(self):
        from lookup_engine import _arxiv_year
        # Modern format YYMM.NNNNN
        assert _arxiv_year("1602.03032") == 2016
        assert _arxiv_year("2401.12345") == 2024
        assert _arxiv_year("9101.00001") == 1991  # arXiv launched Aug 1991
        # Legacy format <subject>/YYMMNNN
        assert _arxiv_year("math/0102001") == 2001
        assert _arxiv_year("cs.AI/9912001") == 1999
        # None / malformed → None
        assert _arxiv_year(None) is None
        assert _arxiv_year("") is None
        assert _arxiv_year("not-an-arxiv-id") is None

    def test_years_compatible_threshold(self):
        from lookup_engine import _years_compatible
        assert _years_compatible(1997, 1997) is True
        assert _years_compatible(2023, 2024) is True   # within 3
        assert _years_compatible(2020, 2023) is True   # exactly 3
        assert _years_compatible(2020, 2024) is False  # 4 > 3
        assert _years_compatible(1997, 2016) is False  # the LSTM case
        # Unknown years are permissive (don't block when we can't compare)
        assert _years_compatible(None, 2024) is True
        assert _years_compatible(2024, None) is True
        assert _years_compatible(None, None) is True

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value={
        "abstract": "the LSTM paper abstract", "citation_count": 95710,
        "authors": ["Sepp Hochreiter", "Jurgen Schmidhuber"], "year": "1997",
    })
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value={
        # Wrong paper: Greff et al. 2016 "LSTM: A Search Space Odyssey"
        "arxiv_id": "1602.03032",
        "pdf_url": "https://arxiv.org/pdf/1602.03032v2",
        "url": "https://arxiv.org/abs/1602.03032",
        "abstract": "Search space odyssey abstract",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_lstm_1997_rejects_2016_arxiv_match(self, *mocks):
        result = process_reference(self._ref(), metadata_only=False)
        # The 19-year-gap arXiv match must be rejected — pdf_url / url stay clean,
        # arxiv source is NOT added.
        assert "1602.03032" not in (result.get("pdf_url") or "")
        assert "1602.03032" not in (result.get("url") or "")
        assert "arxiv" not in result["sources"]
        # OpenAlex's legitimate enrichment is still kept
        assert result["citation_count"] == 95710

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value={
        "arxiv_id": "2401.12345", "pdf_url": "https://arxiv.org/pdf/2401.12345",
        "url": "https://arxiv.org/abs/2401.12345", "abstract": "modern paper abstract",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_compatible_year_does_not_block_override(self, *mocks):
        # Bib year 2024, arXiv year 2024 — must NOT be blocked
        ref = self._ref(year="2024", title="Some Modern Paper")
        result = process_reference(ref, metadata_only=False)
        assert "2401.12345" in result["pdf_url"]
        assert "arxiv" in result["sources"]

    @patch("lookup_engine.lookup_crossref", return_value=None)
    @patch("lookup_engine.lookup_unpaywall", return_value=None)
    @patch("lookup_engine.lookup_openalex", return_value=None)
    @patch("lookup_engine.lookup_semantic_scholar", return_value=None)
    @patch("lookup_engine.search_arxiv", return_value={
        "arxiv_id": "2401.12345", "pdf_url": "https://arxiv.org/pdf/2401.12345",
        "url": "https://arxiv.org/abs/2401.12345", "abstract": "abstract",
    })
    @patch("lookup_engine.lookup_wikipedia", return_value=None)
    @patch("lookup_engine.lookup_scholarly", return_value=None)
    @patch("lookup_engine.lookup_google_search", return_value=None)
    def test_missing_bib_year_does_not_block(self, *mocks):
        # Without a bib year we can't compare → don't reject
        ref = self._ref(year=None, title="Some Paper")
        result = process_reference(ref, metadata_only=False)
        assert "arxiv" in result["sources"]


class TestProcessAllWithCustomFn:
    def test_uses_custom_fn_when_provided(self):
        calls = []

        def custom_fn(ref):
            calls.append(ref["bib_key"])
            return {
                "bib_key": ref["bib_key"], "title": ref.get("title"),
                "authors": [], "year": None, "journal": None, "doi": None,
                "abstract": None, "pdf_url": None, "url": None,
                "citation_count": None, "sources": ["custom"],
                "status": "not_found", "error": None, "raw_bib": None,
            }

        refs = [
            {"bib_key": "a", "title": "A", "status": None},
            {"bib_key": "b", "title": "B", "status": None},
        ]
        results = process_all(refs, process_fn=custom_fn, max_workers=1)
        assert len(results) == 2
        assert set(calls) == {"a", "b"}
        for r in results:
            assert "custom" in r["sources"]


# ============================================================
# Integration: bib URL download + metadata-only flow
# ============================================================

class TestBibUrlIntegration:
    """Simulates the end-to-end flow: ref with bib URL → pre-download → metadata_only → download_reference_files."""

    def test_successful_bib_url_skips_api_url_discovery(self, tmp_path):
        from file_downloader import download_reference_files
        bib_key = "myref2024"
        project_dir = str(tmp_path)
        bib_url = "https://example.com/article"

        # Step 1: pre-download succeeds
        with patch("file_downloader._download_page") as mock_dl:
            def fake(url, path, **kwargs):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("<html><body><p>Full article body that is long enough for the extraction.</p></body></html>")
                return True
            mock_dl.side_effect = fake
            pre_files = pre_download_bib_url(project_dir, bib_key, bib_url)
        assert pre_files.get("page")

        # Step 2: process_reference in metadata_only mode
        ref = {"bib_key": bib_key, "title": "My Paper", "doi": None,
               "authors": "", "year": "2024", "journal": None,
               "url": bib_url, "arxiv_id": None, "entry_type": "article",
               "status": None, "raw_bib": "@article{myref2024, title={My Paper}}"}
        with patch("lookup_engine.lookup_crossref", return_value=None), \
             patch("lookup_engine.lookup_unpaywall", return_value=None), \
             patch("lookup_engine.lookup_openalex", return_value=None), \
             patch("lookup_engine.lookup_semantic_scholar", return_value={"abstract": "API abstract"}), \
             patch("lookup_engine.search_arxiv", return_value=None), \
             patch("lookup_engine.lookup_wikipedia", return_value=None), \
             patch("lookup_engine.lookup_scholarly", return_value=None), \
             patch("lookup_engine.lookup_google_search", return_value=None):
            result = process_reference(ref, metadata_only=True)

        # Bib URL preserved; API abstract cleared (downloaded content is the text)
        assert result["url"] == bib_url
        assert result["pdf_url"] is None
        assert result["abstract"] is None

        # Step 3: download_reference_files — pre-downloaded file already on disk
        files = download_reference_files(project_dir, bib_key, result)
        assert files.get("page"), "pre-downloaded HTML page must be picked up"
        assert files.get("md"), ".md must be built from the HTML body"

    def test_failed_bib_url_short_circuits_to_unreachable(self, tmp_path):
        """If bib URL download fails (4xx/network), produce a bib_url_unreachable
        result WITHOUT running the lookup pipeline.

        Regression: ManTrendFollowing case. The bib URL was a Man Group corporate page
        that 403's to bots. The old behavior fell through to a title-only API search,
        which found unrelated arXiv papers titled "Trend-Following" and downloaded them
        as the "source" — completely wrong content presented as the citation."""
        from lookup_engine import make_bib_url_unreachable_result

        bib_key = "ManTrendFollowing"
        project_dir = str(tmp_path)
        bib_url = "https://www.man.com/trend-following"

        # Pre-download fails with 403 (Man Group corporate site)
        def page_403(url, path, status_out=None):
            if status_out is not None:
                status_out["http_status"] = 403
                status_out["kind"] = "http_4xx"
            return False
        with patch("file_downloader._download_page", side_effect=page_403):
            pre = pre_download_bib_url(project_dir, bib_key, bib_url)
        assert pre.get("error") is True
        assert pre.get("http_status") == 403

        # Build the unreachable result directly (this is what app.py does instead
        # of calling process_reference)
        ref = {"bib_key": bib_key, "title": "Trend-Following", "doi": None,
               "authors": "Man Group", "year": "2026", "journal": None,
               "url": bib_url, "arxiv_id": None, "entry_type": "misc",
               "status": None, "raw_bib": "@misc{ManTrendFollowing, title={Trend-Following}}"}
        result = make_bib_url_unreachable_result(ref, pre)

        assert result["status"] == "bib_url_unreachable"
        assert result["url"] == bib_url
        assert result["pdf_url"] is None
        assert result["abstract"] is None
        # Title and bib metadata preserved so the user can identify the citation
        assert result["title"] == "Trend-Following"
        assert result["raw_bib"]
        # Error message includes the HTTP status so the user knows what went wrong
        assert "403" in result["error"]
        # Failure detail preserved for debugging
        assert result["bib_url_failure"]["http_status"] == 403


class TestMakeBibUrlUnreachableResult:
    """Helper that constructs the result dict for unreachable bib URLs."""

    def _ref(self, **overrides):
        ref = {
            "bib_key": "k", "title": "T", "doi": None,
            "authors": "Smith, J.", "year": "2024", "journal": "J",
            "url": "https://example.com/broken", "arxiv_id": None,
            "entry_type": "article", "status": None,
            "raw_bib": "@article{k, title={T}}",
        }
        ref.update(overrides)
        return ref

    def test_http_4xx_message(self):
        from lookup_engine import make_bib_url_unreachable_result
        r = make_bib_url_unreachable_result(self._ref(),
            {"http_status": 403, "kind": "http_4xx", "url": "https://example.com/broken"})
        assert r["status"] == "bib_url_unreachable"
        assert "403" in r["error"]
        assert r["bib_url_failure"]["kind"] == "http_4xx"

    def test_network_error_message(self):
        from lookup_engine import make_bib_url_unreachable_result
        r = make_bib_url_unreachable_result(self._ref(),
            {"http_status": None, "kind": "network"})
        assert "network" in r["error"].lower() or "unreachable" in r["error"].lower()

    def test_preserves_authors_as_list(self):
        from lookup_engine import make_bib_url_unreachable_result
        # String author -> list (matches the schema other results use)
        r = make_bib_url_unreachable_result(
            self._ref(authors="Single Author"),
            {"http_status": 403, "kind": "http_4xx"})
        assert r["authors"] == ["Single Author"]

        r = make_bib_url_unreachable_result(
            self._ref(authors=["A", "B"]),
            {"http_status": 403, "kind": "http_4xx"})
        assert r["authors"] == ["A", "B"]

    def test_no_lookup_apis_called(self):
        """Sanity check: building the unreachable result must not call any API."""
        from lookup_engine import make_bib_url_unreachable_result
        # Patch every lookup function — none should be invoked
        with patch("lookup_engine.lookup_crossref") as cr, \
             patch("lookup_engine.lookup_unpaywall") as uw, \
             patch("lookup_engine.lookup_openalex") as oa, \
             patch("lookup_engine.lookup_semantic_scholar") as s2, \
             patch("lookup_engine.search_arxiv") as ax, \
             patch("lookup_engine.lookup_wikipedia") as wk, \
             patch("lookup_engine.lookup_scholarly") as sch, \
             patch("lookup_engine.lookup_google_search") as gs:
            make_bib_url_unreachable_result(self._ref(),
                {"http_status": 403, "kind": "http_4xx"})
        cr.assert_not_called(); uw.assert_not_called(); oa.assert_not_called()
        s2.assert_not_called(); ax.assert_not_called(); wk.assert_not_called()
        sch.assert_not_called(); gs.assert_not_called()
