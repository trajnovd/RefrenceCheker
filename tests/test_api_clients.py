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


# ============================================================
# arxiv_client.search_arxiv — author-overlap guard
# ============================================================

def _arxiv_atom(entries):
    """Build a minimal arXiv atom feed XML from a list of (title, arxiv_id, authors)."""
    items = ""
    for title, arxiv_id, authors in entries:
        author_xml = "".join(
            f"<author><name>{a}</name></author>" for a in authors
        )
        items += (
            "<entry>"
            f"<id>http://arxiv.org/abs/{arxiv_id}v1</id>"
            f"<title>{title}</title>"
            f"<summary>An abstract.</summary>"
            f"<link title=\"pdf\" type=\"application/pdf\" href=\"https://arxiv.org/pdf/{arxiv_id}v1\"/>"
            f"{author_xml}"
            "</entry>"
        )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{items}</feed>'


class TestArxivAuthorGuard:
    """Pin baddeley2020working regression. Generic 2-word title 'Working memory'
    matched 'Working Memory Graphs' (Loynd et al., 1911.07141) by substring
    and the wrong PDF was downloaded. The fix: when bib provides authors,
    weak title matches must share at least one author last-name."""

    def test_rejects_weak_match_when_authors_disagree(self):
        from api_clients.arxiv_client import search_arxiv
        # arxiv returns "Working Memory Graphs" by Loynd et al. — substring
        # match on the bib's "Working memory" but completely different paper.
        feed = _arxiv_atom([
            ("Working Memory Graphs", "1911.07141",
             ["Ricky Loynd", "Roland Fernandez", "Asli Celikyilmaz"]),
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Working memory", authors="Baddeley, Alan")
        assert r is None

    def test_accepts_when_author_overlaps(self):
        from api_clients.arxiv_client import search_arxiv
        feed = _arxiv_atom([
            ("Working Memory Graphs", "1911.07141",
             ["Alan Baddeley", "Ricky Loynd"]),  # Baddeley is now an author
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Working memory", authors="Baddeley, Alan")
        assert r is not None
        assert r["arxiv_id"] == "1911.07141"

    def test_accepts_exact_title_match_regardless_of_authors(self):
        """Exact title match is strong enough on its own (avoids breaking
        legitimate matches where the bib mistypes / abbreviates an author)."""
        from api_clients.arxiv_client import search_arxiv
        feed = _arxiv_atom([
            ("Working Memory", "1234.5678", ["Some Other Person"]),
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Working Memory", authors="Baddeley, Alan")
        assert r is not None
        assert r["arxiv_id"] == "1234.5678"

    def test_no_authors_provided_keeps_old_behavior(self):
        """When the bib has no authors, fall back to the title-only behavior
        — don't introduce a regression for refs with no author info."""
        from api_clients.arxiv_client import search_arxiv
        feed = _arxiv_atom([
            ("Working Memory Graphs", "1911.07141", ["Ricky Loynd"]),
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Working memory", authors=None)
        # Substring match passes when there's no author check available
        assert r is not None
        assert r["arxiv_id"] == "1911.07141"

    def test_accepts_strong_title_match_without_author_overlap(self):
        """High word-overlap (≥0.85) is enough on its own — bypasses author
        check. Common when an arXiv preprint's title differs from the bib's
        by one trailing word in a long title (subtitle, year, etc.)."""
        from api_clients.arxiv_client import search_arxiv
        # 7 query words, 8 result words → 7/8 = 0.875 ≥ 0.85
        feed = _arxiv_atom([
            ("Attention Is All You Need For Sequence Transduction Tasks",
             "9999.0001", ["Other Author"]),
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Attention Is All You Need For Sequence Transduction",
                             authors="Some, Author")
        assert r is not None

    def test_skips_to_next_entry_when_first_rejected(self):
        """Multiple results: first one fails author guard, second is a real
        Baddeley paper — pipeline should pick the second."""
        from api_clients.arxiv_client import search_arxiv
        feed = _arxiv_atom([
            ("Working Memory Graphs", "1911.07141", ["Ricky Loynd"]),
            ("Working Memory and Cognition", "2001.0002", ["Alan Baddeley"]),
        ])
        resp = Mock(status_code=200, text=feed)
        with patch("api_clients.arxiv_client.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = search_arxiv("Working memory", authors="Baddeley, Alan")
        assert r is not None
        assert r["arxiv_id"] == "2001.0002"


class TestArxivLastNameExtraction:
    def test_bibtex_and_form(self):
        from api_clients.arxiv_client import _last_names
        assert _last_names("Baddeley, Alan and Hitch, Graham J.") == {"baddeley", "hitch"}

    def test_first_last_form(self):
        from api_clients.arxiv_client import _last_names
        assert _last_names("Alan Baddeley") == {"baddeley"}

    def test_list_of_strings(self):
        from api_clients.arxiv_client import _last_names
        assert _last_names(["Alan Baddeley", "Graham Hitch"]) == {"baddeley", "hitch"}

    def test_empty(self):
        from api_clients.arxiv_client import _last_names
        assert _last_names(None) == set()
        assert _last_names("") == set()
        assert _last_names([]) == set()
