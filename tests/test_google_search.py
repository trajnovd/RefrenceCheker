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

    def test_wiley_jstor_skipped(self):
        from api_clients.google_search import _is_fragile_pdf_url
        assert _is_fragile_pdf_url("https://onlinelibrary.wiley.com/doi/pdf/10.x")
        assert _is_fragile_pdf_url("https://www.jstor.org/stable/pdf/12345.pdf")

    def test_ssrn_no_longer_skipped(self):
        """v6.5 reclassified SSRN: removed from FRAGILE_PDF_DOMAINS so the
        Google parser surfaces SSRN PDFs and the rescue tier attempts them.
        The orchestrator's force_tier=curl_cffi rule (see test_ssrn_routes_to_curl_cffi
        below) routes the actual download through TLS impersonation, which
        defeats SSRN's Cloudflare protection. End result: SSRN-only papers
        (e.g. kirilenko2017flash) are now downloadable end-to-end."""
        from api_clients.google_search import _is_fragile_pdf_url
        assert not _is_fragile_pdf_url("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1")

    def test_nber_university_arxiv_not_fragile(self):
        from api_clients.google_search import _is_fragile_pdf_url
        assert not _is_fragile_pdf_url("https://www.nber.org/system/files/working_papers/w20592/w20592.pdf")
        assert not _is_fragile_pdf_url("https://people.duke.edu/~charvey/Research/P118.PDF")
        assert not _is_fragile_pdf_url("https://arxiv.org/pdf/2306.06031")

    def test_parse_results_skips_fragile_for_pdf_url(self):
        """When Google's #1 PDF is on a fragile domain, fall through to the next item.
        Uses Wiley as the still-fragile example — SSRN no longer qualifies (v6.5)."""
        data = _fake_cse_response([
            ("https://onlinelibrary.wiley.com/doi/pdf/10.1111/jofi.12498",
             "and the Cross-Section of Expected Returns",
             "Snippet content describing this important paper from the journal"),
            ("https://people.duke.edu/~charvey/P118_and_the_cross.PDF",
             "and the Cross-Section of Expected Returns - Duke",
             "Snippet content from a university author homepage"),
        ])
        r = _parse_results(data, "and the Cross-Section of Expected Returns")
        assert r is not None
        assert "duke.edu" in r["pdf_url"]
        assert "wiley.com" not in r["pdf_url"]


class TestHtmlPaywallSkippedForBestUrl:
    """Regression: Hasbrouck2007 — Google ranked the ResearchGate teaser page
    #1, our parser accepted it as `best_url`, _download_page saved the teaser,
    ref_match flagged it as wrong. The parser must now skip HTML-paywall
    hosts (RG, JSTOR, Wiley landing pages) when picking best_url and fall
    through to the next result."""

    def test_researchgate_skipped_in_favor_of_author_homepage(self):
        data = _fake_cse_response([
            ("https://www.researchgate.net/publication/254441114_Empirical_Market_Microstructure",
             "Empirical Market Microstructure",
             "Description of the book and its contents"),
            ("https://pages.stern.nyu.edu/~jhasbrou/Research/EMS-book-chapter.pdf",
             "Empirical Market Microstructure - NYU author page",
             "Author's homepage hosted PDF chapter excerpt"),
        ])
        r = _parse_results(data, "Empirical Market Microstructure")
        assert r is not None
        assert "researchgate.net" not in (r["url"] or "")
        assert "stern.nyu.edu" in (r["url"] or "")

    def test_jstor_skipped_for_best_url(self):
        """JSTOR landing page must NOT be picked as the page URL."""
        data = _fake_cse_response([
            ("https://www.jstor.org/stable/4479463",
             "A short history of program trading",
             "JSTOR teaser snippet"),
            ("https://www.cfainstitute.org/-/media/documents/article/faj/program-trading.pdf",
             "A short history of program trading - CFA Institute",
             "Open access mirror snippet"),
        ])
        r = _parse_results(data, "A short history of program trading")
        assert r is not None
        assert "jstor.org" not in (r["url"] or "")
        assert "cfainstitute.org" in (r["url"] or "")

    def test_only_paywall_results_returns_no_url(self):
        """If Google returns nothing but paywall hosts, best_url stays None.
        Better than saving captcha/teaser content."""
        data = _fake_cse_response([
            ("https://www.researchgate.net/publication/x",
             "Some Paper", "RG teaser"),
            ("https://www.jstor.org/stable/x",
             "Some Paper - JSTOR", "JSTOR teaser"),
        ])
        r = _parse_results(data, "Some Paper")
        # Both pdf_url and url end up unusable → parser returns None
        assert r is None or not r.get("url")

    def test_google_books_skipped_for_best_url(self):
        """Google Books snippet view isn't useful as content — skip it,
        fall through to a real-content host."""
        data = _fake_cse_response([
            ("https://books.google.com/books?id=abc123",
             "Empirical Market Microstructure - Google Books",
             "Snippet view of the book"),
            ("https://pages.stern.nyu.edu/~jhasbrou/Research/empirical.pdf",
             "Empirical Market Microstructure - NYU",
             "Author homepage chapter"),
        ])
        r = _parse_results(data, "Empirical Market Microstructure")
        assert r is not None
        assert "books.google.com" not in (r["url"] or "")
        assert "stern.nyu.edu" in (r["url"] or "")

    def test_pdf_link_on_paywall_host_still_filtered_via_fragile_list(self):
        """The fragile-pdf filter handles PDF URLs on Wiley/JSTOR. The new
        html-paywall filter is for PAGE URLs. They're complementary; neither
        regresses the other."""
        data = _fake_cse_response([
            ("https://www.jstor.org/stable/pdf/4479463.pdf",
             "A short history of program trading",
             "JSTOR PDF teaser"),
            ("https://people.duke.edu/charvey/program-trading.pdf",
             "A short history of program trading - Duke",
             "Author homepage PDF"),
        ])
        r = _parse_results(data, "A short history of program trading")
        assert r is not None
        # Duke wins for both pdf_url AND best_url
        assert "duke.edu" in (r["pdf_url"] or "")
        assert "duke.edu" in (r["url"] or "")


class TestSsrnRoutingToCurlCffi:
    """SSRN was removed from FRAGILE_PDF_DOMAINS so the parser/rescue surface
    SSRN URLs as PDF candidates. The orchestrator MUST then route those
    downloads to curl_cffi (TLS impersonation defeats SSRN's Cloudflare).
    This pins the contract from the orchestrator side so a future accidental
    change to BUILTIN_RULES can't silently break SSRN downloads."""

    def test_ssrn_url_routes_to_curl_cffi(self):
        from file_downloader_fallback import _resolve_force_tier
        assert _resolve_force_tier(
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1686004"
        ) == "curl_cffi"

    def test_ssrn_subpath_still_routes_to_curl_cffi(self):
        from file_downloader_fallback import _resolve_force_tier
        # Suffix-match must hold for any SSRN URL shape.
        assert _resolve_force_tier(
            "https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1686004_code123.pdf"
        ) == "curl_cffi"

    def test_unrelated_host_does_not_force_curl_cffi(self):
        from file_downloader_fallback import _resolve_force_tier
        # Sanity: arXiv must NOT be forced through curl_cffi.
        assert _resolve_force_tier("https://arxiv.org/pdf/1409.0473") is None


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
        """Regression: Hasbrouck 2007 book case. Google's top result is Amazon — must skip
        and fall through to a real-content host. (RG used to be the fallback here
        but is now in HTML_PAYWALL_HOSTS — see TestHtmlPaywallSkippedForBestUrl.)"""
        data = _fake_cse_response([
            ("https://www.amazon.com/Empirical-Market-Microstructure/dp/0195301641",
             "Empirical Market Microstructure", "Book listing on Amazon"),
            ("https://pages.stern.nyu.edu/~jhasbrou/Research/empirical-market-microstructure.html",
             "Empirical Market Microstructure - NYU author page",
             "Author's homepage with the book's table of contents and chapter excerpts."),
        ])
        r = _parse_results(data, "Empirical Market Microstructure")
        assert r is not None
        assert "amazon.com" not in r["url"]
        assert "stern.nyu.edu" in r["url"]

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


class TestRelevanceUsesTitleAndSnippet:
    """Regression: Hasbrouck2007EmpiricalMicrostructure. The Buffalo .edu PDF
    that Google ranks #1 for the manual search has filename `Hasbrouck's
    book.pdf` — Google's `title` field is essentially that filename, with
    ~0 overlap against the 11-word bib title. The relevance check must
    consider the SNIPPET (extracted PDF text) too, not just the title."""

    def test_buffalo_edu_pdf_accepted_via_snippet(self):
        """Cryptic title + snippet that contains the bib title words → accept."""
        data = _fake_cse_response([
            ("https://www.acsu.buffalo.edu/~keechung/MGF743/Readings/Hasbrouck's%20book.pdf",
             "Hasbrouck's book.pdf",   # title is just the filename
             # Snippet is rich — extracted from the PDF's first page
             "Empirical Market Microstructure: The Institutions, Economics, and "
             "Econometrics of Securities Trading. Joel Hasbrouck. Oxford University Press."),
        ])
        r = _parse_results(data, "Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading")
        assert r is not None
        assert "buffalo.edu" in (r["pdf_url"] or "")

    def test_long_unrelated_snippet_does_not_falsely_pass(self):
        """A snippet that doesn't contain the title words still gets rejected."""
        data = _fake_cse_response([
            ("https://example.com/random.pdf",
             "random.pdf",
             "This document is about something completely unrelated like cooking pasta and wine pairings."),
        ])
        r = _parse_results(data, "Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading")
        assert r is None

    def test_title_alone_still_passes_when_rich(self):
        """Backward compat: when the title alone has good overlap, no snippet needed."""
        data = _fake_cse_response([
            ("https://example.edu/paper.pdf",
             "Empirical Market Microstructure The Institutions Economics Econometrics",
             ""),  # empty snippet
        ])
        r = _parse_results(data, "Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading")
        assert r is not None

    def test_partial_overlap_in_title_plus_partial_in_snippet_combines(self):
        """Title has half the words, snippet contributes the rest → combined ≥ 50% accepts."""
        data = _fake_cse_response([
            ("https://example.edu/paper.pdf",
             "Empirical Market Microstructure",   # only 3 of 11 query words in title
             "Hasbrouck on the institutions, economics, and econometrics of securities trading."),
        ])
        r = _parse_results(data, "Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading")
        assert r is not None


class TestRelaxedLastResortPass:
    """Pass 3: when strict exact-phrase passes return nothing usable, drop
    the quotes around the title and let Google's relevance ranking surface
    candidates we'd otherwise miss. Anchor with the first-author surname
    so the search doesn't drift to unrelated papers.

    Regression: Hasbrouck2007 — strict passes returned only Amazon / RG /
    Google Books (all filtered by the parser); the relaxed pass surfaces
    Hasbrouck's NYU faculty page."""

    def test_relaxed_pass_fires_when_strict_passes_return_nothing(self):
        from api_clients.google_search import lookup_google_search
        # Mock _run_query: every strict (quoted) call returns None;
        # the relaxed (unquoted) call returns a real hit.
        from unittest.mock import patch
        calls = []
        def fake_run(query, *_args, **_kw):
            calls.append(query)
            if '"' in query:    # any exact-phrase query
                return None
            return {"url": "https://pages.stern.nyu.edu/~jhasbrou/", "pdf_url": None,
                    "abstract": "NYU faculty page snippet"}
        with patch("api_clients.google_search._run_query", side_effect=fake_run), \
             patch("api_clients.google_search._ENABLED", True):
            result = lookup_google_search(
                "Empirical Market Microstructure: The Institutions, Economics, and Econometrics",
                authors="Hasbrouck, Joel")
        assert result is not None
        assert "stern.nyu.edu" in result["url"]
        # Verify the LAST call was the relaxed bare-words query
        assert any('"' not in c and "Hasbrouck" in c for c in calls), \
            f"expected at least one un-quoted query with author name; got {calls}"

    def test_relaxed_pass_NOT_fired_when_strict_returns_real_url(self):
        """If strict passes already found a usable URL, don't waste another
        Google API call on the relaxed query."""
        from api_clients.google_search import lookup_google_search
        from unittest.mock import patch
        calls = []
        def fake_run(query, *_args, **_kw):
            calls.append(query)
            return {"url": "https://example.edu/paper.pdf",
                    "pdf_url": "https://example.edu/paper.pdf", "abstract": None}
        with patch("api_clients.google_search._run_query", side_effect=fake_run), \
             patch("api_clients.google_search._ENABLED", True):
            lookup_google_search("Some Paper", authors="Smith, John")
        # No call should be the relaxed bare-words form (i.e. no call should
        # have the title without quotes — every fired query had a quoted title).
        for c in calls:
            assert '"' in c, f"unexpected unquoted (relaxed) call: {c}"

    def test_relaxed_pass_skipped_without_authors(self):
        """Without an author surname, the relaxed query has no anchor and
        would drift wildly. Skip it."""
        from api_clients.google_search import lookup_google_search
        from unittest.mock import patch
        calls = []
        def fake_run(query, *_args, **_kw):
            calls.append(query)
            return None    # everything strict returns nothing
        with patch("api_clients.google_search._run_query", side_effect=fake_run), \
             patch("api_clients.google_search._ENABLED", True):
            lookup_google_search("Some Paper", authors=None)
        # No relaxed call should have been issued
        for c in calls:
            assert '"' in c, f"unexpected unquoted call without authors: {c}"

    def test_relaxed_pass_strips_punctuation_from_title(self):
        from api_clients.google_search import lookup_google_search
        from unittest.mock import patch
        calls = []
        def fake_run(query, *_args, **_kw):
            calls.append(query)
            return None
        with patch("api_clients.google_search._run_query", side_effect=fake_run), \
             patch("api_clients.google_search._ENABLED", True):
            lookup_google_search(
                "Empirical Market Microstructure: The Institutions, Economics",
                authors="Hasbrouck, Joel")
        # The relaxed query should have no colons, commas, or quotes
        relaxed = [c for c in calls if '"' not in c]
        assert relaxed, f"no relaxed query fired; got {calls}"
        for q in relaxed:
            assert ":" not in q
            assert "," not in q

    def test_relaxed_pass_merges_into_existing_strict_hit_without_overwriting(self):
        """If strict pass found a `url` but no `pdf_url`, the relaxed pass
        can fill in the pdf_url — but must NOT clobber the existing url."""
        from api_clients.google_search import lookup_google_search
        from unittest.mock import patch
        n = [0]
        def fake_run(query, *_args, **_kw):
            n[0] += 1
            if '"' in query:
                # First strict call: page URL but no PDF
                return {"url": "https://strict.example.edu/page",
                        "pdf_url": None, "abstract": None}
            return {"url": "https://relaxed.example.edu/page",
                    "pdf_url": "https://relaxed.example.edu/paper.pdf",
                    "abstract": None}
        with patch("api_clients.google_search._run_query", side_effect=fake_run), \
             patch("api_clients.google_search._ENABLED", True):
            r = lookup_google_search("Some Paper", authors="Smith, John")
        assert r is not None
        # Strict url survives
        assert r["url"] == "https://strict.example.edu/page"
        # Relaxed pdf_url got merged in
        assert r["pdf_url"] == "https://relaxed.example.edu/paper.pdf"
