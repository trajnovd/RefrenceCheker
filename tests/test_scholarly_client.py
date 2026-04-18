"""Tests for scholarly_client — title-overlap relevance filter.

Regression: chiang2025llm case. Google Scholar's broad (no-quote) retry returned
a different COLING 2025 paper for "LLMs for Corporate Transparency: Evaluating
Earnings Call Q&A". Without a relevance check, we downloaded the wrong paper as
the citation source.
"""

from unittest.mock import patch, MagicMock
import pytest

from api_clients.scholarly_client import (
    _is_relevant, _normalize, _pick_relevant, _parse_scholar_result,
)


# ============================================================
# _is_relevant — title overlap threshold
# ============================================================

class TestIsRelevant:
    def test_exact_match_passes(self):
        assert _is_relevant("Reinforcement Learning for Trade Execution",
                            "Reinforcement Learning for Trade Execution") is True

    def test_minor_punctuation_diff_passes(self):
        # Punctuation is stripped in normalization
        assert _is_relevant("LLMs for Corporate Transparency: Evaluating Earnings Call Q&A",
                            "LLMs for Corporate Transparency - Evaluating Earnings Call Q A") is True

    def test_unrelated_paper_rejected(self):
        # Two papers might both contain "Learning" "Trade" "Execution" but be different works
        assert _is_relevant(
            "LLMs for Corporate Transparency: Evaluating Earnings Call Q&A",
            "Reinforcement Learning for Optimized Trade Execution") is False

    def test_partial_overlap_below_threshold_rejected(self):
        # Bib title: 9 distinct words (after normalize). 60% threshold = at least ~5 must match.
        # Result title shares only "Earnings Call" → ~22% overlap → reject.
        assert _is_relevant(
            "LLMs for Corporate Transparency: Evaluating Earnings Call Q&A",
            "Conversational Agents on Earnings Call Disclosures") is False

    def test_high_overlap_passes(self):
        # Same paper, just minor differences (subtitle truncation, casing).
        assert _is_relevant(
            "Reinforcement Learning for Trade Execution with Market Impact",
            "Reinforcement Learning for Trade Execution with Market Impact: A Study") is True

    def test_empty_result_title_rejected(self):
        assert _is_relevant("Some Query", "") is False
        assert _is_relevant("Some Query", None) is False

    def test_empty_query_passes_anything(self):
        # If we have no query words to match, don't filter out
        assert _is_relevant("", "Anything Goes") is True


# ============================================================
# _normalize
# ============================================================

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Hello WORLD") == "hello world"

    def test_strips_punctuation(self):
        assert "q a" in _normalize("Q&A")
        assert ":" not in _normalize("Title: Subtitle")

    def test_handles_none(self):
        assert _normalize(None) == ""


# ============================================================
# _pick_relevant — walks results until one passes
# ============================================================

def _fake_result_div(title, abstract="An abstract.", url="https://x.example.com/", pdf=None):
    """Build a BeautifulSoup-like fake result div using MagicMocks."""
    from bs4 import BeautifulSoup
    pdf_block = (f'<div class="gs_ggs"><a href="{pdf}">PDF</a></div>') if pdf else ""
    html = (
        '<div class="gs_r gs_or gs_scl">'
        f'<h3 class="gs_rt"><a href="{url}">{title}</a></h3>'
        f'<div class="gs_a">A. Author, B. Author - Some Journal, 2024 - Pub</div>'
        f'<div class="gs_rs">{abstract}</div>'
        f'{pdf_block}'
        '</div>'
    )
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("div.gs_r.gs_or.gs_scl")


class TestPickRelevant:
    def test_picks_first_relevant_result(self):
        results = [
            _fake_result_div("Totally Different Paper", url="https://wrong.example.com/"),
            _fake_result_div("Reinforcement Learning for Trade Execution",
                             url="https://right.example.com/"),
        ]
        picked = _pick_relevant(results, "Reinforcement Learning for Trade Execution")
        assert picked is not None
        assert "right.example.com" in picked["url"]

    def test_returns_none_when_no_result_relevant(self):
        """Critical regression: chiang2025llm. Scholar's first result was a
        different paper — must NOT be returned."""
        results = [
            _fake_result_div("Conversational Agents on Earnings Call Disclosures",
                             url="https://aclanthology.org/2025.coling-main.705/"),
            _fake_result_div("Another Unrelated NLP Paper",
                             url="https://other.example.com/"),
        ]
        picked = _pick_relevant(
            results,
            "LLMs for Corporate Transparency: Evaluating Earnings Call Q&A")
        assert picked is None  # better to return nothing than the wrong paper

    def test_skips_results_with_no_title(self):
        # Build a result with empty title element
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(
            '<div class="gs_r gs_or gs_scl"><h3 class="gs_rt"></h3></div>',
            "html.parser")
        empty_div = soup.select_one("div.gs_r.gs_or.gs_scl")
        good = _fake_result_div("Reinforcement Learning for Trade Execution")
        picked = _pick_relevant([empty_div, good], "Reinforcement Learning for Trade Execution")
        assert picked is not None


# ============================================================
# _parse_scholar_result — preserves the existing parsing contract
# ============================================================

class TestParseScholarResult:
    def test_extracts_title_url_abstract_pdf(self):
        div = _fake_result_div(
            "My Paper",
            abstract="An abstract sentence.",
            url="https://example.com/paper",
            pdf="https://example.com/paper.pdf",
        )
        parsed = _parse_scholar_result(div)
        assert parsed["title"] == "My Paper"
        assert parsed["url"] == "https://example.com/paper"
        assert parsed["abstract"] == "An abstract sentence."
        assert parsed["pdf_url"] == "https://example.com/paper.pdf"

    def test_extracts_authors_and_year(self):
        div = _fake_result_div("My Paper")
        parsed = _parse_scholar_result(div)
        assert "A. Author" in parsed["authors"]
        assert parsed["year"] == "2024"


# ============================================================
# End-to-end: lookup_scholarly with mocked HTTP
# ============================================================

def _mock_scholar_html(titles_and_urls):
    """Build a minimal Google Scholar SERP HTML for the given (title, url) tuples."""
    items = "".join(
        f'<div class="gs_r gs_or gs_scl">'
        f'  <h3 class="gs_rt"><a href="{u}">{t}</a></h3>'
        f'  <div class="gs_a">A. Author - Journal, 2024 - Pub</div>'
        f'  <div class="gs_rs">Snippet.</div>'
        f'</div>'
        for t, u in titles_and_urls
    )
    return f"<html><body>{items}</body></html>"


class TestLookupScholarlyRelevance:
    def test_chiang2025_regression(self):
        """Bib: 'LLMs for Corporate Transparency: Evaluating Earnings Call Q&A'.
        Both passes return a wrong COLING paper → must return None."""
        from api_clients import scholarly_client
        # Reset session state in case other tests left it disabled
        scholarly_client._disabled = False
        scholarly_client._consecutive_failures = 0

        wrong_paper_html = _mock_scholar_html([
            ("Conversational Agents on Earnings Call Disclosures",
             "https://aclanthology.org/2025.coling-main.705/"),
        ])

        with patch("api_clients.scholarly_client.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = wrong_paper_html
            mock_get.return_value = resp
            result = scholarly_client.lookup_scholarly(
                "LLMs for Corporate Transparency: Evaluating Earnings Call Q&A")
        assert result is None

    def test_returns_relevant_result(self):
        from api_clients import scholarly_client
        scholarly_client._disabled = False
        scholarly_client._consecutive_failures = 0

        good_html = _mock_scholar_html([
            ("Reinforcement Learning for Trade Execution",
             "https://arxiv.org/abs/2401.00000"),
        ])

        with patch("api_clients.scholarly_client.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.text = good_html
            mock_get.return_value = resp
            result = scholarly_client.lookup_scholarly(
                "Reinforcement Learning for Trade Execution")
        assert result is not None
        assert "arxiv.org" in result["url"]
