import json
import logging
import threading
import tempfile
import os
from datetime import datetime
from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from session_store import SessionStore
from bib_parser import parse_bib_file
from lookup_engine import process_all, process_reference
from report_exporter import export_csv, export_pdf
from file_downloader import download_reference_files
from tex_parser import parse_tex_citations
import project_store
from config import MAX_UPLOAD_SIZE, FLASK_PORT, PROJECTS_DIR, SEMANTIC_SCHOLAR_API_KEY, GOOGLE_API_KEY, GOOGLE_CSE_ID, UNPAYWALL_EMAIL, OPENALEX_API_KEY

_log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# File handler: all debug logs
_file_handler = logging.FileHandler("debug.log", encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(_log_fmt))

# Console handler: warnings and above only
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)
_console_handler.setFormatter(logging.Formatter(_log_fmt))

logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _console_handler])

# Print API key status to terminal on startup
def _print_api_status():
    print("\n--- API Keys Status ---")
    print(f"  Semantic Scholar API Key: {'OK' if SEMANTIC_SCHOLAR_API_KEY else 'MISSING'}")
    print(f"  Google API Key:           {'OK' if GOOGLE_API_KEY else 'MISSING'}")
    print(f"  Google CSE ID:            {'OK' if GOOGLE_CSE_ID else 'MISSING'}")
    print(f"  OpenAlex API Key:         {'OK' if OPENALEX_API_KEY else 'MISSING (optional)'}")
    print(f"  Unpaywall Email:          {'OK' if UNPAYWALL_EMAIL else 'MISSING'}")
    print("------------------------\n")

_print_api_status()

logger = logging.getLogger(__name__)

store = SessionStore()

# Track per-reference refresh status: key = "slug:bib_key", value = "refreshing"|result dict
_refresh_status = {}
_refresh_lock = threading.Lock()


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # Ensure projects directory exists
    os.makedirs(PROJECTS_DIR, exist_ok=True)

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Maximum size is 2MB."}), 413

    @app.route("/")
    def index():
        return render_template("index.html")

    # ================================================================
    # Project API routes
    # ================================================================

    @app.route("/api/projects", methods=["GET"])
    def api_list_projects():
        return jsonify(project_store.list_projects())

    @app.route("/api/projects", methods=["POST"])
    def api_create_project():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Project name is required"}), 400
        proj = project_store.create_project(name)
        return jsonify(proj), 201

    @app.route("/api/projects/<slug>", methods=["GET"])
    def api_get_project(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        return jsonify(proj)

    @app.route("/api/projects/<slug>", methods=["DELETE"])
    def api_delete_project(slug):
        if project_store.delete_project(slug):
            return jsonify({"ok": True})
        return jsonify({"error": "Project not found"}), 404

    @app.route("/api/projects/<slug>/upload", methods=["POST"])
    def api_project_upload(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".bib"):
            return jsonify({"error": "Please upload a .bib file"}), 400

        content = file.read()
        if not content.strip():
            return jsonify({"error": "File is empty"}), 400

        tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".bib", delete=False)
        tmp.write(content)
        tmp.close()

        try:
            refs = parse_bib_file(tmp.name)
        finally:
            os.unlink(tmp.name)

        if not refs:
            return jsonify({"error": "No valid references found in file"}), 400

        # If a LaTeX file is present, only process references that are cited
        if proj.get("tex_content"):
            cited_keys = set()
            for cit in (proj.get("citations") or []):
                cited_keys.add(cit.get("bib_key"))
            if cited_keys:
                refs = [r for r in refs if r["bib_key"] in cited_keys]
                logger.info("Filtered to %d refs cited in LaTeX (from %d total)", len(refs), proj.get("total", 0) or len(refs))
                if not refs:
                    return jsonify({"error": "No references from .bib file are cited in the LaTeX file"}), 400

        # Save parsed refs to project
        project_store.save_parsed_refs(slug, file.filename, refs)

        # Create in-memory session for SSE
        sid = store.create()
        store.update(sid, status="processing", total=len(refs))

        project_dir = project_store.get_project_dir(slug)

        # Build bib lookup for raw_bib
        bib_lookup = {r["bib_key"]: r.get("raw_bib") for r in refs if r.get("raw_bib")}

        def _process():
            batch = []
            batch_count = 0

            def on_result(idx, result):
                nonlocal batch_count
                # Attach raw_bib from parsed refs
                if result["bib_key"] in bib_lookup:
                    result["raw_bib"] = bib_lookup[result["bib_key"]]
                # Download files
                files = download_reference_files(project_dir, result["bib_key"], result)
                result["files"] = files
                # Feed SSE
                store.add_result(sid, result)
                # Accumulate for batch write
                batch.append(result)
                batch_count += 1
                if batch_count % 10 == 0:
                    project_store.save_results_batch(slug, batch)
                    batch.clear()

            process_all(refs, callback=on_result)

            # Flush remaining batch
            if batch:
                project_store.save_results_batch(slug, batch)

            project_store.update_project(slug, status="completed")
            store.update(sid, status="completed")

        t = threading.Thread(target=_process, daemon=True)
        t.start()

        warning = None
        if len(refs) > 500:
            warning = f"Large file with {len(refs)} references. This may take several minutes."

        return jsonify({
            "slug": slug,
            "session_id": sid,
            "total": len(refs),
            "warning": warning,
        })

    @app.route("/api/projects/<slug>/refresh/<bib_key>", methods=["POST"])
    def api_refresh_reference(slug, bib_key):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        ref = project_store.get_parsed_ref(slug, bib_key)
        if ref is None:
            return jsonify({"error": "Reference not found"}), 404

        status_key = f"{slug}:{bib_key}"
        with _refresh_lock:
            _refresh_status[status_key] = "refreshing"

        project_dir = project_store.get_project_dir(slug)

        def _do_refresh():
            try:
                result = process_reference(ref)
                files = download_reference_files(project_dir, bib_key, result, force=True)
                result["files"] = files
                project_store.save_result(slug, result)
                with _refresh_lock:
                    _refresh_status[status_key] = result
            except Exception as e:
                logger.error("Refresh failed for %s/%s: %s", slug, bib_key, e)
                with _refresh_lock:
                    _refresh_status[status_key] = {"error": str(e)}

        t = threading.Thread(target=_do_refresh, daemon=True)
        t.start()

        return jsonify({"status": "refreshing"})

    @app.route("/api/projects/<slug>/refresh-status/<bib_key>", methods=["GET"])
    def api_refresh_status(slug, bib_key):
        status_key = f"{slug}:{bib_key}"
        with _refresh_lock:
            status = _refresh_status.get(status_key)

        if status is None:
            return jsonify({"status": "idle"})
        if status == "refreshing":
            return jsonify({"status": "refreshing"})
        # Done — return result and clean up
        with _refresh_lock:
            _refresh_status.pop(status_key, None)
        return jsonify({"status": "done", "result": status})

    @app.route("/api/projects/<slug>/set-link/<bib_key>", methods=["POST"])
    def api_set_link(slug, bib_key):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "URL is required"}), 400

        # Find existing result
        result = None
        for r in proj.get("results", []):
            if r.get("bib_key") == bib_key:
                result = r
                break
        if result is None:
            return jsonify({"error": "Reference not found"}), 404

        project_dir = project_store.get_project_dir(slug)
        is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()

        if is_pdf:
            result["pdf_url"] = url
        else:
            result["url"] = url

        # Download the file
        from file_downloader import download_reference_files, _safe_filename, _download_pdf, _download_page
        safe_key = _safe_filename(bib_key)
        files = result.get("files", {})

        if is_pdf:
            filename = safe_key + "_pdf.pdf"
            path = os.path.join(project_dir, filename)
            if _download_pdf(url, path):
                files["pdf"] = filename
        else:
            filename = safe_key + "_page.html"
            path = os.path.join(project_dir, filename)
            if _download_page(url, path):
                files["page"] = filename

        result["files"] = files

        # Update status
        if result.get("pdf_url") or files.get("pdf"):
            result["status"] = "found_pdf"
        elif result.get("abstract"):
            result["status"] = "found_abstract"
        elif result.get("url") or files.get("page"):
            result["status"] = "found_web_page"

        if "manual" not in result.get("sources", []):
            result.setdefault("sources", []).append("manual")

        project_store.save_result(slug, result)
        return jsonify({"ok": True, "result": result})

    @app.route("/api/projects/<slug>/files/<path:filename>")
    def api_serve_file(slug, filename):
        project_dir = project_store.get_project_dir(slug)
        if not os.path.isdir(project_dir):
            return jsonify({"error": "Project not found"}), 404
        # Prevent path traversal
        safe_path = os.path.normpath(os.path.join(project_dir, filename))
        if not safe_path.startswith(os.path.normpath(project_dir)):
            return jsonify({"error": "Invalid path"}), 400
        if not os.path.isfile(safe_path):
            return jsonify({"error": "File not found"}), 404
        return send_from_directory(project_dir, filename)

    # ================================================================
    # LaTeX / Citation Review routes
    # ================================================================

    @app.route("/api/projects/<slug>/upload-tex", methods=["POST"])
    def api_upload_tex(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.lower().endswith(".tex"):
            return jsonify({"error": "Please upload a .tex file"}), 400

        content = file.read()
        if not content.strip():
            return jsonify({"error": "File is empty"}), 400

        tex_content = content.decode("utf-8", errors="replace")
        citations = parse_tex_citations(tex_content)

        # Save to project
        project_store.update_project(
            slug,
            tex_filename=file.filename,
            tex_content=tex_content,
            citations=citations,
        )

        # Save .tex file to project directory
        project_dir = project_store.get_project_dir(slug)
        tex_path = os.path.join(project_dir, file.filename)
        with open(tex_path, "w", encoding="utf-8") as tf:
            tf.write(tex_content)

        # Compute stats
        result_keys = set(r.get("bib_key") for r in proj.get("results", []))
        cite_keys = set(c["bib_key"] for c in citations)
        unmatched = sorted(cite_keys - result_keys)

        return jsonify({
            "total_citations": len(citations),
            "unique_keys": len(cite_keys),
            "unmatched_keys": unmatched,
        })

    @app.route("/api/projects/<slug>/save-tex", methods=["POST"])
    def api_save_tex(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        if not content:
            return jsonify({"error": "No content"}), 400

        citations = parse_tex_citations(content)

        project_store.update_project(
            slug,
            tex_content=content,
            citations=citations,
        )

        # Save .tex file to project directory
        tex_filename = proj.get("tex_filename", "document.tex")
        project_dir = project_store.get_project_dir(slug)
        tex_path = os.path.join(project_dir, tex_filename)
        with open(tex_path, "w", encoding="utf-8") as tf:
            tf.write(content)

        return jsonify({
            "ok": True,
            "total_citations": len(citations),
            "citations": citations,
        })

    @app.route("/api/projects/<slug>/download-tex", methods=["GET"])
    def api_download_tex(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        if not proj.get("tex_content"):
            return jsonify({"error": "No LaTeX file"}), 404
        tex_filename = proj.get("tex_filename", "document.tex")
        response = Response(proj["tex_content"], mimetype="text/plain")
        response.headers["Content-Disposition"] = f'attachment; filename="{tex_filename}"'
        return response

    @app.route("/api/projects/<slug>/tex", methods=["GET"])
    def api_get_tex(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        if not proj.get("tex_content"):
            return jsonify({"error": "No LaTeX file uploaded"}), 404
        return jsonify({
            "tex_filename": proj.get("tex_filename"),
            "tex_content": proj.get("tex_content"),
            "citations": proj.get("citations", []),
        })

    # ================================================================
    # SSE streaming (works with both session IDs and project slugs)
    # ================================================================

    @app.route("/stream/<session_id>")
    def stream(session_id):
        session = store.get(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404

        last_id = request.headers.get("Last-Event-ID")
        initial_sent = int(last_id) + 1 if last_id and last_id.isdigit() else 0

        def generate():
            import time
            sent = initial_sent
            last_heartbeat = time.time()
            while True:
                session = store.get(session_id)
                if session is None:
                    break

                results = session["results"]
                total = session["total"]

                while sent < len(results):
                    r = results[sent]
                    event_data = json.dumps({
                        "index": sent,
                        "total": total,
                        "bib_key": r.get("bib_key"),
                        "status": r.get("status"),
                        "result": r,
                    })
                    if r.get("error"):
                        yield f"id: {sent}\nevent: error\ndata: {json.dumps({'index': sent, 'total': total, 'bib_key': r.get('bib_key'), 'message': r.get('error')})}\n\n"
                    else:
                        yield f"id: {sent}\nevent: progress\ndata: {event_data}\n\n"
                    sent += 1

                if session["status"] == "completed" and sent >= total:
                    found_pdf = sum(1 for r in results if r["status"] == "found_pdf")
                    found_abstract = sum(1 for r in results if r["status"] == "found_abstract")
                    found_web_page = sum(1 for r in results if r["status"] == "found_web_page")
                    not_found = total - found_pdf - found_abstract - found_web_page
                    done_data = json.dumps({
                        "total": total,
                        "found_pdf": found_pdf,
                        "found_abstract": found_abstract,
                        "found_web_page": found_web_page,
                        "not_found": not_found,
                    })
                    yield f"event: complete\ndata: {done_data}\n\n"
                    break

                now = time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now

                time.sleep(0.3)

        return Response(generate(), mimetype="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ================================================================
    # Download reports (project-based)
    # ================================================================

    @app.route("/download/<slug>/<fmt>")
    def download(slug, fmt):
        proj = project_store.get_project(slug)
        if proj is None:
            # Fallback: try as session ID for backward compat
            session = store.get(slug)
            if session is None:
                return jsonify({"error": "Not found"}), 404
            results = session["results"]
        else:
            if proj["status"] == "processing":
                return jsonify({"error": "Processing still in progress"}), 409
            results = proj["results"]

        if fmt == "csv":
            csv_data = export_csv(results)
            return Response(csv_data, mimetype="text/csv",
                          headers={"Content-Disposition": "attachment; filename=references_report.csv"})
        elif fmt == "pdf":
            pdf_data = export_pdf(results)
            return Response(pdf_data, mimetype="application/pdf",
                          headers={"Content-Disposition": "attachment; filename=references_report.pdf"})
        else:
            return jsonify({"error": "Invalid format. Use 'csv' or 'pdf'"}), 400

    return app


if __name__ == "__main__":
    store.start_cleanup_thread()
    app = create_app()
    print(f"Starting app on http://localhost:{FLASK_PORT}")
    app.run(debug=True, port=FLASK_PORT, threaded=True)
