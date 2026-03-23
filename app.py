import json
import threading
import tempfile
import os
from flask import Flask, request, jsonify, Response, render_template
from session_store import SessionStore
from bib_parser import parse_bib_file
from lookup_engine import process_all
from report_exporter import export_csv, export_pdf
from config import MAX_UPLOAD_SIZE, FLASK_PORT

store = SessionStore()


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Maximum size is 2MB."}), 413

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/upload", methods=["POST"])
    def upload():
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".bib"):
            return jsonify({"error": "Please upload a .bib file"}), 400

        content = file.read()
        if not content.strip():
            return jsonify({"error": "File is empty"}), 400

        # Save to temp file for parsing
        tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".bib", delete=False)
        tmp.write(content)
        tmp.close()

        try:
            refs = parse_bib_file(tmp.name)
        finally:
            os.unlink(tmp.name)

        if not refs:
            return jsonify({"error": "No valid references found in file"}), 400

        sid = store.create()
        store.update(sid, status="processing", total=len(refs))

        # Process in background thread
        def _process():
            def on_result(idx, result):
                store.add_result(sid, result)
            process_all(refs, callback=on_result)
            store.update(sid, status="completed")

        t = threading.Thread(target=_process, daemon=True)
        t.start()

        warning = None
        if len(refs) > 500:
            warning = f"Large file with {len(refs)} references. This may take several minutes."

        return jsonify({"session_id": sid, "total": len(refs), "warning": warning})

    @app.route("/stream/<session_id>")
    def stream(session_id):
        session = store.get(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404

        # Capture before entering generator (request context won't be available inside)
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

                # Send new results
                while sent < len(results):
                    r = results[sent]
                    event_data = json.dumps({
                        "index": sent,
                        "total": total,
                        "bib_key": r.get("bib_key"),
                        "status": r.get("status"),
                        "result": r,
                    })
                    # Emit error event for failed lookups
                    if r.get("error"):
                        yield f"id: {sent}\nevent: error\ndata: {json.dumps({'index': sent, 'total': total, 'bib_key': r.get('bib_key'), 'message': r.get('error')})}\n\n"
                    else:
                        yield f"id: {sent}\nevent: progress\ndata: {event_data}\n\n"
                    sent += 1

                # Check if done
                if session["status"] == "completed" and sent >= total:
                    found_pdf = sum(1 for r in results if r["status"] == "found_pdf")
                    found_abstract = sum(1 for r in results if r["status"] == "found_abstract")
                    not_found = total - found_pdf - found_abstract
                    done_data = json.dumps({
                        "total": total,
                        "found_pdf": found_pdf,
                        "found_abstract": found_abstract,
                        "not_found": not_found,
                    })
                    yield f"event: complete\ndata: {done_data}\n\n"
                    break

                # Heartbeat
                now = time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now

                time.sleep(0.3)

        return Response(generate(), mimetype="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/download/<session_id>/<fmt>")
    def download(session_id, fmt):
        session = store.get(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404

        if session["status"] == "processing":
            return jsonify({"error": "Processing still in progress"}), 409

        results = session["results"]

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
    app.run(debug=True, port=FLASK_PORT, threaded=True)
