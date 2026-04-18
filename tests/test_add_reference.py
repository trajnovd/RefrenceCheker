"""Tests for the manual Add Reference flow.

Covers:
- project_store.add_parsed_ref (append + dedupe by bib_key)
- POST /api/projects/<slug>/add-reference (validation, parsed_ref creation,
  background lookup spawn, stale "key not found" verdict invalidation, conflict on duplicate)
"""

import os
import json
import time
from unittest.mock import patch, MagicMock
import pytest

import project_store
from app import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Create a real project on disk under tmp_path; patch PROJECTS_DIR globally."""
    monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
    proj = project_store.create_project("Test Project")
    return proj


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
    app = create_app()
    return app.test_client()


# ============================================================
# project_store.add_parsed_ref
# ============================================================

class TestAddParsedRef:
    def test_appends_new_ref(self, project):
        ok = project_store.add_parsed_ref(project["slug"], {
            "bib_key": "smith2020", "title": "Title"
        })
        assert ok is True
        reloaded = project_store.get_project(project["slug"])
        keys = [r.get("bib_key") for r in reloaded.get("parsed_refs", [])]
        assert "smith2020" in keys
        assert reloaded["total"] == 1

    def test_duplicate_is_rejected(self, project):
        slug = project["slug"]
        ok1 = project_store.add_parsed_ref(slug, {"bib_key": "x", "title": "A"})
        ok2 = project_store.add_parsed_ref(slug, {"bib_key": "x", "title": "B"})
        assert ok1 is True
        assert ok2 is False
        reloaded = project_store.get_project(slug)
        # Original entry preserved, second rejected
        titles = [r["title"] for r in reloaded["parsed_refs"]]
        assert titles == ["A"]

    def test_missing_bib_key_rejected(self, project):
        assert project_store.add_parsed_ref(project["slug"], {"title": "X"}) is False

    def test_unknown_slug_returns_false(self):
        assert project_store.add_parsed_ref("does-not-exist", {"bib_key": "k"}) is False


# ============================================================
# /api/projects/<slug>/add-reference
# ============================================================

class TestAddReferenceRoute:
    SAMPLE_BIB = """@article{anyKey,
  title = {Added Title},
  author = {Smith, John and Jones, Kate},
  year = {2024},
  doi = {10.1/abc},
  journal = {Journal of Examples},
}"""

    def _patch_lookup(self, bib_key="added2024"):
        """Patch process_reference + download_reference_files to be no-op deterministic."""
        return patch("app.process_reference", return_value={
            "bib_key": bib_key, "title": "Added Title",
            "authors": ["Smith, John", "Jones, Kate"], "year": "2024",
            "doi": "10.1/abc",
            "abstract": None, "pdf_url": None, "url": None,
            "citation_count": None, "sources": ["manual"],
            "status": "found_abstract",
        }), patch("app.download_reference_files", return_value={})

    def test_validates_required_fields(self, client, project):
        slug = project["slug"]
        # Missing bib_key
        r = client.post(f"/api/projects/{slug}/add-reference",
                        json={"bib_text": self.SAMPLE_BIB})
        assert r.status_code == 400
        assert b"bib_key" in r.data

        # Missing bib_text
        r = client.post(f"/api/projects/{slug}/add-reference",
                        json={"bib_key": "k"})
        assert r.status_code == 400
        assert b"bib_text" in r.data

    def test_rejects_unparseable_bib_text(self, client, project):
        slug = project["slug"]
        r = client.post(f"/api/projects/{slug}/add-reference",
                        json={"bib_key": "k", "bib_text": "not a bibtex entry at all"})
        assert r.status_code == 400
        # Either the parser raised, or no usable entry was found
        assert b"BibTeX" in r.data or b"entry" in r.data

    def test_rejects_entry_without_title(self, client, project):
        slug = project["slug"]
        bad = "@article{x, year = {2024}}"
        r = client.post(f"/api/projects/{slug}/add-reference",
                        json={"bib_key": "k", "bib_text": bad})
        assert r.status_code == 400

    def test_parses_bibtex_and_overrides_key(self, client, project):
        """The pasted entry's internal key should be replaced with the citation's bib_key."""
        slug = project["slug"]
        p_proc, p_dl = self._patch_lookup(bib_key="added2024")
        with p_proc, p_dl:
            r = client.post(f"/api/projects/{slug}/add-reference",
                            json={"bib_key": "added2024", "bib_text": self.SAMPLE_BIB})
        assert r.status_code == 202

        reloaded = project_store.get_project(slug)
        added = [r for r in reloaded["parsed_refs"] if r["bib_key"] == "added2024"]
        assert len(added) == 1, "parsed_ref must be stored under the supplied bib_key"
        # Original key 'anyKey' from the BibTeX must NOT remain
        assert not any(r["bib_key"] == "anyKey" for r in reloaded["parsed_refs"])
        # Title + DOI extracted by the parser
        assert added[0]["title"] == "Added Title"
        assert added[0]["doi"] == "10.1/abc"
        assert added[0]["manually_added"] is True
        # raw_bib carries the corrected key, not the original
        assert "{added2024," in added[0]["raw_bib"]
        assert "{anyKey," not in added[0]["raw_bib"]

        # Background lookup landed
        for _ in range(50):
            reloaded = project_store.get_project(slug)
            if any(r.get("bib_key") == "added2024" for r in reloaded.get("results", [])):
                break
            time.sleep(0.05)
        results = [r for r in project_store.get_project(slug).get("results", [])
                   if r.get("bib_key") == "added2024"]
        assert len(results) == 1
        assert results[0]["title"] == "Added Title"

    def test_duplicate_bib_key_returns_409(self, client, project):
        slug = project["slug"]
        project_store.save_result(slug, {"bib_key": "dup", "title": "Existing"})
        r = client.post(f"/api/projects/{slug}/add-reference",
                        json={"bib_key": "dup", "bib_text": self.SAMPLE_BIB})
        assert r.status_code == 409

    def test_unknown_project_returns_404(self, client):
        r = client.post("/api/projects/does-not-exist/add-reference",
                        json={"bib_key": "k", "bib_text": self.SAMPLE_BIB})
        assert r.status_code == 404

    def test_clears_stale_key_not_found_verdicts(self, client, project):
        """Adding the missing reference should drop any 'citation key not found'
        verdicts pointing at it, so they get re-checked next batch run."""
        slug = project["slug"]
        # Pre-seed: a citation that referenced "ghost", and a stale verdict for it
        from claim_checker import _empty_verdict
        verdict = _empty_verdict("Citation key not found in project results.")
        project_store.save_claim_check(slug, "ghost-ck-1", verdict)
        # Manually set the citation list (not via tex upload, simpler)
        proj = project_store.get_project(slug)
        proj["citations"] = [
            {"bib_key": "ghost", "position": 0, "end_position": 10,
             "line": 1, "claim_check_key": "ghost-ck-1"},
            {"bib_key": "other", "position": 20, "end_position": 30,
             "line": 2, "claim_check_key": "other-ck"},
        ]
        # Save by writing through update_project or direct re-write
        from datetime import datetime
        import json as _j
        from pathlib import Path
        proj_path = Path(project_store.PROJECTS_DIR) / slug / "project.json"
        proj["updated_at"] = datetime.now().isoformat()
        proj_path.write_text(_j.dumps(proj, indent=2), encoding="utf-8")

        # Pre-seed manual verdict for "other" — must NOT be cleared
        from claim_checker import make_manual_verdict
        manual_v = make_manual_verdict("not_supported")
        project_store.save_claim_check(slug, "other-ck", manual_v)

        # Add the missing "ghost" reference
        ghost_bib = "@article{anyKey, title = {Found You}, year = {2024}}"
        p_proc, p_dl = self._patch_lookup(bib_key="ghost")
        with p_proc, p_dl:
            r = client.post(f"/api/projects/{slug}/add-reference",
                            json={"bib_key": "ghost", "bib_text": ghost_bib})
        assert r.status_code == 202

        reloaded = project_store.get_project(slug)
        cite_keys = {c["bib_key"]: c.get("claim_check_key") for c in reloaded["citations"]}
        # ghost's stale verdict pointer was cleared
        assert cite_keys["ghost"] is None or "claim_check_key" not in reloaded["citations"][0]
        # "other"'s manual verdict pointer was preserved
        assert cite_keys["other"] == "other-ck"
