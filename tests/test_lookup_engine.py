from unittest.mock import patch, MagicMock
from lookup_engine import process_reference, process_all


def test_process_reference_with_doi():
    ref = {"bib_key": "smith2020", "title": "ML Study", "doi": "10.1234/test",
           "authors": "Smith, John", "year": "2020", "journal": "J AI", "url": None, "status": None}

    crossref_data = {"title": "ML Study", "authors": ["John Smith"],
                     "journal": "J AI", "year": "2020", "url": "https://doi.org/10.1234/test"}
    unpaywall_data = {"is_oa": True, "pdf_url": "https://example.com/paper.pdf"}
    s2_data = {"title": "ML Study", "abstract": "Studies ML.", "year": "2020",
               "citation_count": 42, "pdf_url": None, "authors": ["John Smith"], "doi": "10.1234/test"}

    with patch("lookup_engine.lookup_crossref", return_value=crossref_data), \
         patch("lookup_engine.lookup_unpaywall", return_value=unpaywall_data), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=s2_data):
        result = process_reference(ref)

    assert result["status"] == "found_pdf"
    assert result["abstract"] == "Studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert "crossref" in result["sources"]


def test_process_reference_abstract_only():
    ref = {"bib_key": "jones2019", "title": "DL in Practice", "doi": None,
           "authors": "Jones, Bob", "year": "2019", "journal": None, "url": None, "status": None}

    s2_data = {"title": "DL in Practice", "abstract": "Deep learning stuff.",
               "year": "2019", "citation_count": 10, "pdf_url": None,
               "authors": ["Bob Jones"], "doi": None}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=s2_data), \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    assert result["status"] == "found_abstract"
    assert result["abstract"] == "Deep learning stuff."


def test_process_reference_not_found():
    ref = {"bib_key": "unknown", "title": "Unknown Paper", "doi": None,
           "authors": "", "year": None, "journal": None, "url": None, "status": None}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    assert result["status"] == "not_found"


def test_process_reference_insufficient_data():
    ref = {"bib_key": "empty", "title": None, "doi": None,
           "authors": "", "year": None, "journal": None, "url": None, "status": "insufficient_data"}
    result = process_reference(ref)
    assert result["status"] == "insufficient_data"


def test_process_reference_preserves_raw_bib():
    """raw_bib must survive process_reference so the BibTeX tab keeps working
    after Refresh / Add Reference (regression: was stripped, leaving the
    BibTeX tab dimmed after manual operations)."""
    ref = {"bib_key": "smith2020", "title": None, "doi": None,
           "authors": "", "year": None, "journal": None, "url": None,
           "status": "insufficient_data",
           "raw_bib": "@article{smith2020, title={X}, year={2020}}"}
    result = process_reference(ref)
    assert result.get("raw_bib") == "@article{smith2020, title={X}, year={2020}}"


class TestMakeUrlSourceResult:
    """make_url_source_result builds a clean result for refs whose non-DOI bib
    URL pre-downloaded successfully — bypasses the API to prevent vanity-title
    false matches (CitadelSecuritiesWhatWeDo regression)."""

    def test_html_url_yields_found_web_page(self):
        from lookup_engine import make_url_source_result
        ref = {"bib_key": "k", "title": "What We Do", "year": None,
               "doi": None, "url": "https://www.citadelsecurities.com/what-we-do/",
               "authors": "Citadel Securities", "journal": None,
               "raw_bib": "@misc{k, ...}"}
        r = make_url_source_result(ref)
        assert r["status"] == "found_web_page"
        assert r["pdf_url"] is None
        assert r["url"] == "https://www.citadelsecurities.com/what-we-do/"
        assert r["sources"] == ["URL"]
        # Bib identity preserved verbatim
        assert r["title"] == "What We Do"
        assert r["authors"] == ["Citadel Securities"]
        # No API enrichment can leak in
        assert r["doi"] is None
        assert r["abstract"] is None
        assert r["citation_count"] is None
        assert "pdf_url_fallbacks" not in r
        # Hard contract — download_reference_files must not search for a PDF
        assert r["url_source_only"] is True

    def test_pdf_url_yields_found_pdf(self):
        from lookup_engine import make_url_source_result
        ref = {"bib_key": "k", "title": "T", "year": "2024", "doi": None,
               "url": "https://example.com/paper.pdf", "authors": ["A. Author"],
               "journal": None, "raw_bib": ""}
        r = make_url_source_result(ref)
        assert r["status"] == "found_pdf"
        assert r["pdf_url"] == "https://example.com/paper.pdf"

    def test_arxiv_abs_url_normalized_to_pdf(self):
        """Same URL normalization as pre_download_bib_url so result.pdf_url
        points at the actual file on disk."""
        from lookup_engine import make_url_source_result
        ref = {"bib_key": "k", "title": "T", "year": "2024", "doi": None,
               "url": "https://arxiv.org/abs/2308.00016", "authors": "A",
               "journal": None, "raw_bib": ""}
        r = make_url_source_result(ref)
        assert r["status"] == "found_pdf"
        assert "/pdf/" in r["pdf_url"]

    def test_authors_list_preserved(self):
        from lookup_engine import make_url_source_result
        ref = {"bib_key": "k", "title": "T", "year": None, "doi": None,
               "url": "https://x.com/p", "authors": ["A. Author", "B. Author"],
               "journal": None, "raw_bib": ""}
        r = make_url_source_result(ref)
        assert r["authors"] == ["A. Author", "B. Author"]


def test_doi_promoted_from_url_when_doi_field_empty():
    """Regression: kirilenko2017flash. The bib has only `url={https://doi.org/...}`
    and no doi field, so old saved parsed_refs in project.json have doi=None.
    process_reference must still extract the DOI so the DOI-driven steps
    (CrossRef, Unpaywall, OpenAlex by DOI) actually run."""
    ref = {"bib_key": "kirilenko2017", "title": "Flash Crash", "doi": None,
           "authors": "Kirilenko", "year": "2017", "journal": "JF", "url":
           "https://doi.org/10.1111/jofi.12498", "status": None,
           "entry_type": "article"}

    crossref_seen = {}
    def fake_cr(doi):
        crossref_seen["doi"] = doi
        return None

    with patch("lookup_engine.lookup_crossref", side_effect=fake_cr) as cr, \
         patch("lookup_engine.lookup_unpaywall", return_value=None) as uw, \
         patch("lookup_engine.lookup_openalex", return_value=None), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
         patch("lookup_engine.search_arxiv", return_value=None), \
         patch("lookup_engine.lookup_google_search", return_value=None), \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        process_reference(ref)

    # CrossRef must have been called with the DOI promoted from the URL
    cr.assert_called_once_with("10.1111/jofi.12498")
    # Unpaywall too (only fires when DOI is present and arxiv_id isn't)
    uw.assert_called_with("10.1111/jofi.12498")


class TestArxivAnchorLockdown:
    """Regression: bahdanau2015neural. The bib has arxiv_id='1409.0473' (from
    note field). Step 0 sets pdf_url=arxiv.org/pdf/1409.0473 — but Step 4
    (Google Search) treated `inproceedings` as a book and overrode the URL
    with iclr.cc. Worse, OpenAlex matched the title to a different paper
    (Sennrich-Haddow-Birch's 'Neural MT of Rare Words') and overwrote the
    bib's authors. ref_match then reported the legitimately-correct download
    as 'not_matched' because the result.authors disagreed with the PDF.

    Rule: when arxiv_id is present, arxiv is the canonical source. No
    pdf_url overrides, no authors overrides, no Google Search."""

    def test_arxiv_id_locks_pdf_url_against_google_override(self):
        """inproceedings is in BOOK_TYPES — without the lockdown, Step 4 would
        replace the arxiv pdf_url with a Google-found mirror."""
        ref = {"bib_key": "bahdanau2015", "title": "Neural Machine Translation by Jointly Learning to Align and Translate",
               "doi": None, "arxiv_id": "1409.0473", "year": "2015",
               "authors": "Bahdanau, Dzmitry and Cho, Kyunghyun and Bengio, Yoshua",
               "journal": "ICLR", "url": None, "status": None,
               "entry_type": "inproceedings"}
        # OpenAlex returns enrichment but with WRONG paper authors (Sennrich)
        oa_data = {"abstract": "An abstract", "citation_count": 29216, "doi": None,
                   "pdf_url": "https://example.com/wrong.pdf",
                   "authors": ["Rico Sennrich", "Barry Haddow", "Alexandra Birch"],
                   "year": "2015"}
        with patch("lookup_engine.lookup_crossref", return_value=None), \
             patch("lookup_engine.lookup_unpaywall", return_value=None), \
             patch("lookup_engine.lookup_openalex", return_value=oa_data), \
             patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
             patch("lookup_engine.search_arxiv", return_value=None), \
             patch("lookup_engine.lookup_google_search") as gs, \
             patch("lookup_engine.lookup_scholarly", return_value=None):
            result = process_reference(ref)

        # pdf_url must remain the arxiv URL, NOT replaced by the wrong OpenAlex one
        assert result["pdf_url"] == "https://arxiv.org/pdf/1409.0473"
        # Google Search must NOT have been called (arxiv is the source)
        gs.assert_not_called()

    def test_arxiv_id_preserves_bib_authors(self):
        """OpenAlex's wrong-paper authors must NOT replace the bib's authors —
        the bib is ground truth when arxiv_id anchors identity."""
        ref = {"bib_key": "bahdanau2015", "title": "Neural Machine Translation",
               "doi": None, "arxiv_id": "1409.0473", "year": "2015",
               "authors": "Bahdanau, Dzmitry and Cho, Kyunghyun and Bengio, Yoshua",
               "journal": None, "url": None, "status": None,
               "entry_type": "inproceedings"}
        oa_data = {"abstract": None, "citation_count": 100,
                   "authors": ["Rico Sennrich", "Barry Haddow", "Alexandra Birch"],
                   "year": "2015"}
        with patch("lookup_engine.lookup_crossref", return_value=None), \
             patch("lookup_engine.lookup_unpaywall", return_value=None), \
             patch("lookup_engine.lookup_openalex", return_value=oa_data), \
             patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
             patch("lookup_engine.search_arxiv", return_value=None), \
             patch("lookup_engine.lookup_google_search", return_value=None), \
             patch("lookup_engine.lookup_scholarly", return_value=None):
            result = process_reference(ref)

        # Bib authors preserved (still wrapped as a single-string list — that's
        # the original bib state, ref_match knows how to handle it)
        bib_author_str = "Bahdanau, Dzmitry and Cho, Kyunghyun and Bengio, Yoshua"
        assert result["authors"] == [bib_author_str]
        # Definitely not the wrong Sennrich list
        assert "Sennrich" not in str(result["authors"])

    def test_arxiv_id_still_enriches_citation_count_and_abstract(self):
        """Lockdown only blocks identity overrides — safe enrichment continues."""
        ref = {"bib_key": "k", "title": "Some Paper", "doi": None,
               "arxiv_id": "1409.0473", "year": "2015",
               "authors": "Author, A.", "journal": None, "url": None,
               "status": None, "entry_type": "inproceedings"}
        oa_data = {"abstract": "An OpenAlex abstract.", "citation_count": 42,
                   "authors": ["Wrong, Person"], "year": "2015"}
        with patch("lookup_engine.lookup_crossref", return_value=None), \
             patch("lookup_engine.lookup_unpaywall", return_value=None), \
             patch("lookup_engine.lookup_openalex", return_value=oa_data), \
             patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
             patch("lookup_engine.search_arxiv", return_value=None), \
             patch("lookup_engine.lookup_google_search", return_value=None), \
             patch("lookup_engine.lookup_scholarly", return_value=None):
            result = process_reference(ref)

        assert result["abstract"] == "An OpenAlex abstract."
        assert result["citation_count"] == 42


def test_book_triggers_google_search_override():
    """Regression: russell2020artificial. OpenAlex matched the wrong work and
    handed back a bogus Zenodo URL; Google Search wasn't called because a
    pdf_url was set. For BOOK types we always want Google to run and override
    the OpenAlex pick when it finds a non-fragile mirror (course page, author
    site)."""
    ref = {"bib_key": "russell2020", "title": "Artificial Intelligence: A Modern Approach",
           "doi": None, "authors": "Russell, Stuart and Norvig, Peter", "year": "2020",
           "journal": None, "url": None, "status": None, "entry_type": "book"}
    oa_data = {"abstract": "AI textbook.", "citation_count": 22244, "doi": "10.5860/wrong",
               "pdf_url": "https://doi.org/10.5281/zenodo.bogus", "authors": ["Russell"],
               "year": "2020", "pdf_url_fallbacks": []}
    s2_data = {"title": "AI Modern Approach 3rd Ed", "abstract": None, "year": "2020",
               "citation_count": 1117, "pdf_url": None, "authors": ["Wrong, Author"]}
    google_hit = {"url": "https://people.engr.tamu.edu/guni/csce625/",
                  "pdf_url": "https://people.engr.tamu.edu/guni/csce625/slides/AI.pdf",
                  "abstract": None}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_openalex", return_value=oa_data), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=s2_data), \
         patch("lookup_engine.search_arxiv", return_value=None), \
         patch("lookup_engine.lookup_wikipedia", return_value=None), \
         patch("lookup_engine.lookup_google_search", return_value=google_hit) as gs, \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    # Google Search MUST have been called (was previously skipped because pdf_url was set)
    gs.assert_called_once()
    # The bogus OpenAlex URL must have been overridden by the TAMU mirror
    assert result["pdf_url"] == "https://people.engr.tamu.edu/guni/csce625/slides/AI.pdf"
    assert "google_search" in result["sources"]


def test_article_with_pdf_url_skips_google_search():
    """Articles with a working pdf_url should NOT trigger Google Search — that
    would burn API quota for every successful direct hit. Only books bypass
    the gate."""
    ref = {"bib_key": "vaswani2017", "title": "Attention Is All You Need", "doi": None,
           "authors": "Vaswani, Ashish", "year": "2017", "journal": None, "url": None,
           "status": None, "entry_type": "article"}
    oa_data = {"abstract": "Transformers.", "citation_count": 100000, "doi": None,
               "pdf_url": "https://arxiv.org/pdf/1706.03762", "authors": ["Vaswani"],
               "year": "2017"}

    with patch("lookup_engine.lookup_crossref", return_value=None), \
         patch("lookup_engine.lookup_unpaywall", return_value=None), \
         patch("lookup_engine.lookup_openalex", return_value=oa_data), \
         patch("lookup_engine.lookup_semantic_scholar", return_value=None), \
         patch("lookup_engine.search_arxiv", return_value=None), \
         patch("lookup_engine.lookup_google_search") as gs, \
         patch("lookup_engine.lookup_scholarly", return_value=None):
        result = process_reference(ref)

    gs.assert_not_called()
    assert result["pdf_url"] == "https://arxiv.org/pdf/1706.03762"


def test_process_all_calls_callback():
    refs = [
        {"bib_key": "a", "title": "Paper A", "doi": None,
         "authors": "", "year": None, "journal": None, "url": None, "status": None},
    ]
    callback = MagicMock()
    with patch("lookup_engine.process_reference", return_value={
        "bib_key": "a", "status": "not_found", "sources": []
    }):
        process_all(refs, callback=callback, max_workers=1)
    callback.assert_called_once()
