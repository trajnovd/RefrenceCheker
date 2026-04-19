import json
import logging
import threading
import tempfile
import os
from datetime import datetime
from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from session_store import SessionStore
from bib_parser import parse_bib_file
from lookup_engine import process_all, process_reference, make_bib_url_unreachable_result
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

from config import print_startup_banner
print_startup_banner()

logger = logging.getLogger(__name__)

store = SessionStore()

# Track per-reference refresh status: key = "slug:bib_key", value = "refreshing"|result dict
_refresh_status = {}
_refresh_lock = threading.Lock()


def _wipe_reference_artifacts(project_dir, bib_key):
    """Remove any downloaded files for this reference (PDF, HTML, abstract, .md, pasted).

    Used when a refresh discovers the bib URL is broken — we don't want to keep
    stale wrong-paper artifacts from a prior run that fell through to the
    title-search pipeline.
    """
    from file_downloader import _safe_filename
    safe_key = _safe_filename(bib_key)
    for suffix in ("_pdf.pdf", "_abstract.txt", "_page.html", "_pasted.md", ".md"):
        path = os.path.join(project_dir, safe_key + suffix)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _maybe_auto_check_ref_match(slug, bib_key, previous_tier=None):
    """Fire-and-forget: kick off a single reference-match check in the background.

    Honors settings.reference_match.enabled + .auto_check_on_download. Quietly
    no-ops if the OpenAI key is missing — the user can still trigger the check
    manually via the dashboard. Errors are swallowed (logged) so download paths
    are never broken by a match-check failure.

    v6.1 A3: if `previous_tier` is given and differs from the current tier on
    result.files_origin.pdf, force re-check even if auto_check_on_download is
    off — a tier change means a potentially different paper (Wayback snapshot,
    OpenReview preprint, alt mirror). Manual verdicts remain sticky.
    """
    try:
        from config import get_reference_match_settings, get_openai_api_key
        s = get_reference_match_settings()
        if not get_openai_api_key():
            return
        force_recheck = False
        if previous_tier is not None:
            # Read the current tier after the download completed
            current_tier = _current_pdf_tier(slug, bib_key)
            if current_tier and current_tier != previous_tier:
                force_recheck = True
                logger.info("[%s/%s] pdf tier changed %s -> %s; forcing ref-match recheck",
                             slug, bib_key, previous_tier, current_tier)
        if not force_recheck and not (s.get("enabled") and s.get("auto_check_on_download")):
            return
    except Exception:
        return

    def _run():
        try:
            from reference_matcher import check_and_save
            check_and_save(slug, bib_key, force=True)
        except Exception as e:
            logger.debug("auto ref-match for %s/%s failed: %s", slug, bib_key, e)

    threading.Thread(target=_run, daemon=True).start()


def _current_pdf_tier(slug, bib_key):
    """Read result.files_origin.pdf.tier for a bib_key. Returns None if missing."""
    try:
        proj = project_store.get_project(slug)
        if not proj:
            return None
        for r in proj.get("results") or []:
            if r.get("bib_key") == bib_key:
                return ((r.get("files_origin") or {}).get("pdf") or {}).get("tier")
    except Exception:
        pass
    return None


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # Ensure projects directory exists
    os.makedirs(PROJECTS_DIR, exist_ok=True)

    @app.errorhandler(413)
    def too_large(e):
        mb = MAX_UPLOAD_SIZE // (1024 * 1024)
        return jsonify({"error": f"File too large. Maximum size is {mb}MB."}), 413

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
        project_store.add_activity(slug, "bib_uploaded",
                                   f"Uploaded {file.filename} ({len(refs)} references)")

        # Create in-memory session for SSE
        sid = store.create()
        store.update(sid, status="processing", total=len(refs))

        project_dir = project_store.get_project_dir(slug)

        # Build bib lookup for raw_bib
        bib_lookup = {r["bib_key"]: r.get("raw_bib") for r in refs if r.get("raw_bib")}

        def _process():
            batch = []
            batch_count = 0

            # Wrapper: for refs with a bib URL, pre-download content first.
            # If the download succeeds, run the lookup pipeline in metadata-only
            # mode (abstract, citations, DOI) — do not fetch alternative PDF/HTML URLs.
            from file_downloader import pre_download_bib_url

            def _process_ref_with_bib_url(ref):
                bib_url = ref.get("url")
                if bib_url:
                    pre = pre_download_bib_url(project_dir, ref["bib_key"], bib_url)
                    if pre.get("pdf") or pre.get("page"):
                        result = process_reference(ref, metadata_only=True)
                        result["_pre_downloaded"] = pre
                        return result
                    if pre.get("error"):
                        # Bib URL unreachable — short-circuit. Running the title-only
                        # API chain would find unrelated papers and present them as the source.
                        return make_bib_url_unreachable_result(ref, pre)
                return process_reference(ref)

            def on_result(idx, result):
                nonlocal batch_count
                # Attach raw_bib from parsed refs
                if result["bib_key"] in bib_lookup:
                    result["raw_bib"] = bib_lookup[result["bib_key"]]
                if result.get("status") == "bib_url_unreachable":
                    # Don't re-download the broken URL. No artifacts to attach.
                    result["files"] = {}
                else:
                    # Download files (pre-downloaded files are already on disk and
                    # will be picked up automatically — download_reference_files skips
                    # existing files when force=False).
                    files = download_reference_files(project_dir, result["bib_key"], result)
                    result["files"] = files
                result.pop("_pre_downloaded", None)
                # Auto-verify the downloaded text actually matches the bib's title+authors.
                if (result.get("files") or {}).get("md"):
                    _maybe_auto_check_ref_match(slug, result["bib_key"])
                # Feed SSE
                store.add_result(sid, result)
                # Accumulate for batch write
                batch.append(result)
                batch_count += 1
                if batch_count % 10 == 0:
                    project_store.save_results_batch(slug, batch)
                    batch.clear()

            process_all(refs, callback=on_result, process_fn=_process_ref_with_bib_url)

            # Flush remaining batch
            if batch:
                project_store.save_results_batch(slug, batch)

            project_store.update_project(slug, status="completed")
            store.update(sid, status="completed")
            project_store.add_activity(slug, "lookup_completed",
                                       f"Lookup completed for {len(refs)} references")

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
                # Capture the previous tier before we overwrite the result, so
                # A3 can detect a tier change and force a ref-match recheck.
                previous_tier = _current_pdf_tier(slug, bib_key)

                # Try bib URL first; if it works, only fetch metadata from APIs
                from file_downloader import pre_download_bib_url
                bib_url = ref.get("url")
                metadata_only = False
                bib_url_failure = None
                if bib_url:
                    pre = pre_download_bib_url(project_dir, bib_key, bib_url)
                    if pre.get("pdf") or pre.get("page"):
                        metadata_only = True
                    elif pre.get("error"):
                        bib_url_failure = pre

                if bib_url_failure:
                    # Bib URL is broken — don't run the lookup pipeline. Wipe any
                    # stale wrong-paper artifacts the previous run may have left behind,
                    # then surface the failure so the user can fix the citation.
                    _wipe_reference_artifacts(project_dir, bib_key)
                    result = make_bib_url_unreachable_result(ref, pre)
                    result["files"] = {}
                else:
                    result = process_reference(ref, metadata_only=metadata_only)
                    files = download_reference_files(project_dir, bib_key, result, force=not metadata_only)
                    result["files"] = files
                project_store.save_result(slug, result)
                with _refresh_lock:
                    _refresh_status[status_key] = result
                if (result.get("files") or {}).get("md"):
                    _maybe_auto_check_ref_match(slug, bib_key, previous_tier=previous_tier)
            except Exception as e:
                logger.error("Refresh failed for %s/%s: %s", slug, bib_key, e)
                with _refresh_lock:
                    _refresh_status[status_key] = {"error": str(e)}

        t = threading.Thread(target=_do_refresh, daemon=True)
        t.start()

        return jsonify({"status": "refreshing"})

    @app.route("/api/projects/<slug>/add-reference", methods=["POST"])
    def api_add_reference(slug):
        """Manually add a missing reference from a pasted BibTeX entry, then look it up.

        Body: { bib_key (required, the citation key from the .tex), bib_text (required) }.
        We parse bib_text using the project's BibTeX parser, take the first entry, and
        override its key with the supplied bib_key so it lines up with the citation.
        Returns 202 + bib_key; client polls /refresh-status/<bib_key>.
        """
        from bib_parser import parse_bib_string

        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        body = request.get_json(silent=True) or {}
        bib_key = (body.get("bib_key") or "").strip()
        bib_text = (body.get("bib_text") or "").strip()
        if not bib_key:
            return jsonify({"error": "bib_key is required"}), 400
        if not bib_text:
            return jsonify({"error": "bib_text is required"}), 400

        # Reject if a result already exists for this key (use Refresh instead)
        for r in proj.get("results") or []:
            if r.get("bib_key") == bib_key:
                return jsonify({"error": "A reference with this bib_key already exists"}), 409

        # Parse the pasted BibTeX
        try:
            parsed = parse_bib_string(bib_text)
        except Exception as e:
            return jsonify({"error": f"Failed to parse BibTeX: {e}"}), 400
        # Drop parse-error stubs and insufficient-data entries
        valid = [r for r in parsed
                 if r.get("status") not in ("parse_error",)
                 and r.get("title")]
        if not valid:
            return jsonify({"error": "No usable BibTeX entry found (need at least one entry with a title)"}), 400

        ref = dict(valid[0])  # take the first usable entry
        # Force the citation's expected bib_key (the one from the .tex)
        original_key = ref.get("bib_key")
        ref["bib_key"] = bib_key
        ref["manually_added"] = True

        # Rewrite raw_bib so the BibTeX tab shows the corrected key
        if ref.get("raw_bib") and original_key and original_key != bib_key:
            ref["raw_bib"] = ref["raw_bib"].replace("{" + original_key + ",", "{" + bib_key + ",", 1)

        added = project_store.add_parsed_ref(slug, ref)
        if not added:
            return jsonify({"error": "A parsed_ref with this bib_key already exists"}), 409
        project_store.add_activity(slug, "add_reference",
                                   f"Added reference {bib_key}: {ref.get('title', '?')}", target=bib_key)

        # Clear any stale "key not found" verdicts for this bib_key — the ref now exists
        from claim_checker import is_manual_verdict
        checks = proj.get("claim_checks") or {}
        for idx, cite in enumerate(proj.get("citations") or []):
            if cite.get("bib_key") != bib_key:
                continue
            ck = cite.get("claim_check_key")
            if ck and not is_manual_verdict(checks.get(ck)):
                project_store.set_citation_check_key(slug, idx, None)

        # Kick off lookup + downloads in the background; reuse the refresh-status mechanism.
        status_key = f"{slug}:{bib_key}"
        with _refresh_lock:
            _refresh_status[status_key] = "refreshing"
        project_dir = project_store.get_project_dir(slug)

        def _do_add():
            try:
                # Try bib URL first; if it works, only fetch metadata from APIs
                from file_downloader import pre_download_bib_url
                bib_url = ref.get("url")
                metadata_only = False
                bib_url_failure = None
                if bib_url:
                    pre = pre_download_bib_url(project_dir, bib_key, bib_url)
                    if pre.get("pdf") or pre.get("page"):
                        metadata_only = True
                    elif pre.get("error"):
                        bib_url_failure = pre

                if bib_url_failure:
                    _wipe_reference_artifacts(project_dir, bib_key)
                    result = make_bib_url_unreachable_result(ref, pre)
                    if ref.get("raw_bib"):
                        result["raw_bib"] = ref["raw_bib"]
                    result["files"] = {}
                else:
                    result = process_reference(ref, metadata_only=metadata_only)
                    # Attach raw_bib so the BibTeX tab shows the user's pasted entry
                    if ref.get("raw_bib"):
                        result["raw_bib"] = ref["raw_bib"]
                    files = download_reference_files(project_dir, bib_key, result, force=not metadata_only)
                    result["files"] = files
                project_store.save_result(slug, result)
                with _refresh_lock:
                    _refresh_status[status_key] = result
                if (result.get("files") or {}).get("md"):
                    _maybe_auto_check_ref_match(slug, bib_key)
            except Exception as e:
                logger.exception("add-reference failed for %s/%s: %s", slug, bib_key, e)
                with _refresh_lock:
                    _refresh_status[status_key] = {"error": str(e)}

        threading.Thread(target=_do_add, daemon=True).start()
        return jsonify({"status": "processing", "bib_key": bib_key}), 202

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

        # Replace source: drops opposing file, downloads new, refreshes abstract (HTML),
        # rebuilds the consolidated .md.
        from file_downloader import replace_reference_source
        outcome = replace_reference_source(project_dir, bib_key, result, url)
        files = result.get("files") or {}

        # Update status based on what's now available
        if result.get("pdf_url") or files.get("pdf"):
            result["status"] = "found_pdf"
        elif result.get("abstract") or files.get("abstract"):
            result["status"] = "found_abstract"
        elif result.get("url") or files.get("page"):
            result["status"] = "found_web_page"

        if "manual" not in result.get("sources", []):
            result.setdefault("sources", []).append("manual")

        project_store.save_result(slug, result)

        # Invalidate stale auto-verdicts for this reference's citations.
        # Manual verdicts are preserved — the user explicitly set those.
        from claim_checker import is_manual_verdict
        cleared = 0
        citations = proj.get("citations") or []
        checks = proj.get("claim_checks") or {}
        for idx, cite in enumerate(citations):
            if cite.get("bib_key") != bib_key:
                continue
            ck = cite.get("claim_check_key")
            if ck and not is_manual_verdict(checks.get(ck)):
                project_store.set_citation_check_key(slug, idx, None)
                cleared += 1

        # Source changed → re-verify identity match against the new content.
        if (result.get("files") or {}).get("md"):
            _maybe_auto_check_ref_match(slug, bib_key)
        else:
            # New source produced no .md (download failed) — clear stale match.
            project_store.save_ref_match(slug, bib_key, None)

        return jsonify({
            "ok": True,
            "result": result,
            "is_pdf": outcome["is_pdf"],
            "downloaded": outcome["downloaded"],
            "verdicts_cleared": cleared,
        })

    def _replace_source_response(slug, bib_key, helper_outcome, result):
        """Shared finalization for set-link / upload-pdf / paste-content routes:
        update status, mark sources, save, and clear stale auto-verdicts.
        """
        if not helper_outcome.get("ok"):
            return jsonify({"error": helper_outcome.get("reason", "Operation failed")}), 400

        # Heal: if a prior Refresh dropped raw_bib, restore it from parsed_refs so the
        # BibTeX tab keeps working after manual operations.
        if not result.get("raw_bib"):
            current_proj = project_store.get_project(slug)
            for pr in (current_proj or {}).get("parsed_refs") or []:
                if pr.get("bib_key") == bib_key and pr.get("raw_bib"):
                    result["raw_bib"] = pr["raw_bib"]
                    break

        files = result.get("files") or {}
        if result.get("pdf_url") or files.get("pdf"):
            result["status"] = "found_pdf"
        elif result.get("abstract") or files.get("abstract"):
            result["status"] = "found_abstract"
        elif files.get("pasted"):
            result["status"] = "found_abstract"  # treat pasted as readable content
        elif result.get("url") or files.get("page"):
            result["status"] = "found_web_page"

        if "manual" not in result.get("sources", []):
            result.setdefault("sources", []).append("manual")
        project_store.save_result(slug, result)

        # Log which source-type was set
        src_type = "PDF" if (result.get("pdf_url") or (result.get("files") or {}).get("pdf")) else "content"
        project_store.add_activity(slug, "source_replaced",
                                   f"Set {src_type} source for {bib_key}", target=bib_key)

        # Invalidate stale auto-verdicts for this reference's citations.
        from claim_checker import is_manual_verdict
        proj = project_store.get_project(slug)
        cleared = 0
        citations = proj.get("citations") or []
        checks = proj.get("claim_checks") or {}
        for idx, cite in enumerate(citations):
            if cite.get("bib_key") != bib_key:
                continue
            ck = cite.get("claim_check_key")
            if ck and not is_manual_verdict(checks.get(ck)):
                project_store.set_citation_check_key(slug, idx, None)
                cleared += 1

        # Source changed → re-verify identity match against the new content.
        if (result.get("files") or {}).get("md"):
            _maybe_auto_check_ref_match(slug, bib_key)
        else:
            project_store.save_ref_match(slug, bib_key, None)

        return jsonify({
            "ok": True,
            "result": result,
            "verdicts_cleared": cleared,
        })

    @app.route("/api/projects/<slug>/upload-pdf/<bib_key>", methods=["POST"])
    def api_upload_pdf(slug, bib_key):
        """Upload a PDF file as the source for a reference (for sites that block scraping)."""
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "Empty filename"}), 400

        result = next((r for r in (proj.get("results") or []) if r.get("bib_key") == bib_key), None)
        if result is None:
            return jsonify({"error": "Reference not found"}), 404

        pdf_bytes = file.read()
        from file_downloader import set_uploaded_pdf
        project_dir = project_store.get_project_dir(slug)
        outcome = set_uploaded_pdf(project_dir, bib_key, result, pdf_bytes)
        return _replace_source_response(slug, bib_key, outcome, result)

    @app.route("/api/projects/<slug>/paste-content/<bib_key>", methods=["POST"])
    def api_paste_content(slug, bib_key):
        """Save user-pasted text/markdown content as the source for a reference.

        Useful when the page can't be auto-downloaded (Cloudflare, paywalls, JS shells, etc.).
        Body: { content: "<text>" }.
        """
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        body = request.get_json(silent=True) or {}
        content = body.get("content") or ""
        if not content.strip():
            return jsonify({"error": "content is required"}), 400

        result = next((r for r in (proj.get("results") or []) if r.get("bib_key") == bib_key), None)
        if result is None:
            return jsonify({"error": "Reference not found"}), 404

        from file_downloader import set_pasted_content
        project_dir = project_store.get_project_dir(slug)
        outcome = set_pasted_content(project_dir, bib_key, result, content)
        return _replace_source_response(slug, bib_key, outcome, result)

    @app.route("/api/projects/<slug>/build-md", methods=["POST"])
    def api_build_md(slug):
        """Kick off background rebuild of {key}.md for every reference in the project.

        Reuses already-downloaded PDF/HTML/abstract files; does not re-fetch.
        Returns a session_id; clients should subscribe to /build-md-stream/<sid> for progress.
        """
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        from file_downloader import rebuild_reference_md

        project_dir = project_store.get_project_dir(slug)
        results = proj.get("results") or []
        total = len(results)

        sid = store.create()
        store.update(sid, total=total, status="running", built=0, skipped=0, errors=0, current=None)

        def _run():
            built = 0
            skipped = 0
            errors = 0
            updated = []
            for result in results:
                bib_key = result.get("bib_key")
                if not bib_key:
                    skipped += 1
                    store.add_result(sid, {"bib_key": None, "built": False, "skipped": True})
                    continue
                store.update(sid, current=bib_key)
                try:
                    new_files = rebuild_reference_md(project_dir, bib_key, result)
                    result["files"] = new_files
                    updated.append(result)
                    was_built = bool(new_files.get("md"))
                    if was_built:
                        built += 1
                    else:
                        skipped += 1
                    store.add_result(sid, {"bib_key": bib_key, "built": was_built, "skipped": not was_built})
                except Exception as e:
                    logger.exception("build-md failed for %s/%s: %s", slug, bib_key, e)
                    errors += 1
                    store.add_result(sid, {"bib_key": bib_key, "built": False, "error": str(e)})
                store.update(sid, built=built, skipped=skipped, errors=errors)

            if updated:
                project_store.save_results_batch(slug, updated)
            store.update(sid, status="completed", current=None,
                         built=built, skipped=skipped, errors=errors)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True, "session_id": sid, "total": total})

    @app.route("/api/projects/<slug>/build-md-stream/<sid>")
    def api_build_md_stream(slug, sid):
        last_id = request.headers.get("Last-Event-ID")
        try:
            initial_sent = int(last_id) + 1 if last_id else 0
        except ValueError:
            initial_sent = 0

        def generate():
            import time
            sent = initial_sent
            last_heartbeat = time.time()
            while True:
                session = store.get(sid)
                if session is None:
                    break

                results = session["results"]
                total = session.get("total", 0)

                while sent < len(results):
                    r = results[sent]
                    payload = json.dumps({
                        "index": sent,
                        "total": total,
                        "bib_key": r.get("bib_key"),
                        "built": r.get("built", False),
                        "skipped": r.get("skipped", False),
                        "error": r.get("error"),
                        "current": session.get("current"),
                    })
                    yield f"id: {sent}\nevent: progress\ndata: {payload}\n\n"
                    sent += 1

                if session.get("status") == "completed" and sent >= total:
                    done = json.dumps({
                        "total": total,
                        "built": session.get("built", 0),
                        "skipped": session.get("skipped", 0),
                        "errors": session.get("errors", 0),
                    })
                    yield f"event: complete\ndata: {done}\n\n"
                    break

                now = time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now

                time.sleep(0.2)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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

        project_store.add_activity(slug, "tex_uploaded",
                                   f"Uploaded {file.filename} ({len(citations)} citations)")

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
            "claim_checks": proj.get("claim_checks", {}),
        })

    # ================================================================
    # v4: Citation claim verification (LLM)
    # ================================================================

    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        from config import get_settings, get_openai_api_key
        s = get_settings()
        # Strip secrets; show only set/missing
        s.pop("openai_api_key", None)
        if "claim_check" in s:
            s["claim_check"].pop("openai_api_key", None)
        s["_keys"] = {
            "openai": bool(get_openai_api_key()),
            "semantic_scholar": bool(SEMANTIC_SCHOLAR_API_KEY),
            "google_api": bool(GOOGLE_API_KEY),
            "google_cse": bool(GOOGLE_CSE_ID),
            "openalex": bool(OPENALEX_API_KEY),
        }
        return jsonify(s)

    @app.route("/api/settings", methods=["PUT"])
    def api_put_settings():
        from config import update_settings
        body = request.get_json(silent=True) or {}
        updated = update_settings(body)
        return jsonify({"ok": True, "settings": updated})

    @app.route("/api/projects/<slug>/last-viewed", methods=["GET"])
    def api_get_last_viewed(slug):
        idx = project_store.get_last_viewed_citation(slug)
        return jsonify({"citation_index": idx})

    @app.route("/api/projects/<slug>/last-viewed", methods=["POST"])
    def api_set_last_viewed(slug):
        body = request.get_json(silent=True) or {}
        idx = int(body.get("citation_index", 0))
        project_store.set_last_viewed_citation(slug, idx)
        return jsonify({"ok": True})

    @app.route("/api/settings/claim-check", methods=["GET"])
    def api_settings_claim_check():
        from config import get_claim_check_settings, get_openai_api_key
        s = get_claim_check_settings()
        return jsonify({
            "enabled": bool(s.get("enabled")) and bool(get_openai_api_key()),
            "configured": bool(get_openai_api_key()),
            "model": s.get("openai_model"),
            "max_batch_usd": s.get("max_batch_usd"),
        })

    @app.route("/api/projects/<slug>/citations-with-verdicts", methods=["GET"])
    def api_citations_with_verdicts(slug):
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        citations = list(proj.get("citations") or [])
        checks = proj.get("claim_checks") or {}
        results_by_key = {r.get("bib_key"): r for r in (proj.get("results") or [])}
        out = []
        for idx, c in enumerate(citations):
            row = dict(c)
            row["index"] = idx
            ck = c.get("claim_check_key")
            row["verdict"] = checks.get(ck) if ck else None
            r = results_by_key.get(c.get("bib_key"))
            row["reference"] = {
                "title": (r or {}).get("title"),
                "authors": (r or {}).get("authors") or [],
                "year": (r or {}).get("year"),
                "files": (r or {}).get("files") or {},
                "matched": r is not None,
                "has_md": bool(((r or {}).get("files") or {}).get("md")),
            }
            out.append(row)
        return jsonify({"citations": out, "claim_checks": checks})

    @app.route("/api/projects/<slug>/check-citation/<int:idx>", methods=["POST"])
    def api_check_citation(slug, idx):
        from claim_checker import (
            check_citation, load_reference_md, truncate_reference_md, cache_key_for,
            is_setup_failure_verdict,
        )
        from config import get_claim_check_settings, get_openai_api_key
        from tex_parser import extract_claim_context

        api_key = get_openai_api_key()
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 400

        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        citations = proj.get("citations") or []
        if idx < 0 or idx >= len(citations):
            return jsonify({"error": "Citation index out of range"}), 404

        body = request.get_json(silent=True) or {}
        force = bool(body.get("force"))
        model_override = body.get("model")

        settings = get_claim_check_settings()
        model = model_override or settings.get("openai_model") or "gpt-5-mini"
        max_para = settings.get("max_paragraph_chars", 4000)
        max_sent = settings.get("max_sentence_chars", 1500)
        max_ref = settings.get("max_ref_chars", 100000)

        citation = citations[idx]
        bib_key = citation.get("bib_key")
        ctx = extract_claim_context(proj.get("tex_content") or "", citation,
                                    max_paragraph_chars=max_para,
                                    max_sentence_chars=max_sent)

        # Find reference + .md
        results_by_key = {r.get("bib_key"): r for r in (proj.get("results") or [])}
        ref_result = results_by_key.get(bib_key)
        title = (ref_result or {}).get("title") or ""
        project_dir = project_store.get_project_dir(slug)
        ref_md = load_reference_md(project_dir, bib_key) if bib_key else None
        if not ref_md:
            verdict = {
                "verdict": "unknown",
                "confidence": 0.0,
                "explanation": "No reference content (.md) available to check against."
                               if ref_result else "Citation key not found in project results.",
                "evidence_quote": "",
                "model": None,
                "checked_at": datetime.now().isoformat(),
                "input_tokens": 0,
                "output_tokens": 0,
            }
            ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], "", model)
            project_store.save_claim_check(slug, ck, verdict)
            project_store.set_citation_check_key(slug, idx, ck)
            return jsonify({"ok": True, "verdict": verdict, "cache_key": ck})

        ref_md_truncated = truncate_reference_md(ref_md, max_ref)
        ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], ref_md_truncated, model)
        existing = project_store.get_claim_check(slug, ck)
        if existing and not force and not is_setup_failure_verdict(existing):
            project_store.set_citation_check_key(slug, idx, ck)
            return jsonify({"ok": True, "verdict": existing, "cache_key": ck, "cached": True})

        verdict = check_citation(
            ctx["paragraph_clean"], ctx["sentence_clean"], ref_md_truncated,
            bib_key=bib_key, title=title, model=model, api_key=api_key, settings=settings,
        )
        project_store.save_claim_check(slug, ck, verdict)
        project_store.set_citation_check_key(slug, idx, ck)
        return jsonify({"ok": True, "verdict": verdict, "cache_key": ck})

    @app.route("/api/projects/<slug>/set-verdict/<int:idx>", methods=["POST"])
    def api_set_verdict(slug, idx):
        """Manually set the verdict for a single citation. Survives auto-checks."""
        from claim_checker import make_manual_verdict, manual_cache_key

        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        citations = proj.get("citations") or []
        if idx < 0 or idx >= len(citations):
            return jsonify({"error": "Citation index out of range"}), 404

        body = request.get_json(silent=True) or {}
        verdict_value = body.get("verdict")
        note = body.get("note")
        try:
            verdict = make_manual_verdict(verdict_value, note=note)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        ck = manual_cache_key(slug, idx)
        project_store.save_claim_check(slug, ck, verdict)
        project_store.set_citation_check_key(slug, idx, ck)
        cite_key = citations[idx].get("bib_key", "?")
        project_store.add_activity(slug, "manual_verdict",
                                   f"Set verdict on {cite_key} -> {verdict_value}", target=cite_key)
        return jsonify({"ok": True, "verdict": verdict, "cache_key": ck})

    @app.route("/api/projects/<slug>/clear-verdict/<int:idx>", methods=["POST"])
    def api_clear_verdict(slug, idx):
        """Remove the verdict pointer (manual or auto) for a citation."""
        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        citations = proj.get("citations") or []
        if idx < 0 or idx >= len(citations):
            return jsonify({"error": "Citation index out of range"}), 404
        project_store.set_citation_check_key(slug, idx, None)
        return jsonify({"ok": True})

    # --- batch ---

    # Per-batch cancellation flags, keyed by session id.
    _batch_cancel = {}
    _batch_cancel_lock = threading.Lock()

    @app.route("/api/projects/<slug>/check-citations", methods=["POST"])
    def api_check_citations(slug):
        from claim_checker import (
            estimate_batch_cost, run_batch, load_reference_md, truncate_reference_md,
        )
        from config import get_claim_check_settings, get_openai_api_key

        api_key = get_openai_api_key()
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 400

        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404
        citations = proj.get("citations") or []
        if not citations:
            return jsonify({"error": "No citations to check (upload a .tex file first)"}), 400

        body = request.get_json(silent=True) or {}
        force = bool(body.get("force"))
        model_override = body.get("model")

        settings = get_claim_check_settings()
        model = model_override or settings.get("openai_model") or "gpt-5-mini"
        max_batch_usd = float(settings.get("max_batch_usd", 5.0))

        results_by_key = {r.get("bib_key"): r for r in (proj.get("results") or [])}
        project_dir = project_store.get_project_dir(slug)
        estimate = estimate_batch_cost(
            proj.get("tex_content") or "", citations, project_dir, results_by_key,
            model=model, settings=settings,
        )
        if estimate["estimated_cost_usd"] > max_batch_usd and not body.get("override_cost"):
            return jsonify({
                "error": f"Estimated cost ${estimate['estimated_cost_usd']:.2f} exceeds max_batch_usd "
                         f"${max_batch_usd:.2f}. Adjust settings.json or pass override_cost=true.",
                "estimate": estimate,
            }), 409

        sid = store.create()
        store.update(sid, total=len(citations), status="running",
                     counts={}, model=model, estimate=estimate)

        def _on_progress(idx, total, citation, verdict, cache_key):
            store.add_result(sid, {
                "index": idx,
                "bib_key": citation.get("bib_key"),
                "verdict": verdict,
                "cache_key": cache_key,
            })

        def _cancel_flag():
            with _batch_cancel_lock:
                return _batch_cancel.get(sid, False)

        def _save_verdict(cache_key, verdict_dict):
            project_store.save_claim_check(slug, cache_key, verdict_dict)

        def _set_cite_key(idx, cache_key):
            project_store.set_citation_check_key(slug, idx, cache_key)

        def _run():
            try:
                summary = run_batch(
                    slug, force=force, model_override=model,
                    cancel_flag=_cancel_flag, on_progress=_on_progress,
                    save_callbacks={"save_verdict": _save_verdict, "set_cite_key": _set_cite_key},
                )
                store.update(sid, status="completed",
                             cancelled=summary.get("cancelled", False),
                             counts=summary.get("counts", {}))
                c = summary.get("counts", {})
                project_store.add_activity(slug, "claim_check_batch",
                    f"Claim check completed: {c.get('supported',0)} supported, "
                    f"{c.get('partial',0)} partial, {c.get('not_supported',0)} not supported, "
                    f"{c.get('unknown',0)} unknown")
            except Exception as e:
                logger.exception("check-citations batch failed: %s", e)
                store.update(sid, status="error", error=str(e))
            finally:
                with _batch_cancel_lock:
                    _batch_cancel.pop(sid, None)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            "ok": True,
            "session_id": sid,
            "n_citations": len(citations),
            "estimate": estimate,
        })

    @app.route("/api/projects/<slug>/check-citations/<sid>/stop", methods=["POST"])
    def api_check_citations_stop(slug, sid):
        with _batch_cancel_lock:
            _batch_cancel[sid] = True
        return jsonify({"ok": True})

    @app.route("/api/projects/<slug>/check-status/<sid>")
    def api_check_status(slug, sid):
        last_id = request.headers.get("Last-Event-ID")
        try:
            initial_sent = int(last_id) + 1 if last_id else 0
        except ValueError:
            initial_sent = 0

        def generate():
            import time
            sent = initial_sent
            last_heartbeat = time.time()
            while True:
                session = store.get(sid)
                if session is None:
                    break

                results = session["results"]
                total = session.get("total", 0)

                while sent < len(results):
                    r = results[sent]
                    payload = json.dumps({
                        "index": r["index"],
                        "total": total,
                        "bib_key": r["bib_key"],
                        "verdict": r["verdict"],
                        "cache_key": r["cache_key"],
                        "progress": sent + 1,
                    })
                    yield f"id: {sent}\nevent: progress\ndata: {payload}\n\n"
                    sent += 1

                if session.get("status") in ("completed", "error") and sent >= len(session["results"]):
                    payload = json.dumps({
                        "status": session.get("status"),
                        "cancelled": session.get("cancelled", False),
                        "counts": session.get("counts", {}),
                        "error": session.get("error"),
                    })
                    yield f"event: complete\ndata: {payload}\n\n"
                    break

                now = time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now

                time.sleep(0.2)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ================================================================
    # Reference identity match (title + authors verification)
    # ================================================================

    _ref_match_cancel = {}
    _ref_match_cancel_lock = threading.Lock()

    @app.route("/api/projects/<slug>/check-reference-match/<bib_key>", methods=["POST"])
    def api_check_reference_match(slug, bib_key):
        """Run the LLM identity check for a single reference (synchronous)."""
        from reference_matcher import check_and_save
        body = request.get_json(silent=True) or {}
        force = bool(body.get("force", True))   # single-ref defaults to force
        match = check_and_save(slug, bib_key, force=force)
        if match is None:
            return jsonify({"error": "Project or reference not found"}), 404
        return jsonify({"ok": True, "bib_key": bib_key, "ref_match": match})

    @app.route("/api/projects/<slug>/set-ref-match/<bib_key>", methods=["POST"])
    def api_set_ref_match(slug, bib_key):
        """Manually override the reference-match verdict (matched / not_matched).

        Body: { verdict: "matched" | "not_matched", note?: str }.
        """
        from reference_matcher import make_manual_match
        body = request.get_json(silent=True) or {}
        verdict_value = body.get("verdict")
        note = body.get("note")
        try:
            match = make_manual_match(verdict_value, note=note)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        ok = project_store.save_ref_match(slug, bib_key, match)
        if not ok:
            return jsonify({"error": "Project or reference not found"}), 404
        project_store.add_activity(slug, "manual_ref_match",
                                   f"Set reference match for {bib_key} -> {verdict_value}",
                                   target=bib_key)
        return jsonify({"ok": True, "bib_key": bib_key, "ref_match": match})

    @app.route("/api/projects/<slug>/clear-ref-match/<bib_key>", methods=["POST"])
    def api_clear_ref_match(slug, bib_key):
        """Remove the ref_match field entirely (treat as never-checked)."""
        ok = project_store.save_ref_match(slug, bib_key, None)
        if not ok:
            return jsonify({"error": "Project or reference not found"}), 404
        return jsonify({"ok": True, "bib_key": bib_key})

    @app.route("/api/projects/<slug>/check-references-match", methods=["POST"])
    def api_check_references_match_batch(slug):
        """Kick off batch identity-check for every reference in the project.

        Body: { force?: bool, model?: str }.
        Returns: { session_id }. Client polls SSE at /ref-match-status/<sid>.
        """
        from reference_matcher import run_batch
        from config import get_openai_api_key

        if not get_openai_api_key():
            return jsonify({"error": "OpenAI API key not configured"}), 400

        proj = project_store.get_project(slug)
        if proj is None:
            return jsonify({"error": "Project not found"}), 404

        results = proj.get("results") or []
        if not results:
            return jsonify({"error": "No references to check"}), 400

        body = request.get_json(silent=True) or {}
        force = bool(body.get("force"))
        model_override = body.get("model")

        sid = store.create()
        store.update(sid, total=len(results), status="running", counts={})

        def _on_progress(bib_key, match):
            store.add_result(sid, {"bib_key": bib_key, "ref_match": match})

        def _cancel_flag():
            with _ref_match_cancel_lock:
                return _ref_match_cancel.get(sid, False)

        def _run():
            try:
                summary = run_batch(slug, force=force, model_override=model_override,
                                    cancel_flag=_cancel_flag, on_progress=_on_progress)
                store.update(sid, status="completed",
                             cancelled=summary.get("cancelled", False),
                             counts=summary.get("counts", {}))
                c = summary.get("counts", {})
                project_store.add_activity(slug, "ref_match_batch",
                    f"Reference-match completed: {c.get('matched',0)} matched, "
                    f"{c.get('not_matched',0)} not matched, "
                    f"{c.get('unverifiable',0) + c.get('skipped_no_md',0)} unverifiable")
            except Exception as e:
                logger.exception("check-references-match batch failed: %s", e)
                store.update(sid, status="error", error=str(e))
            finally:
                with _ref_match_cancel_lock:
                    _ref_match_cancel.pop(sid, None)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True, "session_id": sid, "n_references": len(results)})

    @app.route("/api/projects/<slug>/check-references-match/<sid>/stop", methods=["POST"])
    def api_check_references_match_stop(slug, sid):
        with _ref_match_cancel_lock:
            _ref_match_cancel[sid] = True
        return jsonify({"ok": True})

    @app.route("/api/projects/<slug>/ref-match-status/<sid>")
    def api_ref_match_status(slug, sid):
        last_id = request.headers.get("Last-Event-ID")
        try:
            initial_sent = int(last_id) + 1 if last_id else 0
        except ValueError:
            initial_sent = 0

        def generate():
            import time as _time
            sent = initial_sent
            last_heartbeat = _time.time()
            while True:
                session = store.get(sid)
                if session is None:
                    break
                results = session["results"]
                total = session.get("total", 0)
                while sent < len(results):
                    r = results[sent]
                    payload = json.dumps({
                        "bib_key": r["bib_key"],
                        "ref_match": r["ref_match"],
                        "progress": sent + 1,
                        "total": total,
                    })
                    yield f"id: {sent}\nevent: progress\ndata: {payload}\n\n"
                    sent += 1
                if session.get("status") in ("completed", "error") and sent >= len(session["results"]):
                    payload = json.dumps({
                        "status": session.get("status"),
                        "cancelled": session.get("cancelled", False),
                        "counts": session.get("counts", {}),
                        "error": session.get("error"),
                    })
                    yield f"event: complete\ndata: {payload}\n\n"
                    break
                now = _time.time()
                if now - last_heartbeat > 15:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = now
                _time.sleep(0.2)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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

    # ================================================================
    # Validity report (HTML + references.zip bundle)
    # See validity_report_v1.md for spec.
    # ================================================================

    @app.route("/api/projects/<slug>/download-stats", methods=["GET"])
    def api_download_stats(slug):
        """v6.1 A3: aggregate per-tier + per-host download telemetry for the
        project dashboard. Feeds the Top Blocked Hosts card."""
        stats = project_store.compute_download_stats(slug)
        if stats is None:
            return jsonify({"error": "Project not found"}), 404
        return jsonify({"ok": True, "stats": stats})

    @app.route("/api/projects/<slug>/validity-report", methods=["POST"])
    def api_build_validity_report(slug):
        """Build/rebuild the validity-report bundle on disk and return paths."""
        from validity_report import build_validity_report
        try:
            _html, html_path, zip_path = build_validity_report(slug)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            logger.exception("validity-report build failed for %s: %s", slug, e)
            return jsonify({"error": str(e)}), 500
        project_store.add_activity(slug, "validity_report",
                                   "Built validity report")
        return jsonify({
            "ok": True,
            "html_url": f"/projects/{slug}/validity-report/{slug}_report.html",
            "zip_url":  f"/projects/{slug}/validity-report/references.zip",
            "html_size": os.path.getsize(html_path) if os.path.isfile(html_path) else 0,
            "zip_size":  os.path.getsize(zip_path) if os.path.isfile(zip_path) else 0,
        })

    @app.route("/api/projects/<slug>/validity-report/download", methods=["GET"])
    def api_download_validity_report_html(slug):
        """Download just the report HTML (rebuilds first to ensure fresh)."""
        from validity_report import build_validity_report
        try:
            _html, html_path, _zip = build_validity_report(slug)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return send_from_directory(
            os.path.dirname(html_path), os.path.basename(html_path),
            as_attachment=True, download_name=f"{slug}_report.html")

    @app.route("/api/projects/<slug>/validity-report/references-zip", methods=["GET"])
    def api_download_validity_report_zip(slug):
        """Download the references bundle (zip). Auto-builds if missing."""
        from validity_report import build_validity_report
        out_dir = os.path.join(PROJECTS_DIR, slug, "validity-report")
        zip_path = os.path.join(out_dir, "references.zip")
        if not os.path.isfile(zip_path):
            try:
                build_validity_report(slug)
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
        return send_from_directory(out_dir, "references.zip",
                                    as_attachment=True, download_name="references.zip")

    @app.route("/projects/<slug>/validity-report/<path:filename>")
    def projects_validity_static(slug, filename):
        """Static-style serving for the saved validity-report folder tree
        (so the HTML's relative `references/<file>` links work in-browser).
        Path-traversal is contained because send_from_directory normalizes
        and rejects escapes outside its root."""
        out_dir = os.path.join(PROJECTS_DIR, slug, "validity-report")
        if not os.path.isdir(out_dir):
            return jsonify({"error": "Report not built yet"}), 404
        return send_from_directory(out_dir, filename)

    return app


if __name__ == "__main__":
    store.start_cleanup_thread()
    app = create_app()
    print(f"Starting app on http://localhost:{FLASK_PORT}")
    app.run(debug=True, port=FLASK_PORT, threaded=True)
