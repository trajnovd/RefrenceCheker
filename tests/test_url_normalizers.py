"""Tests for the URL normalizer registry (v6.1 A0.4).

Pins:
- Built-in normalizers handle arXiv abs / arXiv html / OpenReview forum
- First-match-wins ordering
- Empty / unknown URLs pass through unchanged
- A rewriter that raises is skipped (doesn't poison the pipeline)
- register_normalizer() adds to the registry for new A1 tiers
"""

import re

import pytest

import url_normalizers


class TestBuiltins:
    def test_arxiv_abs_to_pdf(self):
        assert url_normalizers.normalize("https://arxiv.org/abs/2308.00016") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_arxiv_abs_strips_version(self):
        assert url_normalizers.normalize("https://arxiv.org/abs/2308.00016v3") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_arxiv_abs_legacy_id(self):
        # Legacy arXiv ids: subject-class/YYMMNNN (e.g. math/0102001)
        assert url_normalizers.normalize("https://arxiv.org/abs/math/0102001") == \
               "https://arxiv.org/pdf/math/0102001"

    def test_arxiv_html_to_pdf(self):
        """Regression: cheridito2025 case."""
        assert url_normalizers.normalize("https://arxiv.org/html/2507.06345v2") == \
               "https://arxiv.org/pdf/2507.06345"

    def test_arxiv_pdf_unchanged(self):
        # Already a direct PDF URL — don't touch it.
        assert url_normalizers.normalize("https://arxiv.org/pdf/2308.00016") == \
               "https://arxiv.org/pdf/2308.00016"

    def test_openreview_forum_to_pdf(self):
        assert url_normalizers.normalize("https://openreview.net/forum?id=Abc123") == \
               "https://openreview.net/pdf?id=Abc123"

    def test_openreview_pdf_unchanged(self):
        # Already direct; no match.
        assert url_normalizers.normalize("https://openreview.net/pdf?id=Abc123") == \
               "https://openreview.net/pdf?id=Abc123"


class TestPassthrough:
    def test_none_returns_none(self):
        assert url_normalizers.normalize(None) is None

    def test_empty_string_returns_empty(self):
        assert url_normalizers.normalize("") == ""

    def test_unknown_host_unchanged(self):
        url = "https://example.com/some/paper.pdf"
        assert url_normalizers.normalize(url) == url


class TestRegistry:
    def test_register_custom_normalizer(self):
        """New A1 tiers register their own rewriters. Confirm the machinery."""
        pattern = re.compile(r"https?://custom\.example\.com/landing/(\w+)")
        calls = []

        @url_normalizers.register_normalizer(pattern)
        def _rewrite(m):
            calls.append(m.group(1))
            return f"https://custom.example.com/pdf/{m.group(1)}"

        try:
            assert url_normalizers.normalize("https://custom.example.com/landing/abc") == \
                   "https://custom.example.com/pdf/abc"
            assert calls == ["abc"]
        finally:
            # Clean up so we don't leak state into other tests
            url_normalizers._NORMALIZERS.pop()

    def test_first_match_wins(self):
        """If two normalizers' patterns overlap, the first registered wins."""
        p1 = re.compile(r"https?://dup\.example\.com/(.*)")
        p2 = re.compile(r"https?://dup\.example\.com/specific")

        @url_normalizers.register_normalizer(p1)
        def _first(m):
            return "FIRST"

        @url_normalizers.register_normalizer(p2)
        def _second(m):
            return "SECOND"

        try:
            # Both patterns match; first-registered wins.
            assert url_normalizers.normalize("https://dup.example.com/specific") == "FIRST"
        finally:
            url_normalizers._NORMALIZERS.pop()
            url_normalizers._NORMALIZERS.pop()

    def test_rewriter_that_raises_is_skipped(self):
        """A broken normalizer must not poison the pipeline — we log and
        continue to the next one (or fall through to identity)."""
        pattern = re.compile(r"https?://bad\.example\.com/(.*)")

        @url_normalizers.register_normalizer(pattern)
        def _broken(m):
            raise ValueError("deliberate")

        try:
            # The broken rewriter matches but raises; normalize falls through
            # and returns the input unchanged.
            url = "https://bad.example.com/foo"
            assert url_normalizers.normalize(url) == url
        finally:
            url_normalizers._NORMALIZERS.pop()

    def test_registered_count_reflects_builtins(self):
        """Sanity check: at least the 3 built-ins (arxiv abs, arxiv html,
        openreview forum) are registered at import time."""
        assert url_normalizers.registered_count() >= 3


class TestLegacyAliasStillWorks:
    """file_downloader._normalize_bib_url delegates here — pin the contract
    so any future accidental reversion is caught."""

    def test_delegation(self):
        from file_downloader import _normalize_bib_url
        assert _normalize_bib_url("https://arxiv.org/abs/2308.00016v3") == \
               "https://arxiv.org/pdf/2308.00016"
        assert _normalize_bib_url("https://example.com/x") == \
               "https://example.com/x"
        assert _normalize_bib_url(None) is None
