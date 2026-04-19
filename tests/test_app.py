import json
from io import BytesIO
from unittest.mock import patch, MagicMock
from app import create_app


def test_index_page():
    app = create_app()
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"References Checker" in resp.data


def test_upload_valid_bib():
    app = create_app()
    client = app.test_client()
    bib_content = b"""@article{test, title={Test Paper}, year={2020}}"""
    with patch("app.threading.Thread"):
        resp = client.post("/upload", data={"file": (BytesIO(bib_content), "test.bib")},
                          content_type="multipart/form-data")
    data = json.loads(resp.data)
    assert resp.status_code == 200
    assert "session_id" in data
    assert data["total"] >= 1


def test_upload_no_file():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload")
    assert resp.status_code == 400


def test_upload_empty_file():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload", data={"file": (b"", "empty.bib")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400


def test_download_csv():
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="completed")
        store.add_result(sid, {
            "bib_key": "test", "title": "Test", "authors": ["A"],
            "year": "2020", "journal": "J", "doi": None, "abstract": "Ab",
            "pdf_url": None, "url": None, "citation_count": 0,
            "sources": [], "status": "found_abstract", "error": None
        })
    resp = client.get(f"/download/{sid}/csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type


def test_download_pdf():
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="completed")
        store.add_result(sid, {
            "bib_key": "test", "title": "Test", "authors": ["A"],
            "year": "2020", "journal": "J", "doi": None, "abstract": "Ab",
            "pdf_url": None, "url": None, "citation_count": 0,
            "sources": [], "status": "found_abstract", "error": None
        })
    resp = client.get(f"/download/{sid}/pdf")
    assert resp.status_code == 200
    assert "application/pdf" in resp.content_type


def test_download_while_processing_returns_409():
    app = create_app()
    client = app.test_client()
    with app.app_context():
        from app import store
        sid = store.create()
        store.update(sid, status="processing")
    resp = client.get(f"/download/{sid}/csv")
    assert resp.status_code == 409


def test_upload_wrong_extension():
    app = create_app()
    client = app.test_client()
    resp = client.post("/upload", data={"file": (b"some content", "test.txt")},
                      content_type="multipart/form-data")
    assert resp.status_code == 400


def test_download_nonexistent_session():
    app = create_app()
    client = app.test_client()
    resp = client.get("/download/nonexistent/csv")
    assert resp.status_code == 404


class TestWipeReferenceArtifacts:
    """Refresh = start clean. Stale wrong-paper PDF/abstract/md from a prior
    API false-match (CitadelSecuritiesWhatWeDo → Hokkaido photocatalysis paper)
    must be removed before re-fetch, else ref_match keeps reading the old PDF
    and reports the wrong paper indefinitely."""

    def test_wipes_all_artifact_suffixes(self, tmp_path):
        from app import _wipe_reference_artifacts
        import os
        for suffix in ("_pdf.pdf", "_abstract.txt", "_page.html",
                       "_pasted.md", ".md"):
            (tmp_path / f"k{suffix}").write_bytes(b"x")
        _wipe_reference_artifacts(str(tmp_path), "k")
        for suffix in ("_pdf.pdf", "_abstract.txt", "_page.html",
                       "_pasted.md", ".md"):
            assert not os.path.exists(tmp_path / f"k{suffix}"), \
                f"k{suffix} should have been wiped"

    def test_leaves_unrelated_bib_keys_alone(self, tmp_path):
        from app import _wipe_reference_artifacts
        import os
        (tmp_path / "k_pdf.pdf").write_bytes(b"target")
        (tmp_path / "other_pdf.pdf").write_bytes(b"keeper")
        _wipe_reference_artifacts(str(tmp_path), "k")
        assert not os.path.exists(tmp_path / "k_pdf.pdf")
        assert os.path.exists(tmp_path / "other_pdf.pdf")

    def test_safe_when_no_files_exist(self, tmp_path):
        from app import _wipe_reference_artifacts
        # Should not raise
        _wipe_reference_artifacts(str(tmp_path), "no-such-key")
