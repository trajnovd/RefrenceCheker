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
