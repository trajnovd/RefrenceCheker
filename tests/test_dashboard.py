"""Tests for v5 dashboard: activity log, last-viewed citation, settings PUT."""

import json
import time
from unittest.mock import patch
import pytest

import project_store
from app import create_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
    return project_store.create_project("Dashboard Test")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store, "PROJECTS_DIR", str(tmp_path))
    app = create_app()
    return app.test_client()


# ============================================================
# Activity log
# ============================================================

class TestActivityLog:
    def test_add_activity_appends(self, project):
        slug = project["slug"]
        project_store.add_activity(slug, "bib_uploaded", "Uploaded test.bib")
        project_store.add_activity(slug, "tex_uploaded", "Uploaded main.tex")
        proj = project_store.get_project(slug)
        assert len(proj["activity"]) == 2
        assert proj["activity"][0]["type"] == "bib_uploaded"
        assert proj["activity"][1]["type"] == "tex_uploaded"

    def test_activity_capped_at_50(self, project):
        slug = project["slug"]
        for i in range(60):
            project_store.add_activity(slug, "test", f"entry {i}")
        proj = project_store.get_project(slug)
        assert len(proj["activity"]) == 50
        assert proj["activity"][0]["message"] == "entry 10"
        assert proj["activity"][-1]["message"] == "entry 59"

    def test_activity_has_timestamp(self, project):
        slug = project["slug"]
        project_store.add_activity(slug, "test", "msg")
        proj = project_store.get_project(slug)
        assert "ts" in proj["activity"][0]

    def test_bib_upload_creates_activity(self, client, project):
        slug = project["slug"]
        from io import BytesIO
        bib = b"@article{k, title={T}, year={2024}}"
        with patch("app.threading.Thread"):
            client.post(f"/api/projects/{slug}/upload",
                        data={"file": (BytesIO(bib), "test.bib")},
                        content_type="multipart/form-data")
        proj = project_store.get_project(slug)
        types = [a["type"] for a in (proj.get("activity") or [])]
        assert "bib_uploaded" in types


# ============================================================
# Last-viewed citation
# ============================================================

class TestLastViewed:
    def test_set_and_get(self, project):
        slug = project["slug"]
        project_store.set_last_viewed_citation(slug, 7)
        assert project_store.get_last_viewed_citation(slug) == 7

    def test_defaults_to_zero(self, project):
        assert project_store.get_last_viewed_citation(project["slug"]) == 0

    def test_api_roundtrip(self, client, project):
        slug = project["slug"]
        client.post(f"/api/projects/{slug}/last-viewed",
                    json={"citation_index": 12})
        r = client.get(f"/api/projects/{slug}/last-viewed")
        assert r.get_json()["citation_index"] == 12


# ============================================================
# Settings GET / PUT
# ============================================================

class TestSettingsApi:
    def test_get_settings_strips_secrets(self, client):
        r = client.get("/api/settings")
        data = r.get_json()
        assert "openai_api_key" not in json.dumps(data)
        assert "_keys" in data

    def test_put_settings_updates_file(self, client, tmp_path, monkeypatch):
        import config
        test_path = str(tmp_path / "settings.json")
        monkeypatch.setattr(config, "_SETTINGS_PATH", test_path)
        # Write initial
        with open(test_path, "w") as f:
            json.dump({"pdf_converter": "pymupdf4llm"}, f)

        r = client.put("/api/settings", json={"pdf_converter": "docling"})
        assert r.get_json()["ok"]

        with open(test_path) as f:
            updated = json.load(f)
        assert updated["pdf_converter"] == "docling"

    def test_put_settings_refuses_api_keys(self, client, tmp_path, monkeypatch):
        import config
        test_path = str(tmp_path / "settings.json")
        monkeypatch.setattr(config, "_SETTINGS_PATH", test_path)
        with open(test_path, "w") as f:
            json.dump({}, f)

        client.put("/api/settings", json={"openai_api_key": "sk-evil"})
        with open(test_path) as f:
            data = json.load(f)
        assert "openai_api_key" not in data
