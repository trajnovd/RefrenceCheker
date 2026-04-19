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
        # Block playwright.sync_api import so the tier sees ImportError.
        with patch("file_downloader_fallback._playwright_enabled", return_value=True), \
             patch.dict("sys.modules", {"playwright.sync_api": None}):
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


# ============================================================
# JS-challenge host registry (download_rules)
# ============================================================

class TestHtmlPaywallRefusal:
    """Regression: gastineau1991short — Google Scholar returned a JSTOR URL,
    JSTOR served a 200 reCAPTCHA wall, we saved it as the source, ref_match
    flagged the .md as not_matched. JSTOR-class HTML paywalls must be refused
    upfront (no heavy retry, no Wayback — those publishers' robots.txt blocks
    the Archive too)."""

    def test_jstor_refused_without_network(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        with patch.object(fd, "get_session") as gs:
            status = {}
            ok = fd._download_page("https://www.jstor.org/stable/4479463",
                                    out_path, status_out=status)
        assert ok is False
        assert status["kind"] == "html_paywall"
        # Direct GET must NOT have been called (the wall is universal)
        gs.return_value.get.assert_not_called()

    def test_wiley_html_refused(self, tmp_path):
        import file_downloader as fd
        with patch.object(fd, "get_session") as gs:
            status = {}
            ok = fd._download_page(
                "https://onlinelibrary.wiley.com/doi/10.1111/jofi.12498",
                str(tmp_path / "p.html"), status_out=status)
        assert ok is False
        assert status["kind"] == "html_paywall"
        gs.return_value.get.assert_not_called()

    def test_oxford_academic_html_refused(self, tmp_path):
        import file_downloader as fd
        with patch.object(fd, "get_session") as gs:
            status = {}
            ok = fd._download_page(
                "https://academic.oup.com/rfs/article/29/1/5/1576035",
                str(tmp_path / "p.html"), status_out=status)
        assert ok is False
        assert status["kind"] == "html_paywall"

    def test_taylor_francis_html_refused(self, tmp_path):
        import file_downloader as fd
        with patch.object(fd, "get_session") as gs:
            status = {}
            ok = fd._download_page(
                "https://www.tandfonline.com/doi/full/10.1080/12345.2024",
                str(tmp_path / "p.html"), status_out=status)
        assert ok is False
        assert status["kind"] == "html_paywall"

    def test_researchgate_html_refused(self, tmp_path):
        """Regression: Hasbrouck2007 — RG returned a teaser page (200 OK,
        title + abstract snippet) and we saved it. The HTML-paywall list
        now refuses RG; the force_tier=curl_cffi rule for RG (PDFs) is
        unaffected."""
        import file_downloader as fd
        with patch.object(fd, "get_session") as gs:
            status = {}
            ok = fd._download_page(
                "https://www.researchgate.net/publication/254441114_Empirical_Market_Microstructure",
                str(tmp_path / "p.html"), status_out=status)
        assert ok is False
        assert status["kind"] == "html_paywall"
        gs.return_value.get.assert_not_called()

    def test_normal_host_still_downloads(self, tmp_path):
        """Non-paywall hosts go through the normal path."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp = MagicMock(status_code=200, text="<html><body>" + "x" * 200 + "</body></html>")
        with patch.object(fd, "get_session") as gs:
            gs.return_value.get.return_value = resp
            ok = fd._download_page("https://arxiv.org/abs/1409.0473", out_path)
        assert ok is True
        gs.return_value.get.assert_called_once()


class TestIsHtmlPaywall:
    def test_jstor_recognized(self):
        from download_rules import is_html_paywall
        assert is_html_paywall("https://www.jstor.org/stable/4479463")
        assert is_html_paywall("https://JSTOR.org/x")  # case-insensitive

    def test_wiley_recognized(self):
        from download_rules import is_html_paywall
        assert is_html_paywall("https://onlinelibrary.wiley.com/doi/10.1/x")

    def test_oxford_academic_recognized(self):
        from download_rules import is_html_paywall
        assert is_html_paywall("https://academic.oup.com/rfs/article/29/1/5")

    def test_tandfonline_recognized(self):
        from download_rules import is_html_paywall
        assert is_html_paywall("https://www.tandfonline.com/doi/abs/10.1/x")

    def test_researchgate_recognized(self):
        from download_rules import is_html_paywall
        assert is_html_paywall("https://www.researchgate.net/publication/254441114")
        assert is_html_paywall("https://researchgate.net/x")  # no www

    def test_researchgate_pdf_still_routes_to_curl_cffi(self):
        """RG on the HTML-paywall list must NOT break the PDF orchestrator's
        existing force_tier=curl_cffi rule — RG sometimes serves real PDFs
        through TLS impersonation. Two separate code paths."""
        from file_downloader_fallback import _resolve_force_tier
        assert _resolve_force_tier(
            "https://www.researchgate.net/publication/254441114/file.pdf"
        ) == "curl_cffi"

    def test_unrelated_hosts_not_flagged(self):
        from download_rules import is_html_paywall
        assert not is_html_paywall("https://arxiv.org/abs/1409.0473")
        assert not is_html_paywall("https://www.nber.org/papers/w20592")
        assert not is_html_paywall("https://example.com/")

    def test_empty_url(self):
        from download_rules import is_html_paywall
        assert not is_html_paywall(None)
        assert not is_html_paywall("")


class TestPlaywrightSparsePageRejection:
    """Regression: wooldridge1995intelligent. Playwright went to a handle.net
    redirect, the redirect page had no real content, page.pdf() produced a
    678-byte 'valid PDF' (magic bytes pass) and we accepted it. The next tier
    (google_rescue) — which would have found a real PDF — was never reached."""

    def _make_pw_runtime(self, page_mock, browser_mock=None):
        """Build a sync_playwright fake that yields the given page when
        chromium.launch().new_context().new_page() is called."""
        ctx_pw = MagicMock(); ctx_pw.new_page.return_value = page_mock
        if browser_mock is None:
            browser_mock = MagicMock()
        browser_mock.new_context.return_value = ctx_pw
        runtime = MagicMock(); runtime.chromium.launch.return_value = browser_mock
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=runtime)
        cm.__exit__ = MagicMock(return_value=False)
        fake = MagicMock(); fake.sync_playwright = MagicMock(return_value=cm)
        return fake

    def test_sparse_innertext_rejected(self, tmp_path):
        """An empty redirect page (innerText < 500 chars) → fail, not a tiny PDF."""
        from file_downloader_fallback import _tier_playwright, FetchContext
        page = MagicMock()
        # No download fires → triggers html_to_pdf branch
        page.expect_download.side_effect = Exception("no download")
        page.goto.return_value = None
        page.evaluate.return_value = "Redirecting..."  # 13 chars — sparse
        # If we erroneously continue, page.pdf() would return tiny bytes
        page.pdf.return_value = b"%PDF-1.4 tiny rendering"
        fake_module = self._make_pw_runtime(page)
        ctx = FetchContext(url="http://hdl.handle.net/10044/1/35975",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=True), \
             patch("config.get_settings", return_value={
                 "download": {"playwright_timeout_s": 10, "playwright_html_to_pdf": True}
             }), patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
            r = _tier_playwright(ctx)
        assert r.ok is False
        assert r.kind == "validation"
        assert "sparse" in (r.detail or "")
        # PDF must NOT have been generated (don't waste time on doomed render)
        page.pdf.assert_not_called()
        # And no file written
        import os
        assert not os.path.exists(tmp_path / "out.pdf")

    def test_real_content_accepted(self, tmp_path):
        from file_downloader_fallback import _tier_playwright, FetchContext
        page = MagicMock()
        page.expect_download.side_effect = Exception("no download")
        page.goto.return_value = None
        page.evaluate.return_value = "Real article content. " * 100  # well over 500 chars
        # Synthesize a "real" PDF — well over the 5KB minimum
        page.pdf.return_value = b"%PDF-1.4 " + b"x" * 10000
        fake_module = self._make_pw_runtime(page)
        ctx = FetchContext(url="https://repository.example.edu/paper.html",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=True), \
             patch("config.get_settings", return_value={
                 "download": {"playwright_timeout_s": 10, "playwright_html_to_pdf": True}
             }), patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
            r = _tier_playwright(ctx)
        assert r.ok is True
        page.pdf.assert_called_once()

    def test_tiny_pdf_rejected_even_with_text(self, tmp_path):
        """Belt-and-suspenders: even when innerText is long enough, a sub-5KB
        PDF render is suspicious (Chromium can produce one for hidden bodies)."""
        from file_downloader_fallback import _tier_playwright, FetchContext
        page = MagicMock()
        page.expect_download.side_effect = Exception("no download")
        page.goto.return_value = None
        page.evaluate.return_value = "Substantial visible text. " * 50  # > 500 chars
        page.pdf.return_value = b"%PDF-1.4 only 700 bytes total maybe"  # too small
        fake_module = self._make_pw_runtime(page)
        ctx = FetchContext(url="https://x.com/", target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        with patch("file_downloader_fallback._playwright_enabled", return_value=True), \
             patch("config.get_settings", return_value={
                 "download": {"playwright_timeout_s": 10, "playwright_html_to_pdf": True}
             }), patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
            r = _tier_playwright(ctx)
        assert r.ok is False
        assert r.kind == "validation"
        assert "too small" in (r.detail or "")


class TestJsChallengeRegistry:
    def test_eur_lex_recognized(self):
        from download_rules import is_js_challenge
        assert is_js_challenge("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202401689")
        assert is_js_challenge("https://EUR-LEX.europa.eu/...")  # case-insensitive

    def test_europa_eu_subdomains_recognized(self):
        from download_rules import is_js_challenge
        # Suffix match — any europa.eu subdomain
        assert is_js_challenge("https://ec.europa.eu/info/...")
        assert is_js_challenge("https://europa.eu/")

    def test_elsevier_recognized(self):
        from download_rules import is_js_challenge
        assert is_js_challenge("https://www.elsevier.com/")
        assert is_js_challenge("https://www.sciencedirect.com/article/x")

    def test_unrelated_host_not_flagged(self):
        from download_rules import is_js_challenge
        assert not is_js_challenge("https://arxiv.org/abs/1706.03762")
        assert not is_js_challenge("https://example.com/")

    def test_empty_url(self):
        from download_rules import is_js_challenge
        assert not is_js_challenge(None)
        assert not is_js_challenge("")


# ============================================================
# HTML heavy fetchers (curl_cffi, Playwright) for pre-fetch
# ============================================================

class TestFetchHtmlViaCurlCffi:
    def test_disabled_in_settings_is_noop(self, tmp_path):
        from file_downloader import _fetch_html_via_curl_cffi
        with patch("config.get_settings", return_value={
            "download": {"use_curl_cffi_fallback": False}
        }):
            assert _fetch_html_via_curl_cffi("https://x.com/", str(tmp_path / "p.html")) is False

    def test_writes_html_on_success(self, tmp_path):
        from file_downloader import _fetch_html_via_curl_cffi
        out_path = str(tmp_path / "p.html")
        body = "<html><body>" + ("hello world content body. " * 20) + "</body></html>"
        fake_resp = MagicMock(status_code=200, text=body)
        fake_session = MagicMock()
        fake_session.__enter__ = MagicMock(return_value=fake_session)
        fake_session.__exit__ = MagicMock(return_value=False)
        fake_session.get = MagicMock(return_value=fake_resp)
        fake_requests = MagicMock()
        fake_requests.Session = MagicMock(return_value=fake_session)
        fake_module = MagicMock()
        fake_module.requests = fake_requests
        with patch("config.get_settings", return_value={
            "download": {"use_curl_cffi_fallback": True, "curl_cffi_impersonate": "chrome120",
                         "curl_cffi_timeout_s": 30}
        }), patch.dict("sys.modules", {"curl_cffi": fake_module,
                                         "curl_cffi.requests": fake_requests}):
            ok = _fetch_html_via_curl_cffi("https://x.com/", out_path)
        assert ok is True
        with open(out_path, encoding="utf-8") as f:
            assert "hello world content body" in f.read()

    def test_non_200_returns_false(self, tmp_path):
        from file_downloader import _fetch_html_via_curl_cffi
        fake_resp = MagicMock(status_code=403, text="forbidden")
        fake_session = MagicMock()
        fake_session.__enter__ = MagicMock(return_value=fake_session)
        fake_session.__exit__ = MagicMock(return_value=False)
        fake_session.get = MagicMock(return_value=fake_resp)
        fake_requests = MagicMock()
        fake_requests.Session = MagicMock(return_value=fake_session)
        fake_module = MagicMock()
        fake_module.requests = fake_requests
        with patch("config.get_settings", return_value={
            "download": {"use_curl_cffi_fallback": True}
        }), patch.dict("sys.modules", {"curl_cffi": fake_module,
                                         "curl_cffi.requests": fake_requests}):
            assert _fetch_html_via_curl_cffi("https://x.com/", str(tmp_path / "p.html")) is False


class TestFetchHtmlViaPlaywright:
    def test_disabled_in_settings_is_noop(self, tmp_path):
        from file_downloader import _fetch_html_via_playwright
        with patch("config.get_settings", return_value={
            "download": {"use_playwright_fallback": False}
        }):
            assert _fetch_html_via_playwright("https://x.com/", str(tmp_path / "p.html")) is False

    def test_writes_html_on_success(self, tmp_path):
        from file_downloader import _fetch_html_via_playwright
        out_path = str(tmp_path / "p.html")
        page = MagicMock()
        page.content.return_value = "<html><body>" + ("rendered content body text long. " * 10) + "</body></html>"
        page.goto.return_value = None
        ctx_pw = MagicMock()
        ctx_pw.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = ctx_pw
        # sync_playwright() returns a context manager exposing .chromium.launch
        pw_runtime = MagicMock()
        pw_runtime.chromium.launch.return_value = browser
        pw_cm = MagicMock()
        pw_cm.__enter__ = MagicMock(return_value=pw_runtime)
        pw_cm.__exit__ = MagicMock(return_value=False)
        fake_sync = MagicMock(return_value=pw_cm)
        fake_module = MagicMock()
        fake_module.sync_playwright = fake_sync
        with patch("config.get_settings", return_value={
            "download": {"use_playwright_fallback": True, "playwright_timeout_s": 30}
        }), patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
            ok = _fetch_html_via_playwright("https://eur-lex.europa.eu/x", out_path)
        assert ok is True
        with open(out_path, encoding="utf-8") as f:
            assert "rendered content body text long" in f.read()
        # Each call must own its lifecycle — browser must be closed
        browser.close.assert_called_once()

    def test_returns_false_when_playwright_not_installed(self, tmp_path):
        from file_downloader import _fetch_html_via_playwright
        # Simulate ImportError by removing the module from sys.modules and
        # blocking re-import via a sentinel.
        with patch("config.get_settings", return_value={
            "download": {"use_playwright_fallback": True}
        }), patch.dict("sys.modules", {"playwright.sync_api": None}):
            assert _fetch_html_via_playwright("https://x.com/", str(tmp_path / "p.html")) is False

    def test_per_call_lifecycle_safe_across_threads(self, tmp_path):
        """Regression: 'cannot switch to a different thread (which happens to
        have exited)'. The previous shared BrowserPool failed when Flask's
        short-lived refresh threads each tried to use the singleton. Each call
        must now own its own sync_playwright context."""
        import threading
        from file_downloader import _fetch_html_via_playwright

        sync_call_threads = []

        def make_pw():
            page = MagicMock()
            page.content.return_value = "<html>" + ("body content here. " * 20) + "</html>"
            ctx_pw = MagicMock(); ctx_pw.new_page.return_value = page
            browser = MagicMock(); browser.new_context.return_value = ctx_pw
            pw_runtime = MagicMock(); pw_runtime.chromium.launch.return_value = browser
            cm = MagicMock()
            def _enter(*a, **k):
                sync_call_threads.append(threading.get_ident())
                return pw_runtime
            cm.__enter__ = _enter
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        fake_module = MagicMock()
        fake_module.sync_playwright = MagicMock(side_effect=lambda: make_pw())

        results = []
        def worker(n):
            with patch("config.get_settings", return_value={
                "download": {"use_playwright_fallback": True, "playwright_timeout_s": 30}
            }), patch.dict("sys.modules", {"playwright.sync_api": fake_module}):
                ok = _fetch_html_via_playwright("https://x.com/", str(tmp_path / f"p_{n}.html"))
                results.append(ok)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert results == [True, True, True]
        # Each call should have entered sync_playwright separately (no singleton reuse)
        assert len(sync_call_threads) == 3
        # And the threads should be distinct (3 different thread idents)
        assert len(set(sync_call_threads)) == 3


# ============================================================
# _download_page integration: 202 + JS-challenge host trigger heavy fallback
# ============================================================

class TestDownloadPageHeavyFallback:
    def test_202_response_triggers_heavy_fallback(self, tmp_path):
        """EUR-Lex pattern: server returns 202 with a JS interstitial body.
        We must retry via the heavy fallback chain instead of giving up."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_202 = MagicMock(status_code=202, text="js interstitial")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback", return_value=True) as heavy:
            gs.return_value.get.return_value = resp_202
            ok = fd._download_page("https://example.com/page", out_path)
        assert ok is True
        heavy.assert_called_once()

    def test_js_challenge_host_skips_direct(self, tmp_path):
        """For known JS-challenge hosts, the direct fetch is skipped — we go
        straight to the heavy fallback (saves the doomed round-trip)."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback", return_value=True) as heavy:
            ok = fd._download_page(
                "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202401689",
                out_path)
        assert ok is True
        heavy.assert_called_once()
        # Direct GET must NOT have been called
        gs.return_value.get.assert_not_called()

    def test_4xx_does_not_trigger_heavy(self, tmp_path):
        """A real 4xx is a real failure — no JS challenge means no heavy retry."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_404 = MagicMock(status_code=404, text="not found")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback") as heavy:
            gs.return_value.get.return_value = resp_404
            ok = fd._download_page("https://example.com/page", out_path)
        assert ok is False
        heavy.assert_not_called()


class TestDownloadPdfHeavyFallback:
    def test_js_challenge_host_skips_direct_pdf(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.pdf")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_pdf_fallback", return_value="curl_cffi") as heavy, \
             patch.object(fd, "_try_wayback_pdf_fallback", return_value=False):
            ok = fd._download_pdf(
                "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202401689",
                out_path)
        assert ok is True
        heavy.assert_called_once()
        gs.return_value.get.assert_not_called()

    def test_202_pdf_response_triggers_heavy_fallback(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.pdf")
        resp_202 = MagicMock(status_code=202)
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_pdf_fallback", return_value="playwright") as heavy, \
             patch.object(fd, "_try_wayback_pdf_fallback", return_value=False):
            gs.return_value.get.return_value = resp_202
            ok = fd._download_pdf("https://example.com/x.pdf", out_path)
        assert ok is True
        heavy.assert_called_once()


# ============================================================
# A: 403/429 trigger heavy retry (Cloudflare-blocked pages)
# ============================================================

class TestHeavyRetryStatuses:
    def test_403_html_triggers_heavy(self, tmp_path):
        """klover_hsbc2025 regression. Klover's site is Cloudflare-protected
        and 403s anonymous requests; curl_cffi defeats it."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_403 = MagicMock(status_code=403, text="forbidden")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback", return_value="curl_cffi") as heavy, \
             patch.object(fd, "_try_wayback_html_fallback", return_value=False):
            gs.return_value.get.return_value = resp_403
            status = {}
            ok = fd._download_page("https://www.klover.ai/article", out_path, status_out=status)
        assert ok is True
        assert status["source_tier"] == "curl_cffi"
        heavy.assert_called_once()

    def test_429_html_triggers_heavy(self, tmp_path):
        """Rate-limited responses also retry — a real browser may get a fresh quota."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_429 = MagicMock(status_code=429, text="too many")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback", return_value="playwright") as heavy, \
             patch.object(fd, "_try_wayback_html_fallback", return_value=False):
            gs.return_value.get.return_value = resp_429
            ok = fd._download_page("https://x.com/", out_path)
        assert ok is True
        heavy.assert_called_once()

    def test_403_pdf_triggers_heavy(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.pdf")
        resp_403 = MagicMock(status_code=403)
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_pdf_fallback", return_value="curl_cffi") as heavy, \
             patch.object(fd, "_try_wayback_pdf_fallback", return_value=False):
            gs.return_value.get.return_value = resp_403
            ok = fd._download_pdf("https://x.com/x.pdf", out_path)
        assert ok is True
        heavy.assert_called_once()


# ============================================================
# B: Wayback HTML pre-fetch fallback
# ============================================================

class TestWaybackHtmlFallback:
    def test_wayback_snapshot_saved_with_captured_at(self, tmp_path):
        """googlea2a2025 regression. 404 dead URL → Wayback may have a snapshot."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        cdx_resp = MagicMock(status_code=200)
        cdx_resp.json.return_value = {
            "archived_snapshots": {"closest": {
                "url": "https://web.archive.org/web/20240615120000/https://a.com/x",
                "timestamp": "20240615120000"
            }}
        }
        snap_resp = MagicMock(status_code=200)
        snap_resp.text = "<html><body>" + ("archived content " * 20) + "</body></html>"
        def fake_get(url, **kw):
            return cdx_resp if "wayback/available" in url else snap_resp
        with patch.object(fd, "get_session") as gs:
            gs.return_value.get.side_effect = fake_get
            ok, snapshot_url, captured_at = fd._fetch_html_via_wayback("https://a.com/x", out_path)
        assert ok is True
        assert "web.archive.org" in snapshot_url
        # captured_at is the SNAPSHOT timestamp, not "now"
        assert captured_at.startswith("2024-06-15")
        with open(out_path, encoding="utf-8") as f:
            assert "archived content" in f.read()

    def test_no_snapshot_returns_false(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        cdx_resp = MagicMock(status_code=200)
        cdx_resp.json.return_value = {"archived_snapshots": {}}
        with patch.object(fd, "get_session") as gs:
            gs.return_value.get.return_value = cdx_resp
            ok, snapshot_url, captured_at = fd._fetch_html_via_wayback(
                "https://nonexistent.example.com/x", out_path)
        assert ok is False
        assert snapshot_url is None

    def test_404_falls_through_to_wayback(self, tmp_path):
        """404 from direct → Wayback rescue (the googlea2a2025 path)."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_404 = MagicMock(status_code=404, text="not found")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_wayback_html_fallback", return_value=True) as wb:
            gs.return_value.get.return_value = resp_404
            ok = fd._download_page("https://dead.example.com/x", out_path)
        assert ok is True
        wb.assert_called_once()


class TestWaybackTimestampParser:
    def test_full_timestamp_parsed_to_iso(self):
        from file_downloader import _wayback_ts_to_iso
        assert _wayback_ts_to_iso("20240615120000") == "2024-06-15T12:00:00+00:00"

    def test_short_timestamp_falls_back_to_midnight(self):
        from file_downloader import _wayback_ts_to_iso
        assert _wayback_ts_to_iso("20240615") == "2024-06-15T00:00:00+00:00"

    def test_invalid_returns_none(self):
        from file_downloader import _wayback_ts_to_iso
        assert _wayback_ts_to_iso(None) is None
        assert _wayback_ts_to_iso("") is None
        assert _wayback_ts_to_iso("xyz") is None


# ============================================================
# Pre-fetch threading: tier metadata flows back to caller for provenance
# ============================================================

class TestPreFetchTierThreading:
    def test_wayback_success_carries_tier_and_captured_at(self, tmp_path):
        from file_downloader import pre_download_bib_url
        def fake_dl(url, path, status_out=None):
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html>archived</html>")
            if status_out is not None:
                status_out["source_tier"] = "wayback"
                status_out["snapshot_url"] = "https://web.archive.org/web/20240601/https://x/"
                status_out["captured_at"] = "2024-06-01T00:00:00+00:00"
            return True
        with patch("file_downloader._download_page", side_effect=fake_dl):
            out = pre_download_bib_url(str(tmp_path), "k", "https://x.com/dead-page")
        assert out["page"]
        assert out["tier"] == "wayback"
        assert out["url"] == "https://web.archive.org/web/20240601/https://x/"
        assert out["captured_at"] == "2024-06-01T00:00:00+00:00"

    def test_direct_success_carries_direct_tier(self, tmp_path):
        from file_downloader import pre_download_bib_url
        def fake_dl(url, path, status_out=None):
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html>live</html>")
            if status_out is not None:
                status_out["source_tier"] = "direct"
            return True
        with patch("file_downloader._download_page", side_effect=fake_dl):
            out = pre_download_bib_url(str(tmp_path), "k", "https://x.com/live-page")
        assert out["tier"] == "direct"
        assert "captured_at" not in out


# ============================================================
# Wayback annotation in .md
# ============================================================

class TestChallengePageDetection:
    """klover_hsbc2025 regression. Cloudflare's 'Just a moment...' interstitial
    was being captured and saved as the article body — empty .md, garbage HTML."""

    def test_cloudflare_just_a_moment_detected(self):
        from file_downloader import _looks_like_challenge_page
        cf_page = '<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>'
        assert _looks_like_challenge_page(cf_page) is True

    def test_cloudflare_body_marker_detected(self):
        from file_downloader import _looks_like_challenge_page
        page = '<html><body><div id="challenge-error-text">verifying</div></body></html>'
        assert _looks_like_challenge_page(page) is True

    def test_cf_browser_verification_detected(self):
        from file_downloader import _looks_like_challenge_page
        page = '<html><body><div class="cf-browser-verification">x</div></body></html>'
        assert _looks_like_challenge_page(page) is True

    def test_attention_required_detected(self):
        from file_downloader import _looks_like_challenge_page
        page = '<html><head><title>Attention Required! | Cloudflare</title></head></html>'
        assert _looks_like_challenge_page(page) is True

    def test_cdn_cgi_challenge_marker_detected(self):
        from file_downloader import _looks_like_challenge_page
        page = '<html><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1"></script></html>'
        assert _looks_like_challenge_page(page) is True

    def test_real_html_not_flagged(self):
        from file_downloader import _looks_like_challenge_page
        page = '<html><head><title>HSBC AI Strategy Analysis</title></head><body><h1>Article</h1><p>Real content...</p></body></html>'
        assert _looks_like_challenge_page(page) is False

    def test_empty_not_flagged(self):
        from file_downloader import _looks_like_challenge_page
        assert _looks_like_challenge_page("") is False
        assert _looks_like_challenge_page(None) is False


class TestCurlCffiRejectsChallengePage:
    """Cloudflare may return 200 with the challenge body — curl_cffi must
    detect that and not save it (so the next tier gets a chance)."""

    def test_curl_cffi_rejects_cf_challenge(self, tmp_path):
        from file_downloader import _fetch_html_via_curl_cffi
        out_path = str(tmp_path / "p.html")
        cf_body = ('<!DOCTYPE html><html><head><title>Just a moment...</title></head>'
                   '<body>' + ('a' * 200) + '</body></html>')
        fake_resp = MagicMock(status_code=200, text=cf_body)
        fake_session = MagicMock()
        fake_session.__enter__ = MagicMock(return_value=fake_session)
        fake_session.__exit__ = MagicMock(return_value=False)
        fake_session.get = MagicMock(return_value=fake_resp)
        fake_requests = MagicMock(); fake_requests.Session = MagicMock(return_value=fake_session)
        fake_module = MagicMock(); fake_module.requests = fake_requests
        with patch("config.get_settings", return_value={
            "download": {"use_curl_cffi_fallback": True}
        }), patch.dict("sys.modules", {"curl_cffi": fake_module,
                                         "curl_cffi.requests": fake_requests}):
            ok = _fetch_html_via_curl_cffi("https://x.com/", out_path)
        assert ok is False
        # File must NOT have been written — defer to next tier
        import os
        assert not os.path.exists(out_path)


class TestBotBlockedErrorSurfacing:
    """When 4xx/heavy fallback all fail with challenge detection, surface
    a clear 'use Paste Content' message instead of generic 'unreachable'."""

    def test_403_after_heavy_fail_marked_bot_blocked(self, tmp_path):
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_403 = MagicMock(status_code=403, text="forbidden")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_heavy_html_fallback", return_value=None), \
             patch.object(fd, "_try_wayback_html_fallback", return_value=False):
            gs.return_value.get.return_value = resp_403
            status = {}
            ok = fd._download_page("https://www.klover.ai/article", out_path, status_out=status)
        assert ok is False
        assert status["kind"] == "bot_blocked"
        assert status["http_status"] == 403

    def test_404_after_heavy_skip_keeps_http_4xx_kind(self, tmp_path):
        """404 isn't in heavy retry; failure should still classify as http_4xx,
        not bot_blocked (the URL is genuinely dead, not blocked)."""
        import file_downloader as fd
        out_path = str(tmp_path / "p.html")
        resp_404 = MagicMock(status_code=404, text="not found")
        with patch.object(fd, "get_session") as gs, \
             patch.object(fd, "_try_wayback_html_fallback", return_value=False):
            gs.return_value.get.return_value = resp_404
            status = {}
            ok = fd._download_page("https://dead.example.com/x", out_path, status_out=status)
        assert ok is False
        assert status["kind"] == "http_4xx"

    def test_humanize_bot_blocked_message(self):
        from lookup_engine import _humanize_bib_url_failure
        msg = _humanize_bib_url_failure({"kind": "bot_blocked", "http_status": 403})
        assert "Cloudflare" in msg or "WAF" in msg
        assert "Paste Content" in msg


class TestWaybackMdAnnotation:
    def test_note_prepended_for_wayback_page(self, tmp_path):
        """When the .md body comes from a Wayback snapshot, prepend a note so
        the LLM/human knows the content is archival."""
        from file_downloader import _build_reference_md
        # Pre-write the page file
        page_path = tmp_path / "k_page.html"
        page_path.write_text("<html><body>" + ("archived content. " * 20) + "</body></html>",
                             encoding="utf-8")
        files = {"page": "k_page.html"}
        result = {
            "title": "T", "abstract": None, "url": "https://x.com/dead",
            "files_origin": {"page": {
                "tier": "wayback",
                "url": "https://web.archive.org/web/20240615120000/https://x.com/dead",
                "host": "web.archive.org",
                "captured_at": "2024-06-15T12:00:00+00:00",
            }}
        }
        md_filename = _build_reference_md(str(tmp_path), "k", "k", result, files)
        with open(tmp_path / md_filename, encoding="utf-8") as f:
            md = f.read()
        assert "Internet Archive Wayback Machine" in md
        assert "2024-06-15" in md
        # Snapshot URL appears so the user can verify
        assert "web.archive.org" in md

    def test_no_note_for_direct_page(self, tmp_path):
        from file_downloader import _build_reference_md
        page_path = tmp_path / "k_page.html"
        page_path.write_text("<html><body>" + ("live content. " * 20) + "</body></html>",
                             encoding="utf-8")
        files = {"page": "k_page.html"}
        result = {
            "title": "T", "abstract": None, "url": "https://x.com/live",
            "files_origin": {"page": {"tier": "direct", "url": "https://x.com/live",
                                       "host": "x.com", "captured_at": "now"}}
        }
        md_filename = _build_reference_md(str(tmp_path), "k", "k", result, files)
        with open(tmp_path / md_filename, encoding="utf-8") as f:
            md = f.read()
        assert "Internet Archive" not in md
        assert "Wayback" not in md
