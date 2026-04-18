"""Tests for validity_report.

Synthetic project.json with one citation in each severity bucket; assert that
build_validity_report:
- Classifies each into the right bucket
- Produces one HTML block per citation occurrence (so a 3x cited key → 3 blocks)
- Copies files only for problematic + partial refs into references/
- Builds references.zip with a top-level references/ prefix
- Renders key strings into the HTML
"""

import json
import os
import zipfile
from unittest.mock import patch

import pytest

from validity_report import (
    _classify, _build_summary_stats, build_validity_report,
    SEVERITY_ORDER,
)


# ============================================================
# _classify
# ============================================================

class TestClassify:
    def _ref(self, **overrides):
        ref = {"bib_key": "k", "status": "found_pdf",
               "files": {"md": "k.md"}, "ref_match": None}
        ref.update(overrides)
        return ref

    def test_missing_key_when_no_ref(self):
        assert _classify({"bib_key": "k"}, None, None) == "missing_key"

    def test_parse_error_status(self):
        assert _classify({"bib_key": "k"},
                          self._ref(status="parse_error"), None) == "parse_error"
        assert _classify({"bib_key": "k"},
                          self._ref(status="insufficient_data"), None) == "parse_error"

    def test_broken_url(self):
        assert _classify({"bib_key": "k"},
                          self._ref(status="bib_url_unreachable"), None) == "broken_url"

    def test_identity_not_matched(self):
        assert _classify({"bib_key": "k"},
                          self._ref(ref_match={"verdict": "not_matched"}),
                          None) == "identity_not_matched"

    def test_identity_manual_not_matched(self):
        assert _classify({"bib_key": "k"},
                          self._ref(ref_match={"verdict": "manual_not_matched"}),
                          None) == "identity_not_matched"

    def test_claim_not_supported(self):
        assert _classify({"bib_key": "k"}, self._ref(),
                          {"verdict": "not_supported"}) == "claim_not_supported"

    def test_no_md(self):
        assert _classify({"bib_key": "k"},
                          self._ref(files={}), None) == "no_md"

    def test_identity_unverifiable(self):
        assert _classify({"bib_key": "k"},
                          self._ref(ref_match={"verdict": "unverifiable"}),
                          None) == "identity_unverifiable"

    def test_partial(self):
        assert _classify({"bib_key": "k"}, self._ref(),
                          {"verdict": "partial"}) == "partial"

    def test_clean_when_everything_passes(self):
        assert _classify(
            {"bib_key": "k"},
            self._ref(ref_match={"verdict": "matched"}),
            {"verdict": "supported"}) == "clean"

    def test_severity_order_includes_all_buckets(self):
        # Sanity: SEVERITY_ORDER must list every bucket the classifier returns
        observed = {"missing_key", "parse_error", "broken_url",
                    "identity_not_matched", "claim_not_supported",
                    "no_md", "identity_unverifiable", "partial", "clean"}
        assert observed.issubset(set(SEVERITY_ORDER))

    def test_identity_not_matched_dominates_partial(self):
        """Identity-not-matched outranks claim-partial: a wrong-document citation
        is more urgent than a tangentially-related-but-correct one."""
        ref = self._ref(ref_match={"verdict": "not_matched"})
        assert _classify({"bib_key": "k"}, ref,
                          {"verdict": "partial"}) == "identity_not_matched"


# ============================================================
# Synthetic project for end-to-end test
# ============================================================

def _make_synthetic_project(tmp_path):
    """Build a project dir with a project.json covering every severity bucket."""
    slug = "synth"
    project_dir = tmp_path / "projects" / slug
    project_dir.mkdir(parents=True)

    # Create reference files for the refs that have them
    (project_dir / "okref_pdf.pdf").write_bytes(b"%PDF-1.4 fake pdf bytes")
    (project_dir / "okref.md").write_text(
        "# OK Ref\n\n## Full text\n\nBody text of the OK reference.",
        encoding="utf-8")
    (project_dir / "broken_ref.md").write_text(
        "# Wrong Paper\n\n## Full text\n\nDifferent paper content.",
        encoding="utf-8")
    (project_dir / "broken_ref_pdf.pdf").write_bytes(b"%PDF-1.4 wrong paper bytes")
    (project_dir / "partial_ref.md").write_text(
        "# Partial Match\n\n## Full text\n\nTangentially related text.",
        encoding="utf-8")
    (project_dir / "partial_ref_pdf.pdf").write_bytes(b"%PDF-1.4 partial bytes")
    (project_dir / "clean_ref.md").write_text(
        "# Clean Reference\n\n## Full text\n\nBody.", encoding="utf-8")
    (project_dir / "clean_ref_pdf.pdf").write_bytes(b"%PDF-1.4 clean bytes")

    tex_content = (
        "\\section{Intro}\n"
        "Line 1\nLine 2\n"
        "We cite the broken \\cite{broken_ref} here.\n"   # line 4
        "Then a missing \\cite{not_in_bib}.\n"             # line 5
        "Also a clean one \\cite{clean_ref}.\n"             # line 6
        "And a partial \\cite{partial_ref}.\n"              # line 7
        "And the same broken cited again \\cite{broken_ref}.\n"  # line 8 — 2nd occurrence
        "And no .md content here \\cite{no_md_ref}.\n"      # line 9
        "And a parse-error one \\cite{parse_err_ref}.\n"    # line 10
        "And an unverifiable \\cite{unver_ref}.\n"          # line 11
        "And a not-supported \\cite{not_supp_ref}.\n"       # line 12
        "And a broken bib URL \\cite{bad_url_ref}.\n"       # line 13
    )
    (project_dir / "main.tex").write_text(tex_content, encoding="utf-8")

    # citations: a row per occurrence (matches the rendered structure)
    def cite(bib_key, line, position):
        end_position = position + len(f"\\cite{{{bib_key}}}")
        return {
            "bib_key": bib_key, "line": line,
            "position": position, "end_position": end_position,
            "cite_command": f"\\cite{{{bib_key}}}",
            "context_before": "", "context_after": "",
            "claim_check_key": f"ck_{bib_key}_{line}",
        }

    # Compute positions in tex_content
    positions = {}
    for tag in ["broken_ref", "not_in_bib", "clean_ref", "partial_ref",
                "no_md_ref", "parse_err_ref", "unver_ref",
                "not_supp_ref", "bad_url_ref"]:
        positions[tag] = tex_content.find(f"\\cite{{{tag}}}")
    # broken_ref appears twice — find both
    second_broken = tex_content.find(f"\\cite{{broken_ref}}",
                                      positions["broken_ref"] + 1)

    citations = [
        cite("broken_ref", 4, positions["broken_ref"]),
        cite("not_in_bib", 5, positions["not_in_bib"]),
        cite("clean_ref",  6, positions["clean_ref"]),
        cite("partial_ref", 7, positions["partial_ref"]),
        cite("broken_ref", 8, second_broken),
        cite("no_md_ref",  9, positions["no_md_ref"]),
        cite("parse_err_ref", 10, positions["parse_err_ref"]),
        cite("unver_ref", 11, positions["unver_ref"]),
        cite("not_supp_ref", 12, positions["not_supp_ref"]),
        cite("bad_url_ref", 13, positions["bad_url_ref"]),
    ]

    results = [
        {"bib_key": "broken_ref", "title": "Wrong Paper", "authors": ["Bib Author"],
         "year": "2025", "journal": "X", "doi": None,
         "abstract": None, "pdf_url": "https://example.com/x.pdf",
         "url": "https://example.com/x", "citation_count": None,
         "sources": ["openalex"], "status": "found_pdf", "error": None,
         "raw_bib": "@article{broken_ref, title={Real Title}, author={Bib Author}}",
         "files": {"pdf": "broken_ref_pdf.pdf", "md": "broken_ref.md"},
         "ref_match": {"verdict": "not_matched", "title_found": False,
                       "authors_found": False,
                       "evidence": "Different paper than claimed.",
                       "model": "gpt-5-mini",
                       "checked_at": "2026-04-18T10:00:00+00:00", "manual": False}},
        {"bib_key": "clean_ref", "title": "Clean", "authors": ["A"],
         "year": "2024", "journal": "J", "doi": None, "abstract": None,
         "pdf_url": "https://example.com/c.pdf", "url": None,
         "citation_count": 5, "sources": ["openalex"],
         "status": "found_pdf", "error": None,
         "raw_bib": "@article{clean_ref, title={Clean}}",
         "files": {"pdf": "clean_ref_pdf.pdf", "md": "clean_ref.md"},
         "ref_match": {"verdict": "matched", "title_found": True,
                       "authors_found": True, "evidence": "Match.",
                       "model": "gpt-5-mini", "manual": False}},
        {"bib_key": "partial_ref", "title": "Partial", "authors": ["B"],
         "year": "2024", "journal": "J", "doi": None,
         "abstract": None, "pdf_url": None, "url": None,
         "citation_count": None, "sources": [], "status": "found_pdf",
         "error": None, "raw_bib": "@article{partial_ref, title={Partial}}",
         "files": {"pdf": "partial_ref_pdf.pdf", "md": "partial_ref.md"},
         "ref_match": {"verdict": "matched", "manual": False,
                       "evidence": "ok"}},
        {"bib_key": "no_md_ref", "title": "No MD", "authors": [], "year": None,
         "journal": None, "doi": None, "abstract": None, "pdf_url": None,
         "url": None, "citation_count": None, "sources": [],
         "status": "found_pdf", "error": None,
         "raw_bib": "@article{no_md_ref}", "files": {}},
        {"bib_key": "parse_err_ref", "title": None, "authors": [], "year": None,
         "journal": None, "doi": None, "status": "insufficient_data",
         "error": "no title", "raw_bib": "", "files": {}},
        {"bib_key": "unver_ref", "title": "Unver", "authors": [], "year": "2024",
         "journal": "J", "doi": None, "abstract": None, "pdf_url": None,
         "url": None, "citation_count": None, "sources": [],
         "status": "found_pdf", "error": None,
         "raw_bib": "@article{unver_ref}",
         "files": {"md": "okref.md"},
         "ref_match": {"verdict": "unverifiable", "evidence": "could not decide"}},
        {"bib_key": "not_supp_ref", "title": "Not Supp", "authors": [],
         "year": "2024", "journal": "J", "doi": None, "abstract": None,
         "pdf_url": None, "url": None, "citation_count": None,
         "sources": [], "status": "found_pdf", "error": None,
         "raw_bib": "@article{not_supp_ref}",
         "files": {"md": "okref.md", "pdf": "okref_pdf.pdf"},
         "ref_match": {"verdict": "matched", "manual": False}},
        {"bib_key": "bad_url_ref", "title": "Bad URL Ref", "authors": [],
         "year": "2024", "journal": None, "doi": None,
         "abstract": None, "pdf_url": None,
         "url": "https://example.com/broken", "citation_count": None,
         "sources": ["URL"], "status": "bib_url_unreachable",
         "error": "Bib URL returned HTTP 403",
         "bib_url_failure": {"http_status": 403, "kind": "http_4xx"},
         "raw_bib": "@misc{bad_url_ref, url={https://example.com/broken}}",
         "files": {}},
    ]

    parsed_refs = [{"bib_key": r["bib_key"], "title": r.get("title"),
                    "raw_bib": r.get("raw_bib", "")} for r in results]

    claim_checks = {
        "ck_broken_ref_4": {"verdict": "not_supported", "confidence": 0.8,
                              "explanation": "Wrong paper.",
                              "evidence_quote": "different content",
                              "model": "gpt-5-mini",
                              "checked_at": "2026-04-18T10:00:00+00:00"},
        "ck_clean_ref_6": {"verdict": "supported", "confidence": 0.9,
                            "explanation": "Backs the claim.",
                            "model": "gpt-5-mini"},
        "ck_partial_ref_7": {"verdict": "partial", "confidence": 0.5,
                              "explanation": "Tangential.",
                              "model": "gpt-5-mini"},
        "ck_broken_ref_8": {"verdict": "not_supported", "confidence": 0.7,
                              "explanation": "Same wrong paper, second cite.",
                              "model": "gpt-5-mini"},
        "ck_not_supp_ref_12": {"verdict": "not_supported", "confidence": 0.85,
                                "explanation": "Reference doesn't address claim.",
                                "model": "gpt-5-mini"},
        # parse_err_ref / unver_ref / no_md_ref / bad_url_ref / not_in_bib have no claim check
    }

    project = {
        "name": "Synthetic Test", "slug": slug,
        "bib_filename": "test.bib", "tex_filename": "main.tex",
        "tex_content": tex_content,
        "citations": citations, "results": results,
        "parsed_refs": parsed_refs, "claim_checks": claim_checks,
        "status": "completed", "total": len(results),
    }

    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps(project, indent=2), encoding="utf-8")
    return slug, project_dir, project


# ============================================================
# Build summary stats
# ============================================================

class TestBuildSummary:
    def test_counts_per_citation_for_claims(self, tmp_path):
        slug, _, project = _make_synthetic_project(tmp_path)
        # Build rows the same way build_validity_report does
        from validity_report import _classify
        refs_by_key = {r["bib_key"]: r for r in project["results"]}
        rows = []
        for idx, c in enumerate(project["citations"]):
            ref = refs_by_key.get(c["bib_key"])
            cc = project["claim_checks"].get(c.get("claim_check_key"))
            rows.append({"idx": idx, "citation": c, "ref": ref,
                         "claim_check": cc, "severity": _classify(c, ref, cc)})
        s = _build_summary_stats(project, rows)
        assert s["total_refs"] == 8
        assert s["total_cites"] == 10                # 9 unique keys, broken_ref cited 2x
        assert s["claim"]["not_supported"] == 3      # broken_ref x2 + not_supp_ref
        assert s["claim"]["partial"] == 1
        assert s["claim"]["supported"] == 1
        # Identity counts (per ref): broken_ref=not_matched, clean+partial+not_supp=matched(3), unver=unverifiable, no_md+parse_err+bad_url=unchecked
        assert s["identity"]["not_matched"] == 1
        assert s["identity"]["matched"] == 3
        assert s["identity"]["unverifiable"] == 1
        assert s["identity"]["unchecked"] == 3


# ============================================================
# End-to-end build
# ============================================================

class TestBuildValidityReport:
    def _patched_settings(self, tmp_path):
        """Patch PROJECTS_DIR everywhere it's used so the synthetic project
        is found. Returns a context manager."""
        from contextlib import ExitStack
        es = ExitStack()
        es.enter_context(patch("validity_report.PROJECTS_DIR",
                                str(tmp_path / "projects")))
        es.enter_context(patch("project_store.PROJECTS_DIR",
                                str(tmp_path / "projects")))
        return es

    def test_builds_html_and_zip(self, tmp_path):
        slug, project_dir, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            html, html_path, zip_path = build_validity_report(slug)

        assert os.path.isfile(html_path)
        assert os.path.isfile(zip_path)
        assert os.path.basename(html_path) == f"{slug}_report.html"
        assert "Citation Validity Report" in html
        assert "Synthetic Test" in html

    def test_zip_contains_only_problematic_and_partial_refs(self, tmp_path):
        """clean_ref's files must NOT be in the zip — only broken_ref +
        partial_ref + not_supp_ref + unver_ref (which appear in problematic
        / partial sections)."""
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            _, _, zip_path = build_validity_report(slug)
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        # broken_ref → in problematic (not_matched + not_supported)
        assert "references/broken_ref_pdf.pdf" in names
        assert "references/broken_ref.md" in names
        # partial_ref → in partial section
        assert "references/partial_ref_pdf.pdf" in names
        assert "references/partial_ref.md" in names
        # clean_ref → NOT in zip
        assert "references/clean_ref_pdf.pdf" not in names
        assert "references/clean_ref.md" not in names

    def test_zip_paths_have_references_prefix(self, tmp_path):
        """Extracting next to the HTML must reproduce the references/ subfolder."""
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            _, _, zip_path = build_validity_report(slug)
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert name.startswith("references/"), \
                    f"zip entry {name!r} not under references/ prefix"

    def test_per_occurrence_blocks_for_repeated_cites(self, tmp_path):
        """broken_ref is cited twice (line 4 and line 8) — must appear as
        TWO separate blocks, not one block with two sub-claim-checks."""
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            html, _, _ = build_validity_report(slug)
        # Count anchor ids for broken_ref
        anchor_count = html.count('id="cite-')
        # 10 total citations → 10 blocks across all sections (problematic + partial + clean lists clean as one-liners not blocks)
        # Problematic (severity != partial/clean): broken_ref x2, not_in_bib, no_md_ref, parse_err_ref, unver_ref, not_supp_ref, bad_url_ref = 8
        # Partial: partial_ref = 1
        # Clean: clean_ref = 1 (one-liner, no anchor)
        # So problematic blocks = 8, partial blocks = 1 → 9 anchors
        assert anchor_count == 9

    def test_html_contains_section_headers(self, tmp_path):
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            html, _, _ = build_validity_report(slug)
        assert "Problematic citations" in html
        assert "Partial citations" in html
        assert "Clean citations" in html
        assert "Methodology" in html
        # Summary counters present
        assert "CITATIONS NEEDING ATTENTION" in html
        # Download link for the zip
        assert 'href="references.zip"' in html

    def test_html_contains_severity_badges(self, tmp_path):
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            html, _, _ = build_validity_report(slug)
        # Each severity bucket label should appear at least once
        for bucket_label in ["TITLE OR AUTHORS DO NOT MATCH", "CLAIM NOT SUPPORTED",
                              "BROKEN BIB URL", "REFERENCE NOT FOUND",
                              "IDENTITY UNVERIFIABLE", "CITATION KEY NOT IN .BIB",
                              "BIB PARSE ERROR", "CLAIM PARTIAL"]:
            assert bucket_label in html, f"missing badge label: {bucket_label}"

    def test_local_file_links_use_references_prefix(self, tmp_path):
        slug, _, _ = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            html, _, _ = build_validity_report(slug)
        # Source-file links inside cite blocks must use the relative
        # references/<filename> path
        assert 'href="references/broken_ref_pdf.pdf"' in html
        assert 'href="references/broken_ref.md"' in html

    def test_rebuild_wipes_previous(self, tmp_path):
        """A second build must not keep stale files from the first run."""
        slug, project_dir, project = _make_synthetic_project(tmp_path)
        with self._patched_settings(tmp_path):
            build_validity_report(slug)
            refs_dir = project_dir / "validity-report" / "references"
            assert (refs_dir / "broken_ref_pdf.pdf").is_file()
            # Drop broken_ref from the project so it stops being problematic
            project["citations"] = [c for c in project["citations"]
                                     if c["bib_key"] != "broken_ref"]
            project["results"] = [r for r in project["results"]
                                   if r["bib_key"] != "broken_ref"]
            (project_dir / "project.json").write_text(
                json.dumps(project), encoding="utf-8")
            build_validity_report(slug)
            # broken_ref's files should no longer be in the bundle
            assert not (refs_dir / "broken_ref_pdf.pdf").is_file()

    def test_missing_project_raises(self, tmp_path):
        with self._patched_settings(tmp_path):
            with pytest.raises(ValueError):
                build_validity_report("does-not-exist")
