import json
import os
import re
import shutil
import threading
import logging
from datetime import datetime
from config import PROJECTS_DIR

logger = logging.getLogger(__name__)

_locks = {}
_locks_lock = threading.Lock()


def _get_lock(slug):
    with _locks_lock:
        if slug not in _locks:
            _locks[slug] = threading.Lock()
        return _locks[slug]


def _ensure_dir():
    os.makedirs(PROJECTS_DIR, exist_ok=True)


def slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    slug = slug[:50]
    if not slug:
        slug = "project"
    # Handle collisions
    base = slug
    counter = 1
    while os.path.exists(os.path.join(PROJECTS_DIR, slug)):
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _write_json(slug, data):
    path = os.path.join(PROJECTS_DIR, slug, "project.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(slug):
    path = os.path.join(PROJECTS_DIR, slug, "project.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_project(name):
    _ensure_dir()
    slug = slugify(name)
    project_dir = os.path.join(PROJECTS_DIR, slug)
    os.makedirs(project_dir, exist_ok=True)

    now = datetime.now().isoformat()
    data = {
        "name": name,
        "slug": slug,
        "created_at": now,
        "updated_at": now,
        "bib_filename": None,
        "status": "created",
        "total": 0,
        "results": [],
        "parsed_refs": [],
    }
    _write_json(slug, data)
    logger.info("Created project: %s (%s)", name, slug)
    return data


def get_project(slug):
    return _read_json(slug)


def list_projects():
    _ensure_dir()
    projects = []
    for entry in os.scandir(PROJECTS_DIR):
        if entry.is_dir():
            data = _read_json(entry.name)
            if data:
                results = data.get("results", [])
                projects.append({
                    "name": data.get("name"),
                    "slug": data.get("slug"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "status": data.get("status"),
                    "total": data.get("total", 0),
                    "found_pdf": sum(1 for r in results if r.get("status") == "found_pdf"),
                    "found_abstract": sum(1 for r in results if r.get("status") == "found_abstract"),
                    "found_web_page": sum(1 for r in results if r.get("status") == "found_web_page"),
                    "not_found": sum(1 for r in results if r.get("status") in ("not_found", "insufficient_data", "parse_error")),
                })
    projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return projects


def delete_project(slug):
    project_dir = os.path.join(PROJECTS_DIR, slug)
    if not os.path.isdir(project_dir):
        return False
    shutil.rmtree(project_dir)
    with _locks_lock:
        _locks.pop(slug, None)
    logger.info("Deleted project: %s", slug)
    return True


def update_project(slug, **kwargs):
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        data.update(kwargs)
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)


def save_parsed_refs(slug, bib_filename, parsed_refs):
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        data["bib_filename"] = bib_filename
        data["parsed_refs"] = parsed_refs
        data["total"] = len(parsed_refs)
        data["results"] = []
        data["status"] = "processing"
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)


def save_result(slug, result):
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        bib_key = result.get("bib_key")
        # Update existing or append
        found = False
        for i, r in enumerate(data["results"]):
            if r.get("bib_key") == bib_key:
                data["results"][i] = result
                found = True
                break
        if not found:
            data["results"].append(result)
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)


def save_results_batch(slug, results_list):
    """Save multiple results at once (more efficient than individual saves)."""
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        existing = {r.get("bib_key"): i for i, r in enumerate(data["results"])}
        for result in results_list:
            bib_key = result.get("bib_key")
            if bib_key in existing:
                data["results"][existing[bib_key]] = result
            else:
                data["results"].append(result)
                existing[bib_key] = len(data["results"]) - 1
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)


def add_parsed_ref(slug, ref):
    """Append a single parsed_ref to project.json. Returns True on success,
    False if a parsed_ref with the same bib_key already exists.
    """
    bib_key = ref.get("bib_key")
    if not bib_key:
        return False
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return False
        existing = {r.get("bib_key") for r in (data.get("parsed_refs") or [])}
        if bib_key in existing:
            return False
        data.setdefault("parsed_refs", []).append(ref)
        data["total"] = len(data["parsed_refs"])
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)
        return True


def save_claim_check(slug, cache_key, verdict_dict):
    """Persist a single claim-check verdict under project.json["claim_checks"][cache_key]."""
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        checks = data.get("claim_checks") or {}
        checks[cache_key] = verdict_dict
        data["claim_checks"] = checks
        data["updated_at"] = datetime.now().isoformat()
        _write_json(slug, data)


def set_citation_check_key(slug, citation_index, cache_key):
    """Point citations[citation_index]["claim_check_key"] at the verdict cache_key.

    Pass cache_key=None to clear the pointer.
    """
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        citations = data.get("citations") or []
        if 0 <= citation_index < len(citations):
            if cache_key is None:
                citations[citation_index].pop("claim_check_key", None)
            else:
                citations[citation_index]["claim_check_key"] = cache_key
            data["citations"] = citations
            data["updated_at"] = datetime.now().isoformat()
            _write_json(slug, data)


def get_claim_check(slug, cache_key):
    data = _read_json(slug)
    if data is None:
        return None
    return (data.get("claim_checks") or {}).get(cache_key)


def save_ref_match(slug, bib_key, match_dict):
    """Update result.ref_match for the given bib_key. Returns True on success.

    Embedded in the result (not a separate dict) so it survives result rebuilds:
    every persisted result already carries this field.
    """
    if not bib_key:
        return False
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return False
        for r in data.get("results") or []:
            if r.get("bib_key") == bib_key:
                if match_dict is None:
                    r.pop("ref_match", None)
                else:
                    r["ref_match"] = match_dict
                data["updated_at"] = datetime.now().isoformat()
                _write_json(slug, data)
                return True
        return False


def get_ref_match(slug, bib_key):
    data = _read_json(slug)
    if data is None:
        return None
    for r in data.get("results") or []:
        if r.get("bib_key") == bib_key:
            return r.get("ref_match")
    return None


# ============================================================
# Download telemetry (v6.1 A3)
# ============================================================

def compute_download_stats(slug):
    """Aggregate per-tier + per-host download stats from all results in a project.

    Returns:
        {
          "total_attempts": int,
          "per_tier":      {tier_name: count},      # successful downloads
          "failed_by_host":{host: count},           # hosts with a failed attempt
          "top_blocked":   [{"host": ..., "refs": n, "suggested": "curl_cffi"}, ...]
        }

    Reads `result.download_log` (list of per-tier attempts, v6.1 §11.11) and
    `result.files_origin.pdf` (winning-tier stamp). Suggested-tier mapping is
    deterministic: SSRN / ResearchGate / econstor → curl_cffi; EUR-Lex /
    Elsevier portal → playwright; others → "manual upload".
    """
    data = _read_json(slug)
    if data is None:
        return None
    per_tier = {}
    failed_by_host = {}
    total_attempts = 0
    for r in data.get("results") or []:
        # Winning tier
        origin = (r.get("files_origin") or {}).get("pdf")
        if origin and origin.get("tier"):
            per_tier[origin["tier"]] = per_tier.get(origin["tier"], 0) + 1
        # All attempts → telemetry on failures
        for entry in (r.get("download_log") or []):
            total_attempts += 1
            if entry.get("ok"):
                continue
            url = entry.get("final_url") or ""
            # Extract host for failed attempts
            try:
                from urllib.parse import urlparse
                host = (urlparse(url).hostname or "").lower() if url else ""
            except Exception:
                host = ""
            if host:
                failed_by_host[host] = failed_by_host.get(host, 0) + 1
    # Build top_blocked with actionable suggestion
    top_blocked = []
    for host, n in sorted(failed_by_host.items(), key=lambda kv: -kv[1])[:5]:
        top_blocked.append({
            "host": host, "refs": n,
            "suggested": _suggest_tier_for(host),
        })
    return {
        "total_attempts": total_attempts,
        "per_tier": per_tier,
        "failed_by_host": failed_by_host,
        "top_blocked": top_blocked,
    }


def _suggest_tier_for(host):
    """Map a blocked host to a suggested v6.1 tier."""
    h = (host or "").lower()
    # TLS-impersonation targets
    if any(k in h for k in ("ssrn.com", "researchgate.net", "econstor.eu",
                             "sciencedirect.com", "wiley.com", "oup.com",
                             "springer.com", "jstor.org", "tandfonline.com")):
        return "curl_cffi"
    # JS-challenge targets
    if any(k in h for k in ("eur-lex.europa.eu", "europa.eu", "elsevier.com")):
        return "playwright"
    return "manual_upload"


def set_last_viewed_citation(slug, citation_index):
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        data["last_viewed_citation"] = citation_index
        _write_json(slug, data)


def get_last_viewed_citation(slug):
    data = _read_json(slug)
    if data is None:
        return 0
    return data.get("last_viewed_citation", 0)


def add_activity(slug, activity_type, message, target=None):
    """Append an entry to the project's activity log. Capped at 50 entries."""
    lock = _get_lock(slug)
    with lock:
        data = _read_json(slug)
        if data is None:
            return
        log = data.get("activity") or []
        entry = {"ts": datetime.now().isoformat(), "type": activity_type, "message": message}
        if target:
            entry["target"] = target
        log.append(entry)
        if len(log) > 50:
            log = log[-50:]
        data["activity"] = log
        _write_json(slug, data)


def get_parsed_ref(slug, bib_key):
    data = _read_json(slug)
    if data is None:
        return None
    for ref in data.get("parsed_refs", []):
        if ref.get("bib_key") == bib_key:
            return ref
    return None


def get_project_dir(slug):
    return os.path.join(PROJECTS_DIR, slug)
