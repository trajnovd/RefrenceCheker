from unittest.mock import patch, Mock
from api_clients.crossref import lookup_crossref


def test_crossref_with_valid_doi():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "title": ["Machine Learning Study"],
            "author": [{"given": "John", "family": "Smith"}],
            "container-title": ["Journal of AI"],
            "published-print": {"date-parts": [[2020]]},
            "URL": "https://doi.org/10.1234/test"
        }
    }
    with patch("api_clients.crossref.requests.get", return_value=mock_resp):
        result = lookup_crossref("10.1234/test")
    assert result["title"] == "Machine Learning Study"
    assert result["authors"] == ["John Smith"]
    assert result["year"] == "2020"


def test_crossref_with_invalid_doi():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.crossref.requests.get", return_value=mock_resp):
        result = lookup_crossref("10.9999/nonexistent")
    assert result is None


def test_crossref_timeout():
    with patch("api_clients.crossref.requests.get", side_effect=Exception("timeout")):
        result = lookup_crossref("10.1234/test")
    assert result is None


from api_clients.unpaywall import lookup_unpaywall


def test_unpaywall_finds_oa_pdf():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/paper.pdf"
        }
    }
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.1234/test")
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["is_oa"] is True


def test_unpaywall_no_oa():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "is_oa": False,
        "best_oa_location": None
    }
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.1234/test")
    assert result["pdf_url"] is None


def test_unpaywall_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.unpaywall.requests.get", return_value=mock_resp):
        result = lookup_unpaywall("10.9999/fake")
    assert result is None


from api_clients.semantic_scholar import lookup_semantic_scholar


def test_s2_search_by_doi():
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "paperId": "abc123",
        "title": "Machine Learning Study",
        "abstract": "This paper studies ML.",
        "year": 2020,
        "citationCount": 42,
        "isOpenAccess": True,
        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
        "authors": [{"name": "John Smith"}],
        "externalIds": {"DOI": "10.1234/test"}
    }
    with patch("api_clients.semantic_scholar.requests.get", return_value=mock_resp):
        result = lookup_semantic_scholar(doi="10.1234/test")
    assert result["abstract"] == "This paper studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["citation_count"] == 42


def test_s2_search_by_title():
    search_resp = Mock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "data": [{
            "paperId": "abc123",
            "title": "Machine Learning Study",
            "year": 2020,
            "authors": [{"name": "John Smith"}]
        }]
    }
    detail_resp = Mock()
    detail_resp.status_code = 200
    detail_resp.json.return_value = {
        "paperId": "abc123",
        "title": "Machine Learning Study",
        "abstract": "This paper studies ML.",
        "year": 2020,
        "citationCount": 42,
        "isOpenAccess": False,
        "openAccessPdf": None,
        "authors": [{"name": "John Smith"}],
        "externalIds": {"DOI": "10.1234/test"}
    }
    with patch("api_clients.semantic_scholar.requests.get", side_effect=[search_resp, detail_resp]):
        result = lookup_semantic_scholar(title="Machine Learning Study", year="2020")
    assert result["abstract"] == "This paper studies ML."


def test_s2_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.semantic_scholar.requests.get", return_value=mock_resp):
        result = lookup_semantic_scholar(doi="10.9999/fake")
    assert result is None


from api_clients.scholarly_client import lookup_scholarly


def test_scholarly_finds_paper():
    mock_result = {
        "bib": {
            "title": "Machine Learning Study",
            "abstract": "This paper studies ML.",
            "author": ["John Smith"],
            "pub_year": "2020",
            "venue": "Journal of AI",
        },
        "pub_url": "https://example.com/paper",
        "eprint_url": "https://example.com/paper.pdf",
    }
    with patch("api_clients.scholarly_client.scholarly") as mock_scholarly:
        mock_scholarly.search_pubs.return_value = iter([mock_result])
        result = lookup_scholarly("Machine Learning Study")
    assert result["abstract"] == "This paper studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"


def test_scholarly_no_results():
    with patch("api_clients.scholarly_client.scholarly") as mock_scholarly:
        mock_scholarly.search_pubs.return_value = iter([])
        result = lookup_scholarly("Nonexistent Paper XYZ123")
    assert result is None


def test_scholarly_disabled():
    with patch("api_clients.scholarly_client.SCHOLARLY_ENABLED", False):
        result = lookup_scholarly("Any Title")
    assert result is None
