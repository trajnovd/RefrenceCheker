"""Tests for v6.1 A3 telemetry + ref-match re-check on tier change."""

import json
import os

import pytest
from unittest.mock import patch

import project_store


def _write_project(tmp_path, data):
    slug = data.get("slug") or "proj"
    pdir = tmp_path / "projects" / slug
    pdir.mkdir(parents=True, exist_ok=True)
    with open(pdir / "project.json", "w", encoding="utf-8") as f:
        json.dump(data, f)
    return slug


class TestComputeDownloadStats:
    def _patched_store(self, tmp_path):
        return patch.object(project_store, "PROJECTS_DIR",
                             str(tmp_path / "projects"))

    def test_missing_project_returns_none(self, tmp_path):
        with self._patched_store(tmp_path):
            assert project_store.compute_download_stats("nope") is None

    def test_counts_per_tier_from_files_origin(self, tmp_path):
        slug = _write_project(tmp_path, {
            "slug": "p",
            "results": [
                {"bib_key": "a",
                 "files_origin": {"pdf": {"tier": "direct", "url": "https://a.com/a.pdf"}},
                 "download_log": []},
                {"bib_key": "b",
                 "files_origin": {"pdf": {"tier": "openreview"}},
                 "download_log": []},
                {"bib_key": "c",
                 "files_origin": {"pdf": {"tier": "direct"}},
                 "download_log": []},
                {"bib_key": "d",  # no download
                 "download_log": []},
            ],
        })
        with self._patched_store(tmp_path):
            stats = project_store.compute_download_stats(slug)
        assert stats["per_tier"] == {"direct": 2, "openreview": 1}

    def test_failed_by_host_aggregates(self, tmp_path):
        slug = _write_project(tmp_path, {
            "slug": "p",
            "results": [
                {"bib_key": "a", "download_log": [
                    {"tier": "direct", "ok": False, "final_url": "https://papers.ssrn.com/abc"},
                ]},
                {"bib_key": "b", "download_log": [
                    {"tier": "direct", "ok": False, "final_url": "https://papers.ssrn.com/def"},
                    {"tier": "wayback", "ok": True, "final_url": "https://web.archive.org/x"},
                ]},
                {"bib_key": "c", "download_log": [
                    {"tier": "direct", "ok": False, "final_url": "https://econstor.eu/y"},
                ]},
            ],
        })
        with self._patched_store(tmp_path):
            stats = project_store.compute_download_stats(slug)
        assert stats["failed_by_host"]["papers.ssrn.com"] == 2
        assert stats["failed_by_host"]["econstor.eu"] == 1

    def test_top_blocked_sorted_with_suggestion(self, tmp_path):
        slug = _write_project(tmp_path, {
            "slug": "p",
            "results": [
                {"download_log": [{"tier": "direct", "ok": False,
                                   "final_url": "https://papers.ssrn.com/" + str(i)}]
                 for i in range(3)}[0:1][0]  # placeholder; real entries below
                for _ in range(0)  # empty; we'll append below
            ],
        })
        # Construct properly — 7 SSRN failures, 3 econstor, 1 EUR-Lex
        data = {"slug": "p", "results": []}
        for i in range(7):
            data["results"].append({"bib_key": f"s{i}", "download_log": [
                {"tier": "direct", "ok": False, "final_url": f"https://papers.ssrn.com/{i}"}
            ]})
        for i in range(3):
            data["results"].append({"bib_key": f"e{i}", "download_log": [
                {"tier": "direct", "ok": False, "final_url": f"https://econstor.eu/{i}"}
            ]})
        data["results"].append({"bib_key": "eu1", "download_log": [
            {"tier": "direct", "ok": False, "final_url": "https://eur-lex.europa.eu/ai-act"}
        ]})
        slug = _write_project(tmp_path, data)
        with self._patched_store(tmp_path):
            stats = project_store.compute_download_stats(slug)
        top = stats["top_blocked"]
        # Sorted by count descending
        assert top[0]["host"] == "papers.ssrn.com"
        assert top[0]["refs"] == 7
        assert top[0]["suggested"] == "curl_cffi"
        assert top[1]["host"] == "econstor.eu"
        assert top[1]["suggested"] == "curl_cffi"
        assert top[2]["host"] == "eur-lex.europa.eu"
        assert top[2]["suggested"] == "playwright"

    def test_successful_attempts_not_in_failed_hosts(self, tmp_path):
        slug = _write_project(tmp_path, {
            "slug": "p",
            "results": [
                {"download_log": [
                    {"tier": "direct", "ok": True, "final_url": "https://ok.com/x.pdf"},
                ]},
            ],
        })
        with self._patched_store(tmp_path):
            stats = project_store.compute_download_stats(slug)
        assert stats["failed_by_host"] == {}


class TestSuggestTier:
    def test_ssrn_recommends_curl_cffi(self):
        from project_store import _suggest_tier_for
        assert _suggest_tier_for("papers.ssrn.com") == "curl_cffi"

    def test_researchgate_recommends_curl_cffi(self):
        from project_store import _suggest_tier_for
        assert _suggest_tier_for("researchgate.net") == "curl_cffi"

    def test_eur_lex_recommends_playwright(self):
        from project_store import _suggest_tier_for
        assert _suggest_tier_for("eur-lex.europa.eu") == "playwright"

    def test_unknown_host_recommends_manual(self):
        from project_store import _suggest_tier_for
        assert _suggest_tier_for("random.example.com") == "manual_upload"


class TestRefMatchRecheckOnTierChange:
    """When the winning tier for a ref changes between runs, auto ref-match
    must fire regardless of the auto_check_on_download setting (§11.13)."""

    def test_tier_unchanged_skips_recheck_when_auto_off(self, tmp_path, monkeypatch):
        """Tier stayed the same → no forced recheck. auto_check_on_download
        is off, so no recheck happens at all."""
        import app
        # Patch settings to have auto_check_on_download=False
        def fake_settings(): return {"enabled": True, "auto_check_on_download": False}
        monkeypatch.setattr("config.get_reference_match_settings", fake_settings)
        monkeypatch.setattr("config.get_openai_api_key", lambda: "dummy")

        with patch.object(app, "_current_pdf_tier", return_value="direct"), \
             patch("reference_matcher.check_and_save") as mock_check:
            # previous_tier=direct, current=direct → no change, no recheck
            # Inline to avoid the background thread timing
            app._maybe_auto_check_ref_match("slug", "k", previous_tier="direct")
            import time as _t; _t.sleep(0.1)  # give the daemon thread a chance to run
        mock_check.assert_not_called()

    def test_tier_change_forces_recheck_even_when_auto_off(self, tmp_path, monkeypatch):
        """previous=direct, current=wayback → force recheck despite the
        auto_check_on_download=False setting."""
        import app
        def fake_settings(): return {"enabled": True, "auto_check_on_download": False}
        monkeypatch.setattr("config.get_reference_match_settings", fake_settings)
        monkeypatch.setattr("config.get_openai_api_key", lambda: "dummy")

        with patch.object(app, "_current_pdf_tier", return_value="wayback"), \
             patch("reference_matcher.check_and_save") as mock_check:
            app._maybe_auto_check_ref_match("slug", "k", previous_tier="direct")
            import time as _t; _t.sleep(0.2)
        mock_check.assert_called_once_with("slug", "k", force=True)
