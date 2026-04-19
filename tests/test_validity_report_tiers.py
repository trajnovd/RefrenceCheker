"""Tests for v6.1 Phase D — tier info in the validity report.

Pins:
- Non-direct tier renders the explainer banner
- Direct tier does NOT render the banner (would be noise on every ref)
- download_log renders as a collapsed trace when it has >1 entry or failure
- download_log is suppressed when only single successful direct attempt
- Tier + captured_at appears in the Downloaded-source block
"""

import json
import os

import pytest
from unittest.mock import patch

import project_store
from validity_report import (
    build_validity_report, _download_log_trace_html, TIER_EXPLAINERS,
)


def _make_synthetic_project_with_tiers(tmp_path):
    slug = "tier-test"
    pdir = tmp_path / "projects" / slug
    pdir.mkdir(parents=True)
    # Minimal files so the PDF links don't break
    (pdir / "ref_wayback_pdf.pdf").write_bytes(b"%PDF-1.4 wayback")
    (pdir / "ref_wayback.md").write_text("## Full text\n\nBody.", encoding="utf-8")
    (pdir / "ref_direct_pdf.pdf").write_bytes(b"%PDF-1.4 direct")
    (pdir / "ref_direct.md").write_text("## Full text\n\nBody.", encoding="utf-8")
    tex = "\\cite{ref_wayback} and \\cite{ref_direct}."
    (pdir / "main.tex").write_text(tex, encoding="utf-8")

    citations = [
        {"bib_key": "ref_wayback", "line": 1,
         "position": tex.find("\\cite{ref_wayback}"),
         "end_position": tex.find("\\cite{ref_wayback}") + len("\\cite{ref_wayback}"),
         "cite_command": "\\cite{ref_wayback}",
         "context_before": "", "context_after": "",
         "claim_check_key": "ck_wayback"},
        {"bib_key": "ref_direct", "line": 1,
         "position": tex.find("\\cite{ref_direct}"),
         "end_position": tex.find("\\cite{ref_direct}") + len("\\cite{ref_direct}"),
         "cite_command": "\\cite{ref_direct}",
         "context_before": "", "context_after": "",
         "claim_check_key": "ck_direct"},
    ]
    results = [
        {"bib_key": "ref_wayback", "title": "Archived Paper",
         "authors": ["A. Archive"], "year": "2023",
         "status": "found_pdf", "sources": ["unpaywall"],
         "pdf_url": "https://dead.example.com/x.pdf",
         "files": {"pdf": "ref_wayback_pdf.pdf", "md": "ref_wayback.md"},
         "files_origin": {"pdf": {"tier": "wayback",
                                   "url": "https://web.archive.org/2023/...",
                                   "captured_at": "2026-04-20T10:00:00+00:00",
                                   "host": "web.archive.org"}},
         "download_log": [
             {"tier": "direct", "ok": False, "kind": "http_4xx", "http_status": 403,
              "final_url": "https://dead.example.com/x.pdf", "elapsed_ms": 450},
             {"tier": "oa_fallbacks", "ok": False, "kind": "no_match",
              "elapsed_ms": 12},
             {"tier": "wayback", "ok": True,
              "final_url": "https://web.archive.org/2023/...", "elapsed_ms": 820},
         ],
         "raw_bib": "@article{ref_wayback}",
         "ref_match": {"verdict": "matched", "manual": False}},
        {"bib_key": "ref_direct", "title": "Fresh Paper",
         "authors": ["B. Direct"], "year": "2024",
         "status": "found_pdf", "sources": ["openalex"],
         "pdf_url": "https://ok.example.com/x.pdf",
         "files": {"pdf": "ref_direct_pdf.pdf", "md": "ref_direct.md"},
         "files_origin": {"pdf": {"tier": "direct",
                                   "url": "https://ok.example.com/x.pdf",
                                   "captured_at": "2026-04-20T10:05:00+00:00",
                                   "host": "ok.example.com"}},
         "download_log": [
             {"tier": "direct", "ok": True,
              "final_url": "https://ok.example.com/x.pdf", "elapsed_ms": 200},
         ],
         "raw_bib": "@article{ref_direct}",
         "ref_match": {"verdict": "matched", "manual": False}},
    ]
    parsed_refs = [{"bib_key": r["bib_key"], "title": r.get("title"),
                    "raw_bib": r.get("raw_bib", "")} for r in results]
    claim_checks = {
        "ck_wayback": {"verdict": "not_supported", "confidence": 0.8,
                       "explanation": "Wrong paper.", "model": "gpt-5-mini"},
        "ck_direct": {"verdict": "supported", "confidence": 0.9,
                      "explanation": "Supports the claim.", "model": "gpt-5-mini"},
    }
    project = {
        "name": "Tier Test", "slug": slug,
        "bib_filename": "test.bib", "tex_filename": "main.tex", "tex_content": tex,
        "citations": citations, "results": results,
        "parsed_refs": parsed_refs, "claim_checks": claim_checks,
        "status": "completed", "total": len(results),
    }
    (pdir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    return slug, project


class TestTierExplainersInReport:
    def test_wayback_triggers_explainer_banner(self, tmp_path):
        slug, _ = _make_synthetic_project_with_tiers(tmp_path)
        with patch("validity_report.PROJECTS_DIR", str(tmp_path / "projects")), \
             patch("project_store.PROJECTS_DIR", str(tmp_path / "projects")):
            html, _p, _z = build_validity_report(slug)
        # The wayback entry went into Problematic (claim not_supported).
        # Its Downloaded-source block should show the wayback explainer.
        assert TIER_EXPLAINERS["wayback"] in html

    def test_direct_tier_has_no_explainer(self, tmp_path):
        slug, _ = _make_synthetic_project_with_tiers(tmp_path)
        with patch("validity_report.PROJECTS_DIR", str(tmp_path / "projects")), \
             patch("project_store.PROJECTS_DIR", str(tmp_path / "projects")):
            html, _p, _z = build_validity_report(slug)
        # "direct" maps to None in TIER_EXPLAINERS — no banner string like
        # "direct — may differ..." should appear.
        assert "direct — may differ" not in html


class TestDownloadLogTrace:
    def test_multi_attempt_log_renders_table(self):
        log = [
            {"tier": "direct", "ok": False, "kind": "http_4xx", "http_status": 403,
             "final_url": "https://a.com/x.pdf", "elapsed_ms": 450},
            {"tier": "wayback", "ok": True, "final_url": "https://web.archive.org/...",
             "elapsed_ms": 820},
        ]
        html = _download_log_trace_html(log)
        assert "<details" in html
        assert "direct" in html
        assert "wayback" in html
        assert "HTTP 403" in html
        assert "820 ms" in html

    def test_single_direct_success_returns_empty(self):
        """The common case — don't clutter the report."""
        log = [{"tier": "direct", "ok": True,
                "final_url": "https://a.com/x.pdf", "elapsed_ms": 150}]
        assert _download_log_trace_html(log) == ""

    def test_single_failed_direct_still_renders(self):
        """A lone failed direct attempt IS useful info."""
        log = [{"tier": "direct", "ok": False, "kind": "http_4xx",
                "http_status": 403, "elapsed_ms": 400}]
        html = _download_log_trace_html(log)
        assert "<details" in html
        assert "HTTP 403" in html

    def test_empty_log_returns_empty(self):
        assert _download_log_trace_html(None) == ""
        assert _download_log_trace_html([]) == ""


class TestDownloadedSourceLineShowsTier:
    def test_tier_line_in_problematic_block(self, tmp_path):
        slug, _ = _make_synthetic_project_with_tiers(tmp_path)
        with patch("validity_report.PROJECTS_DIR", str(tmp_path / "projects")), \
             patch("project_store.PROJECTS_DIR", str(tmp_path / "projects")):
            html, _p, _z = build_validity_report(slug)
        # The "Downloaded via:" line for the wayback ref should be visible
        assert "Downloaded via:" in html
        assert "wayback" in html
        assert "2026-04-20" in html   # captured_at date
