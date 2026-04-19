"""Tests for v6.1 Phase B (curl_cffi) and Phase C (Playwright) tiers.

Heavy deps are optional — these tests mock the imports so they run even
without curl_cffi / playwright installed. A subset is gated behind
installation checks for real-library behavior validation.

Pins:
- Tier disabled in settings → immediate `kind="disabled"`
- Tier enabled but dep not installed → `kind="not_installed"` (no crash)
- force_tier field in BUILTIN_RULES promotes tier to front of plan
- force_tier falls through normal walk when that tier is disabled
- BrowserPool.instance() returns None when playwright isn't importable
"""

from unittest.mock import patch, MagicMock

import pytest

import file_downloader_fallback as fdf
from file_downloader_fallback import (
    FetchContext, FetchResult, download_with_fallback, _tier_curl_cffi,
    _tier_playwright, _resolve_force_tier,
)


@pytest.fixture(autouse=True)
def _reset_host_cache():
    from download_rules import _reset_host_tier_cache_for_tests, _reset_rate_limits_for_tests
    _reset_host_tier_cache_for_tests()
    _reset_rate_limits_for_tests()
    yield
    _reset_host_tier_cache_for_tests()
    _reset_rate_limits_for_tests()


# ============================================================
# curl_cffi tier
# ============================================================

class TestCurlCffiTier:
    def test_disabled_in_settings_is_noop(self, tmp_path):
        ctx = FetchContext(url="https://ssrn.com/abstract?id=1",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._curl_cffi_enabled", return_value=False):
            r = _tier_curl_cffi(ctx)
        assert r.ok is False
        assert r.kind == "disabled"

    def test_no_url_returns_no_match(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._curl_cffi_enabled", return_value=True):
            r = _tier_curl_cffi(ctx)
        assert r.kind == "no_match"

    def test_not_installed_returns_not_installed(self, tmp_path):
        """When curl_cffi isn't importable, fail gracefully."""
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._curl_cffi_enabled", return_value=True), \
             patch.dict("sys.modules", {"curl_cffi": None}):
            r = _tier_curl_cffi(ctx)
        assert r.ok is False
        assert r.kind == "not_installed"

    def test_success_writes_pdf(self, tmp_path):
        """Happy path: curl_cffi returns a real PDF bytes stream."""
        ctx = FetchContext(url="https://ssrn.com/abstract?id=1",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        # Build a fake curl_cffi module
        fake_module = MagicMock()
        fake_sess = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"%PDF-1.4 real content bytes"
        fake_resp.url = "https://ssrn.com/download?id=1"
        fake_sess.__enter__ = lambda self: self
        fake_sess.__exit__ = lambda *a: None
        fake_sess.get.return_value = fake_resp
        fake_module.requests.Session.return_value = fake_sess

        with patch("file_downloader_fallback._curl_cffi_enabled", return_value=True), \
             patch.dict("sys.modules", {"curl_cffi": fake_module,
                                         "curl_cffi.requests": fake_module.requests}):
            r = _tier_curl_cffi(ctx)
        assert r.ok is True
        # File actually written
        assert (tmp_path / "out.pdf").is_file()

    def test_non_pdf_response_fails_validation(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        fake_module = MagicMock()
        fake_sess = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"<!DOCTYPE html>"  # HTML, not PDF
        fake_resp.url = "https://ex.com/x.pdf"
        fake_sess.__enter__ = lambda self: self
        fake_sess.__exit__ = lambda *a: None
        fake_sess.get.return_value = fake_resp
        fake_module.requests.Session.return_value = fake_sess

        with patch("file_downloader_fallback._curl_cffi_enabled", return_value=True), \
             patch.dict("sys.modules", {"curl_cffi": fake_module,
                                         "curl_cffi.requests": fake_module.requests}):
            r = _tier_curl_cffi(ctx)
        assert r.ok is False
        assert r.kind == "validation"
        assert not (tmp_path / "out.pdf").is_file()


# ============================================================
# Playwright tier
# ============================================================

class TestPlaywrightTier:
    def test_disabled_in_settings_is_noop(self, tmp_path):
        ctx = FetchContext(url="https://eur-lex.europa.eu/x",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=False):
            r = _tier_playwright(ctx)
        assert r.kind == "disabled"

    def test_no_url_returns_no_match(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=True):
            r = _tier_playwright(ctx)
        assert r.kind == "no_match"

    def test_not_installed_returns_not_installed(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=True), \
             patch.dict("sys.modules", {"browser_pool": None}):
            r = _tier_playwright(ctx)
        assert r.ok is False
        assert r.kind == "not_installed"


# ============================================================
# force_tier
# ============================================================

class TestForceTier:
    def test_econstor_forces_curl_cffi(self):
        assert _resolve_force_tier("https://econstor.eu/paper.pdf") == "curl_cffi"

    def test_ssrn_forces_curl_cffi(self):
        assert _resolve_force_tier("https://papers.ssrn.com/x") == "curl_cffi"

    def test_researchgate_forces_curl_cffi(self):
        assert _resolve_force_tier("https://researchgate.net/publication/123") == "curl_cffi"

    def test_plain_host_no_force(self):
        assert _resolve_force_tier("https://example.com/x.pdf") is None

    def test_sec_gov_no_force(self):
        """SEC gets a UA rule but no force_tier — direct works after header fix."""
        assert _resolve_force_tier("https://www.sec.gov/x") is None

    def test_empty_url_no_force(self):
        assert _resolve_force_tier(None) is None
        assert _resolve_force_tier("") is None


class TestForceTierOrchestration:
    def test_forced_tier_runs_first(self, tmp_path):
        """econstor.eu is force_tier=curl_cffi — walk starts with curl_cffi,
        even when direct would otherwise come first."""
        call_order = []
        def track(name):
            def _fn(ctx):
                call_order.append(name)
                if name == "curl_cffi":
                    return FetchResult(ok=True, final_url=ctx.url, elapsed_ms=50)
                return FetchResult(ok=False, kind="http_4xx", http_status=403)
            return _fn
        with patch.object(fdf, "_tier_direct",          side_effect=track("direct")), \
             patch.object(fdf, "_tier_oa_fallbacks",    side_effect=track("oa")), \
             patch.object(fdf, "_tier_doi_negotiation", side_effect=track("doi")), \
             patch.object(fdf, "_tier_openreview",      side_effect=track("or")), \
             patch.object(fdf, "_tier_wayback",         side_effect=track("wayback")), \
             patch.object(fdf, "_tier_curl_cffi",       side_effect=track("curl_cffi")), \
             patch.object(fdf, "_tier_playwright",      side_effect=track("playwright")):
            outcome = download_with_fallback(
                "https://econstor.eu/paper.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://econstor.eu/paper.pdf"})
        assert call_order[0] == "curl_cffi"
        assert outcome["ok"] is True

    def test_forced_tier_falls_through_when_disabled(self, tmp_path):
        """When the forced tier reports `kind=disabled`, the walk continues
        down the rest of the plan rather than giving up."""
        call_order = []
        def track(name, ok_if_name=None, kind_override=None):
            def _fn(ctx):
                call_order.append(name)
                if kind_override:
                    return FetchResult(ok=False, kind=kind_override)
                if name == ok_if_name:
                    return FetchResult(ok=True, final_url=ctx.url, elapsed_ms=50)
                return FetchResult(ok=False, kind="http_4xx", http_status=403)
            return _fn
        with patch.object(fdf, "_tier_curl_cffi",
                           side_effect=track("curl_cffi", kind_override="disabled")), \
             patch.object(fdf, "_tier_direct",
                           side_effect=track("direct", ok_if_name="direct")), \
             patch.object(fdf, "_tier_oa_fallbacks",
                           side_effect=track("oa")), \
             patch.object(fdf, "_tier_doi_negotiation",
                           side_effect=track("doi")), \
             patch.object(fdf, "_tier_openreview",
                           side_effect=track("or")), \
             patch.object(fdf, "_tier_wayback",
                           side_effect=track("wayback")), \
             patch.object(fdf, "_tier_playwright",
                           side_effect=track("playwright")):
            outcome = download_with_fallback(
                "https://econstor.eu/paper.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://econstor.eu/paper.pdf"})
        # curl_cffi ran first (forced), came back disabled; direct ran next; won.
        assert call_order[0] == "curl_cffi"
        assert call_order[1] == "direct"
        assert outcome["ok"] is True


# ============================================================
# BrowserPool
# ============================================================

class TestBrowserPool:
    def test_returns_none_when_playwright_missing(self):
        from browser_pool import BrowserPool
        BrowserPool._reset_for_tests()
        with patch.dict("sys.modules", {"playwright.sync_api": None}):
            pool = BrowserPool.instance(size=1)
        assert pool is None
