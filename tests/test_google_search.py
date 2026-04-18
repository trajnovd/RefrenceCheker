"""Tests for api_clients.google_search — domain filtering + surname helper."""

from unittest.mock import patch, MagicMock
import pytest

from api_clients.google_search import (
    _parse_results, _first_author_last_name, _is_noncontent_url,
    _extract_doc_id,
)


# ============================================================
# Non-content domain filter
# ============================================================

class TestFragilePdfFilter:
    """Google Search must not return PDFs on bot-blocked publisher domains as
    pdf_url candidates — they'll fail at download time. Fall through to a
    non-fragile mirror instead."""

    def test_oxford_academic_pdf_skipped(self):
        from api_clients.google_search import _is_fragile_pdf_url
        assert _is_fragile_pdf_url("https://academic.oup.com/rfs/article-pdf/29/1/5/hhv059.pdf")

    def test_wiley_ssrn_jstor_skipped(self):
        from api_clients.google_search import _is_fragile_pdf_url
        assert _is_fragile_pdf_url("https://onlinelibrary.wiley.com/doi/pdf/10.x")
        assert _is_fragile_pdf_url("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1")
        assert _is_fragile_pdf_url("https://www.jstor.org/stable/pdf/12345.pdf")

    def test_nber_university_arxiv_not_fragile(self):
        from api_clients.google_search import _is_fragile_pdf_url
        assert not _is_fragile_pdf_url("https://www.nber.org/system/files/working_papers/w20592/w20592.pdf")
        assert not _is_fragile_pdf_url("https://people.duke.edu/~charvey/Research/P118.PDF")
        assert not _is_fragile_pdf_url("https://arxiv.org/pdf/2306.06031")

    def test_parse_results_skips_fragile_for_pdf_url(self):
        """When Google's #1 PDF is on a fragile domain, fall through to the next item."""
        data = _fake_cse_response([
            ("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314.pdf",
             "and the Cross-Section of Expected Returns",
             "Snippet content describing this important paper from the journal"),
            ("https://people.duke.edu/~charvey/P118_and_the_cross.PDF",
             "and the Cross-Section of Expected Returns - Duke",
             "Snippet content from a university author homepage"),
        ])
        r = _parse_results(data, "and the Cross-Section of Expected Returns")
        assert r is not None
        assert "duke.edu" in r["pdf_url"]
        assert "ssrn" not in r["pdf_url"]


class TestNonContentFilter:
    def test_amazon_blocked(self):
        assert _is_noncontent_url("https://www.amazon.com/book/dp/12345")
        assert _is_noncontent_url("https://amazon.co.uk/book/dp/12345")

    def test_goodreads_blocked(self):
        assert _is_noncontent_url("https://www.goodreads.com/book/show/1578029")

    def test_ebay_blocked(self):
        assert _is_noncontent_url("https://www.ebay.com/itm/12345")
        assert _is_noncontent_url("https://ebay.de/itm/12345")

    def test_abebooks_alibris_blocked(self):
        assert _is_noncontent_url("https://www.abebooks.com/book/9780195301649")
        assert _is_noncontent_url("https://www.alibris.com/book")

    def test_arxiv_not_blocked(self):
        assert not _is_noncontent_url("https://arxiv.org/abs/2306.06031")

    def test_publisher_not_blocked(self):
        assert not _is_noncontent_url("https://press.princeton.edu/chapters/s6558.pdf")
        assert not _is_noncontent_url("https://academic.oup.com/rfs/article-pdf/29/1/5/hhv059.pdf")

    def test_researchgate_not_blocked(self):
        # researchgate occasionally has PDFs; don't blanket-block
        assert not _is_noncontent_url("https://www.researchgate.net/publication/12345")

    def test_empty_and_malformed(self):
        assert _is_noncontent_url("")
        assert _is_noncontent_url(None)


# ============================================================
# _parse_results — filters apply in integration
# ============================================================

def _fake_cse_response(items):
    """Build a minimal Google CSE response with the given (link, title, snippet) tuples."""
    return {"items": [{"link": l, "title": t, "snippet": s} for l, t, s in items]}


class TestParseResultsFiltering:
    def test_amazon_top_result_is_skipped(self):
        """Regression: Hasbrouck 2007 book case. Google's top result is Amazon — must skip."""
        data = _fake_cse_response([
            ("https://www.amazon.com/Empirical-Market-Microstructure/dp/0195301641",
             "Empirical Market Microstructure", "Book listing on Amazon"),
            ("https://www.researchgate.net/publication/254441114",
             "Empirical Market Microstructure",
             "An important book on market microstructure with detailed coverage of empirical methods and applications to institutional trading."),
        ])
        r = _parse_results(data, "Empirical Market Microstructure")
        assert r is not None
        assert "amazon.com" not in r["url"]
        assert "researchgate.net" in r["url"]

    def test_all_commercial_returns_none(self):
        """If every item is a commerce domain, we return nothing rather than a useless url."""
        data = _fake_cse_response([
            ("https://www.amazon.com/x/dp/1", "Some Book", "snippet"),
            ("https://www.goodreads.com/book/show/2", "Some Book", "snippet"),
            ("https://www.barnesandnoble.com/w/3", "Some Book", "snippet"),
        ])
        r = _parse_results(data, "Some Book")
        assert r is None

    def test_publisher_pdf_preferred_over_shop_page(self):
        """Academic publishers with free chapters should win over commercial pages."""
        data = _fake_cse_response([
            ("https://www.amazon.com/book/dp/1", "Walk Down Wall Street", "amazon snippet"),
            ("http://press.princeton.edu/chapters/s6558.pdf",
             "Walk Down Wall Street",
             "Free chapter preview from Princeton University Press"),
        ])
        r = _parse_results(data, "Walk Down Wall Street")
        assert r is not None
        assert r["pdf_url"] == "http://press.princeton.edu/chapters/s6558.pdf"


# ============================================================
# Surname extraction
# ============================================================

class TestDocIdExtraction:
    """Document identifiers like SEC press release numbers and Fed SR letters
    need to be extracted from bib's number/note fields so we can search by them
    when the user-composed title doesn't index well."""

    def test_sec_press_release_in_number_field(self):
        assert _extract_doc_id(number_field="Press Release 2024-137") == "2024-137"

    def test_fed_sr_letter_in_number_field(self):
        assert _extract_doc_id(number_field="SR 11-7") == "SR 11-7"

    def test_year_dash_id_in_note(self):
        assert _extract_doc_id(note_field="See 2010-19193 for details") == "2010-19193"

    def test_no_pattern_returns_none(self):
        assert _extract_doc_id(number_field="5", note_field="June 2024") is None

    def test_falls_through_sources(self):
        # Found in title when number/note don't match
        assert _extract_doc_id(number_field=None, note_field=None,
                                title="Comments on SR 11-7 framework") == "SR 11-7"

    def test_handles_none_inputs(self):
        assert _extract_doc_id() is None
        assert _extract_doc_id(None, None, None) is None


class TestCorporateAuthorDetection:
    """Corporate/institutional authors should not produce a 'surname' keyword,
    because their last word ('System', 'Reserve', etc.) is not a name and degrades
    Google search results. Regression for SR 11-7 reference."""

    def test_federal_reserve_corporate(self):
        # 'Board of Governors of the Federal Reserve System' → System is wrong surname
        assert _first_author_last_name("Board of Governors of the Federal Reserve System") is None

    def test_simple_personal_name_unaffected(self):
        assert _first_author_last_name("Andrew W. Lo") == "Lo"
        assert _first_author_last_name("White, Halbert") == "White"

    def test_various_corporate_forms_detected(self):
        assert _first_author_last_name("U.S. Securities and Exchange Commission") is None
        assert _first_author_last_name("International Monetary Fund") is None
        assert _first_author_last_name("Bank for International Settlements") is None
        assert _first_author_last_name("Acme Corp") is None
        assert _first_author_last_name("OpenAI Inc") is None

    def test_single_corporate_keyword_in_personal_name_ambiguous(self):
        # 'Bank' is a corporate token; this would (over-)detect "John Bank" as corporate.
        # Acceptable false-positive: the system search just falls through to title-only.
        assert _first_author_last_name("John Bank") is None


class TestSurnameExtractor:
    def test_list_form(self):
        assert _first_author_last_name(["Andrew W. Lo", "A. Craig MacKinlay"]) == "Lo"

    def test_and_string(self):
        assert _first_author_last_name("Andrew W. Lo and A. Craig MacKinlay") == "Lo"

    def test_last_first_form(self):
        assert _first_author_last_name("Lo, Andrew W. and MacKinlay, A. Craig") == "Lo"

    def test_semicolon_list(self):
        assert _first_author_last_name("Smith; Jones; Brown") == "Smith"

    def test_bibtex_braces(self):
        assert _first_author_last_name("{Howard} Forbes") == "Forbes"

    def test_none_empty(self):
        assert _first_author_last_name(None) is None
        assert _first_author_last_name([]) is None
        assert _first_author_last_name("") is None
