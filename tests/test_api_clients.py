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
