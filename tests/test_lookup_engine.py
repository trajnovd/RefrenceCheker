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
