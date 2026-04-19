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
    with patch("api_clients.crossref.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_crossref("10.1234/test")
    assert result["title"] == "Machine Learning Study"
    assert result["authors"] == ["John Smith"]
    assert result["year"] == "2020"


def test_crossref_with_invalid_doi():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.crossref.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
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
    with patch("api_clients.unpaywall.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
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
    with patch("api_clients.unpaywall.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_unpaywall("10.1234/test")
    assert result["pdf_url"] is None


def test_unpaywall_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.unpaywall.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
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
    with patch("api_clients.semantic_scholar.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_semantic_scholar(doi="10.1234/test")
    assert result["abstract"] == "This paper studies ML."
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["citation_count"] == 42


def test_s2_search_by_title():
    # Match endpoint returns full data in one call
    match_resp = Mock()
    match_resp.status_code = 200
    match_resp.json.return_value = {
        "data": [{
            "paperId": "abc123",
            "title": "Machine Learning Study",
            "abstract": "This paper studies ML.",
            "year": 2020,
            "citationCount": 42,
            "isOpenAccess": False,
            "openAccessPdf": None,
            "authors": [{"name": "John Smith"}],
            "externalIds": {"DOI": "10.1234/test"}
        }]
    }
    with patch("api_clients.semantic_scholar.get_session") as _gs:
        _gs.return_value.get.return_value = match_resp
        result = lookup_semantic_scholar(title="Machine Learning Study", year="2020")
    assert result["abstract"] == "This paper studies ML."


def test_s2_not_found():
    mock_resp = Mock()
    mock_resp.status_code = 404
    with patch("api_clients.semantic_scholar.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_semantic_scholar(doi="10.9999/fake")
    assert result is None


from api_clients.scholarly_client import lookup_scholarly


def test_scholarly_finds_paper():
    # Mock the HTML response from Google Scholar
    html = '''<div class="gs_r gs_or gs_scl">
        <div class="gs_ggs"><a href="https://example.com/paper.pdf">[PDF]</a></div>
        <div class="gs_ri">
            <h3 class="gs_rt"><a href="https://example.com/paper">Machine Learning Study</a></h3>
            <div class="gs_a">J Smith, J Doe - Journal of AI, 2020 - Publisher</div>
            <div class="gs_rs">This paper studies ML in depth with novel approaches.</div>
        </div>
    </div>'''
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.text = html
    with patch("api_clients.scholarly_client.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_scholarly("Machine Learning Study")
    assert result is not None
    assert result["title"] == "Machine Learning Study"
    assert "ML" in result["abstract"]
    assert result["pdf_url"] == "https://example.com/paper.pdf"


def test_scholarly_no_results():
    html = '<div class="gs_r gs_or"><div class="gs_ri"><div class="gs_rs">No results</div></div></div>'
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.text = '<html><body></body></html>'
    with patch("api_clients.scholarly_client.get_session") as _gs:
        _gs.return_value.get.return_value = mock_resp
        result = lookup_scholarly("Nonexistent Paper XYZ123")
    assert result is None


def test_scholarly_disabled():
    with patch("api_clients.scholarly_client.SCHOLARLY_ENABLED", False):
        result = lookup_scholarly("Any Title")
    assert result is None
