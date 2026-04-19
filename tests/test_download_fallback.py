"""Tests for the tiered download orchestrator (v6.1 A1).

Pins:
- Direct tier succeeds → no other tiers are consulted
- Direct tier fails → OA fallbacks, DOI negotiation, OpenReview, Wayback walked in order
- First success short-circuits the rest
- is_bib_url=True: only direct tier runs (§11.14)
- download_log records every attempt with elapsed_ms + kind
- provenance recorded on the result for the winning tier
- pdf validator rejects non-PDF content early
"""

import os
from unittest.mock import patch, MagicMock

import pytest

import file_downloader_fallback as fdf
from file_downloader_fallback import (
    download_with_fallback, validate_pdf_head, FetchContext, FetchResult,
)
from provenance import get_origin


@pytest.fixture(autouse=True)
def _reset_host_cache():
    """A2 host-best-tier cache leaks across tests — reset before each."""
    from download_rules import _reset_host_tier_cache_for_tests, _reset_rate_limits_for_tests
    _reset_host_tier_cache_for_tests()
    _reset_rate_limits_for_tests()
    yield
    _reset_host_tier_cache_for_tests()
    _reset_rate_limits_for_tests()


# ============================================================
# PDF validator (A0.2 — the shared primitive)
# ============================================================

class TestValidatePdfHead:
    def test_pdf_header_passes(self):
        assert validate_pdf_head(b"%PDF-1.4 rest") is True

    def test_non_pdf_rejected(self):
        assert validate_pdf_head(b"<!DOCTYPE html>") is False
        assert validate_pdf_head(b"") is False
        assert validate_pdf_head(b"PDF ") is False  # wrong prefix


# ============================================================
# Orchestrator — tier ordering, short-circuit, is_bib_url guard
# ============================================================

def _make_fetch_result_ok(url="https://example.com/x.pdf"):
    return FetchResult(ok=True, final_url=url, http_status=200, elapsed_ms=120)


def _make_fetch_result_fail(kind="http_4xx", status=403):
    return FetchResult(ok=False, http_status=status, kind=kind, elapsed_ms=50)


class TestOrchestratorOrdering:
    def test_direct_wins_no_other_tiers_run(self, tmp_path):
        with patch.object(fdf, "_tier_direct",
                           return_value=_make_fetch_result_ok("https://ex.com/x.pdf")) as direct, \
             patch.object(fdf, "_tier_oa_fallbacks") as oa, \
             patch.object(fdf, "_tier_doi_negotiation") as doi_neg, \
             patch.object(fdf, "_tier_openreview") as opr, \
             patch.object(fdf, "_tier_wayback") as way:
            outcome = download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://ex.com/x.pdf"})
        assert outcome["ok"] is True
        assert outcome["tier"] == "direct"
        direct.assert_called_once()
        oa.assert_not_called(); doi_neg.assert_not_called()
        opr.assert_not_called(); way.assert_not_called()

    def test_direct_fails_oa_walked_next(self, tmp_path):
        with patch.object(fdf, "_tier_direct",
                           return_value=_make_fetch_result_fail("http_4xx", 403)), \
             patch.object(fdf, "_tier_oa_fallbacks",
                           return_value=_make_fetch_result_ok("https://alt.example.com/x.pdf")), \
             patch.object(fdf, "_tier_doi_negotiation") as doi_neg:
            outcome = download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k",
                result={"pdf_url": "https://ex.com/x.pdf",
                        "pdf_url_fallbacks": ["https://alt.example.com/x.pdf"]})
        assert outcome["ok"] is True
        assert outcome["tier"] == "oa_fallbacks"
        doi_neg.assert_not_called()

    def test_all_tiers_fail_returns_none(self, tmp_path):
        with patch.object(fdf, "_tier_direct", return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_oa_fallbacks", return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_doi_negotiation", return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_openreview", return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_wayback", return_value=_make_fetch_result_fail()):
            outcome = download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://ex.com/x.pdf"})
        assert outcome["ok"] is False
        assert outcome["tier"] is None

    def test_bib_url_path_skips_fallbacks(self, tmp_path):
        """§11.14: bib-URL failures must surface as bib_url_unreachable, not get
        silently replaced with a Wayback snapshot of a different URL."""
        with patch.object(fdf, "_tier_direct",
                           return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_wayback") as way:
            outcome = download_with_fallback(
                "https://bib.example.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://bib.example.com/x.pdf"},
                is_bib_url=True)
        assert outcome["ok"] is False
        way.assert_not_called()

    def test_tier_raising_is_isolated(self, tmp_path):
        """A buggy tier must not poison the walk — log & continue."""
        def _boom(ctx): raise RuntimeError("deliberate")
        with patch.object(fdf, "_tier_direct", side_effect=_boom), \
             patch.object(fdf, "_tier_oa_fallbacks",
                           return_value=_make_fetch_result_ok("https://alt.ex.com/x.pdf")):
            outcome = download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k",
                result={"pdf_url": "https://ex.com/x.pdf",
                        "pdf_url_fallbacks": ["https://alt.ex.com/x.pdf"]})
        assert outcome["ok"] is True
        assert outcome["tier"] == "oa_fallbacks"


# ============================================================
# Download log + provenance
# ============================================================

class TestDownloadLog:
    def test_log_captures_every_attempt(self, tmp_path):
        with patch.object(fdf, "_tier_direct", return_value=_make_fetch_result_fail("http_4xx", 403)), \
             patch.object(fdf, "_tier_oa_fallbacks", return_value=_make_fetch_result_fail("no_match", None)), \
             patch.object(fdf, "_tier_doi_negotiation", return_value=_make_fetch_result_fail("http_4xx", 404)), \
             patch.object(fdf, "_tier_openreview",
                           return_value=_make_fetch_result_ok("https://openreview.net/pdf?id=X")), \
             patch.object(fdf, "_tier_wayback") as way:
            outcome = download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result={"pdf_url": "https://ex.com/x.pdf", "title": "Foo"})
        tiers_in_log = [e["tier"] for e in outcome["log"]]
        assert tiers_in_log == ["direct", "oa_fallbacks", "doi_negotiation", "openreview"]
        # Failed entries have http_status / kind recorded
        assert outcome["log"][0]["http_status"] == 403
        assert outcome["log"][0]["kind"] == "http_4xx"
        assert outcome["log"][1]["kind"] == "no_match"
        # Winning entry short-circuits — wayback not logged
        way.assert_not_called()

    def test_provenance_stamped_with_winning_tier(self, tmp_path):
        with patch.object(fdf, "_tier_direct", return_value=_make_fetch_result_fail()), \
             patch.object(fdf, "_tier_oa_fallbacks",
                           return_value=_make_fetch_result_ok("https://alt.ex.com/x.pdf")):
            result = {"pdf_url": "https://ex.com/x.pdf",
                      "pdf_url_fallbacks": ["https://alt.ex.com/x.pdf"],
                      "files_origin": {}}
            download_with_fallback(
                "https://ex.com/x.pdf", str(tmp_path / "out.pdf"),
                bib_key="k", result=result)
        origin = get_origin(result, "pdf")
        assert origin["tier"] == "oa_fallbacks"
        assert origin["host"] == "alt.ex.com"


# ============================================================
# Individual tiers — mock requests.get
# ============================================================

class TestTierDirect:
    def test_success_writes_pdf(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        resp = MagicMock()
        resp.status_code = 200
        resp.url = "https://ex.com/x.pdf"
        resp.iter_content.return_value = iter([b"%PDF-1.4 body bytes"])
        with patch.object(fdf, "get_session") as gs:
            gs.return_value.get.return_value = resp
            r = fdf._tier_direct(ctx)
        assert r.ok is True
        assert os.path.isfile(tmp_path / "out.pdf")

    def test_non_pdf_response_fails_validation(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        resp = MagicMock()
        resp.status_code = 200
        resp.url = "https://ex.com/x.pdf"
        resp.iter_content.return_value = iter([b"<!DOCTYPE html>"])
        with patch.object(fdf, "get_session") as gs:
            gs.return_value.get.return_value = resp
            r = fdf._tier_direct(ctx)
        assert r.ok is False
        assert r.kind == "validation"
        assert not os.path.isfile(tmp_path / "out.pdf")

    def test_403_classified(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        resp = MagicMock(); resp.status_code = 403
        with patch.object(fdf, "get_session") as gs:
            gs.return_value.get.return_value = resp
            r = fdf._tier_direct(ctx)
        assert r.ok is False
        assert r.http_status == 403
        assert r.kind == "http_4xx"

    def test_no_url_returns_no_match(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        r = fdf._tier_direct(ctx)
        assert r.ok is False
        assert r.kind == "no_match"


class TestTierOaFallbacks:
    def test_walks_alternates_skipping_primary(self, tmp_path):
        ctx = FetchContext(
            url="https://primary.example.com/x.pdf",
            target_path=str(tmp_path / "out.pdf"), bib_key="k",
            result={"pdf_url_fallbacks": [
                "https://primary.example.com/x.pdf",    # same as primary — skip
                "https://alt1.example.com/x.pdf",       # try this
                "https://alt2.example.com/x.pdf",
            ]})
        call_log = []
        def fake_fetch(url, target, **kw):
            call_log.append(url)
            if "alt1" in url:
                return FetchResult(ok=True, final_url=url, elapsed_ms=50)
            return FetchResult(ok=False, kind="http_4xx", http_status=403)
        with patch.object(fdf, "_fetch_pdf", side_effect=fake_fetch):
            r = fdf._tier_oa_fallbacks(ctx)
        assert r.ok is True
        # Only alt1 was tried — walk short-circuits
        assert call_log == ["https://alt1.example.com/x.pdf"]

    def test_empty_list_returns_no_match(self, tmp_path):
        ctx = FetchContext(
            url="https://primary.example.com/x.pdf",
            target_path=str(tmp_path / "out.pdf"), bib_key="k",
            result={})
        r = fdf._tier_oa_fallbacks(ctx)
        assert r.ok is False
        assert r.kind == "no_match"


class TestTierDoiNegotiation:
    def test_no_doi_skipped(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={}, doi=None)
        r = fdf._tier_doi_negotiation(ctx)
        assert r.ok is False
        assert r.kind == "no_match"

    def test_uses_doi_org_with_accept_pdf(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={"doi": "10.1000/test"}, doi="10.1000/test")
        called = {}
        def fake_fetch(url, target, *, headers=None, timeout=30):
            called["url"] = url
            called["headers"] = headers or {}
            return FetchResult(ok=True, final_url=url, elapsed_ms=50)
        with patch.object(fdf, "_fetch_pdf", side_effect=fake_fetch):
            r = fdf._tier_doi_negotiation(ctx)
        assert r.ok is True
        assert called["url"] == "https://doi.org/10.1000/test"
        assert called["headers"].get("Accept") == "application/pdf"


class TestTierWayback:
    def test_cdx_miss_returns_no_match(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"archived_snapshots": {}}
        with patch.object(fdf, "get_session") as gs:
            gs.return_value.get.return_value = resp
            r = fdf._tier_wayback(ctx)
        assert r.ok is False
        assert r.kind == "no_match"

    def test_cdx_hit_fetches_id_variant(self, tmp_path):
        ctx = FetchContext(url="https://ex.com/x.pdf",
                           target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={})
        cdx_resp = MagicMock()
        cdx_resp.status_code = 200
        cdx_resp.json.return_value = {
            "archived_snapshots": {"closest": {
                "url": "https://web.archive.org/web/20231115120000/https://ex.com/x.pdf",
                "timestamp": "20231115120000"
            }}
        }
        fetched = {}
        def fake_fetch(url, target, *, timeout=30, **kw):
            fetched["url"] = url
            return FetchResult(ok=True, final_url=url, elapsed_ms=30)
        with patch.object(fdf, "get_session") as gs, \
             patch.object(fdf, "_fetch_pdf", side_effect=fake_fetch):
            gs.return_value.get.return_value = cdx_resp
            r = fdf._tier_wayback(ctx)
        assert r.ok is True
        # Must have used the id_ variant to skip Wayback's toolbar wrapper
        assert "id_/" in fetched["url"]


class TestTierOpenReview:
    def test_no_title_returns_no_match(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k", result={}, title=None)
        r = fdf._tier_openreview(ctx)
        assert r.ok is False
        assert r.kind == "no_match"

    def test_finds_match_and_fetches(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k",
                           result={"title": "Attention Is All You Need"},
                           title="Attention Is All You Need")
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {"notes": [
            {"id": "ABC123", "content": {"title": "Attention Is All You Need"}}
        ]}
        fetched = {}
        def fake_fetch(url, target, **kw):
            fetched["url"] = url
            return FetchResult(ok=True, final_url=url, elapsed_ms=200)
        with patch.object(fdf, "get_session") as gs, \
             patch.object(fdf, "_fetch_pdf", side_effect=fake_fetch):
            gs.return_value.get.return_value = search_resp
            r = fdf._tier_openreview(ctx)
        assert r.ok is True
        assert fetched["url"] == "https://openreview.net/pdf?id=ABC123"

    def test_low_overlap_result_rejected(self, tmp_path):
        ctx = FetchContext(url=None, target_path=str(tmp_path / "out.pdf"),
                           bib_key="k",
                           result={"title": "Attention Is All You Need"},
                           title="Attention Is All You Need")
        search_resp = MagicMock()
        search_resp.status_code = 200
        # Totally different paper — must be rejected
        search_resp.json.return_value = {"notes": [
            {"id": "WrongPaper", "content": {"title": "Something Completely Different Entirely"}}
        ]}
        with patch.object(fdf, "get_session") as gs:
            gs.return_value.get.return_value = search_resp
            r = fdf._tier_openreview(ctx)
        assert r.ok is False
        assert r.kind == "no_match"


class TestOaFallbacksFromAPIClients:
    """Integration: Unpaywall/OpenAlex return pdf_url_fallbacks which the
    lookup pipeline accumulates on result.pdf_url_fallbacks (§3.1/§3.2)."""

    def test_unpaywall_surfaces_oa_locations(self):
        from api_clients.unpaywall import lookup_unpaywall
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://a.com/x.pdf"},
            "oa_locations": [
                {"url_for_pdf": "https://a.com/x.pdf"},
                {"url_for_pdf": "https://b.com/x.pdf"},
                {"url_for_pdf": None},  # skipped
                {"url_for_pdf": "https://c.com/x.pdf"},
            ],
        }
        with patch("api_clients.unpaywall.get_session") as gs:
            gs.return_value.get.return_value = resp
            r = lookup_unpaywall("10.1000/test")
        assert r["pdf_url"] == "https://a.com/x.pdf"
        assert r["pdf_url_fallbacks"] == [
            "https://a.com/x.pdf",
            "https://b.com/x.pdf",
            "https://c.com/x.pdf",
        ]
