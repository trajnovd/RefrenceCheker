"""LLM-based reference identity verification.

Defends against the wrong-paper failure mode: title-only API searches
(arXiv / S2 / Google) sometimes return an unrelated document for a generic
title, and the bib URL pre-download may also serve unrelated content.

For each downloaded reference we ask an LLM whether the bib's title and
authors actually appear near the start of the extracted text. The check
yields a separate `ref_match` field on the result so users see at a glance
whether the downloaded source matches the citation, and can override
manually for cases the LLM gets wrong.

Authors may be personal names ("Smith, J.") or institutional/corporate
("Federal Reserve", "Man Group"). The prompt accommodates both.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from config import get_reference_match_settings, get_openai_api_key

logger = logging.getLogger(__name__)


# ============================================================
# Verdict labels
# ============================================================
# matched              — LLM confirmed title (and authors if visible) appear in excerpt
# not_matched          — LLM says the document is the wrong one
# unverifiable         — no .md / no excerpt / LLM error / setup failure
# manual_matched       — user overrode to OK
# manual_not_matched   — user overrode to NOT OK
VERDICT_VALUES = {"matched", "not_matched", "unverifiable",
                  "manual_matched", "manual_not_matched"}


SYSTEM_PROMPT = (
    "You verify reference citations. The user gives you:\n"
    "  1. A claimed title from a bibliography entry.\n"
    "  2. Claimed author(s) from the same bib entry.\n"
    "  3. An excerpt from the FIRST PAGES of the actual document.\n\n"
    "Your job: is the excerpt from the SAME document the bib refers to?\n\n"
    "Rules:\n"
    "- Authors can be PERSONAL NAMES (e.g. \"John Smith\", \"Smith, J.\")\n"
    "  or an INSTITUTION / CORPORATE BODY (e.g. \"Federal Reserve\",\n"
    "  \"OECD\", \"Man Group\", \"U.S. Securities and Exchange Commission\").\n"
    "- For institutions, accept any reasonable variant naming the same\n"
    "  organization (logo text, abbreviation, sub-division name).\n"
    "- For personal names, accept first-initial / surname-only / re-ordered\n"
    "  variants and minor punctuation differences.\n"
    "- The title may differ in capitalization, punctuation, or sub-titles.\n"
    "  A clear paraphrase IS NOT a match — the words must substantially align.\n"
    "- Authors may not appear in the excerpt (e.g. excerpt skipped the byline,\n"
    "  or the document is a corporate report with a logo not in the text).\n"
    "  In that case set authors_found=null. Do NOT guess.\n"
    "- verdict=\"matched\" requires title_found=true. authors_found may be true\n"
    "  or null (ambiguous), but NOT false.\n"
    "- verdict=\"not_matched\" if title is clearly absent OR authors clearly\n"
    "  disagree (different person, different institution).\n\n"
    "Respond with valid JSON ONLY, matching this schema:\n"
    "{\n"
    "  \"title_found\": true | false,\n"
    "  \"authors_found\": true | false | null,\n"
    "  \"verdict\": \"matched\" | \"not_matched\",\n"
    "  \"reasoning\": \"<= 2 short sentences explaining the decision\"\n"
    "}\n"
)


def _safe_filename(bib_key):
    safe = re.sub(r'[<>:"/\\|?*]', '_', bib_key).strip('. ')
    return safe[:80]


def load_reference_md(project_dir, bib_key):
    """Read {safe_key}.md from the project directory. Returns full string or None."""
    path = os.path.join(project_dir, _safe_filename(bib_key) + ".md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.debug("Failed to read %s: %s", path, e)
        return None


def extract_first_pages(md_content, max_chars):
    """Return the first ~N chars of the body text (no metadata header).

    The .md is structured as:
      # Title
      - **Bib key:** ...      <- LITERAL bib metadata (would always pass title-found)
      ...
      ## Abstract
      <abstract>
      ## Full text
      <body>

    We start from "## Full text" if present so the LLM sees actual document
    text, not the bib header that we wrote ourselves. If there is no body,
    fall back to the abstract (which IS document content).
    """
    if not md_content:
        return ""
    # Prefer full text body
    full_text_marker = "\n## Full text"
    idx = md_content.find(full_text_marker)
    if idx >= 0:
        body = md_content[idx + len(full_text_marker):].lstrip("\n").lstrip()
        return body[:max_chars]
    # Fallback: abstract section (some refs have only abstract, no body)
    abs_marker = "\n## Abstract"
    idx = md_content.find(abs_marker)
    if idx >= 0:
        body = md_content[idx + len(abs_marker):].lstrip("\n").lstrip()
        return body[:max_chars]
    # Worst case: hand back the raw markdown (may include our header)
    return md_content[:max_chars]


def _format_authors(authors):
    """Normalize authors to a single readable string for the LLM prompt."""
    if not authors:
        return ""
    if isinstance(authors, list):
        return ", ".join(a for a in authors if a)
    return str(authors).strip()


def _empty_match(reason, *, verdict="unverifiable", model=None):
    return {
        "verdict": verdict,
        "title_found": None,
        "authors_found": None,
        "evidence": reason,
        "model": model,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manual": False,
    }


def is_manual_match(match):
    """True if the match verdict was set manually by the user."""
    return bool(match) and bool(match.get("manual"))


def make_manual_match(verdict, *, note=None):
    """Build a manual match dict.

    `verdict` should be the user's final-state label: "matched" or "not_matched".
    Stored verdict will be "manual_matched" / "manual_not_matched" so we can
    distinguish manual from automatic in the UI / counts.
    """
    if verdict not in ("matched", "not_matched"):
        raise ValueError(f"Invalid manual verdict: {verdict!r}")
    return {
        "verdict": "manual_" + verdict,
        "title_found": True if verdict == "matched" else False,
        "authors_found": True if verdict == "matched" else False,
        "evidence": note or "Set manually by the user.",
        "model": "manual",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manual": True,
    }


def is_setup_failure_match(match):
    """True if the cached match represents a transient setup failure (no API
    key, openai package missing, network error, malformed JSON) — these should
    NOT be trusted from cache and deserve a real retry. A semantic
    "unverifiable" (no .md present) is not a setup failure.
    """
    if not match:
        return True
    err = match.get("error")
    if err in ("truncated", "malformed_json", "network", "no_api_key", "no_openai_pkg"):
        return True
    return False


# ============================================================
# Single-reference check
# ============================================================

def check_reference_match(bib_key, title, authors, md_content, *,
                          model=None, api_key=None, settings=None):
    """Make one OpenAI call to verify the reference identity.

    Never raises — always returns a match dict. On any kind of skip / failure
    the verdict is "unverifiable" and `evidence` explains why.
    """
    if settings is None:
        settings = get_reference_match_settings()
    if api_key is None:
        api_key = get_openai_api_key()

    if not title:
        return _empty_match("No title in bib entry — nothing to verify against")
    if not md_content:
        return _empty_match("No reference content (.md) available to check against")
    if not api_key:
        return {**_empty_match("OpenAI API key not configured"), "error": "no_api_key"}

    model = model or settings.get("openai_model") or "gpt-5-mini"
    timeout = settings.get("request_timeout_s", 30)
    max_retries = int(settings.get("max_retries", 2))
    max_chars = int(settings.get("max_chars", 6000))

    excerpt = extract_first_pages(md_content, max_chars)
    if not excerpt.strip():
        return _empty_match("Reference .md exists but has no body text to check")

    try:
        from openai import OpenAI
    except ImportError:
        return {**_empty_match("openai package not installed"), "error": "no_openai_pkg"}

    authors_str = _format_authors(authors)
    user_msg = (
        f"CLAIMED TITLE: {title}\n\n"
        f"CLAIMED AUTHOR(S): {authors_str or '(none provided)'}\n\n"
        f"DOCUMENT EXCERPT (first pages):\n{excerpt}\n"
    )

    client = OpenAI(api_key=api_key, timeout=timeout)
    is_gpt5 = model.startswith("gpt-5")
    use_max_completion = is_gpt5
    drop_temperature = is_gpt5
    drop_response_format = False
    # gpt-5 reasoning models burn tokens on internal reasoning; give them headroom.
    max_out_tokens = 4000 if is_gpt5 else 400

    def _kw():
        kw = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }
        if not drop_response_format:
            kw["response_format"] = {"type": "json_object"}
        if not drop_temperature:
            kw["temperature"] = 0.1
        if use_max_completion:
            kw["max_completion_tokens"] = max_out_tokens
        else:
            kw["max_tokens"] = max_out_tokens
        return kw

    last_err = None
    attempt = 0
    budget_bumps = 0
    MAX_BUDGET_BUMPS = 2
    while True:
        try:
            resp = client.chat.completions.create(**_kw())
            content = resp.choices[0].message.content or ""
            finish_reason = getattr(resp.choices[0], "finish_reason", None)
            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
            out_tok = getattr(usage, "completion_tokens", 0) if usage else 0

            if not content.strip() and finish_reason == "length":
                if max_out_tokens < 16000 and budget_bumps < MAX_BUDGET_BUMPS:
                    logger.warning("[%s] %s returned empty content (length, used %d). Bumping budget.",
                                   bib_key, model, out_tok)
                    max_out_tokens = min(16000, max_out_tokens * 2)
                    budget_bumps += 1
                    continue
                return {**_empty_match(
                    f"{model} truncated before producing content (used {out_tok} tokens)",
                    model=model), "error": "truncated"}

            content = content or "{}"
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                return {**_empty_match(f"LLM returned malformed JSON: {e}", model=model),
                        "error": "malformed_json"}

            verdict = (parsed.get("verdict") or "").lower()
            if verdict not in ("matched", "not_matched"):
                # LLM gave something unexpected — keep it unverifiable
                return {**_empty_match(
                    f"LLM returned unexpected verdict: {parsed.get('verdict')!r}",
                    model=model)}

            return {
                "verdict": verdict,
                "title_found": parsed.get("title_found"),
                "authors_found": parsed.get("authors_found"),
                "evidence": str(parsed.get("reasoning") or "").strip()[:600],
                "model": model,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "manual": False,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
            }

        except Exception as e:
            last_err = e
            msg = str(e).lower()
            adjusted = False
            if "max_tokens" in msg and "max_completion_tokens" in msg and not use_max_completion:
                use_max_completion = True; adjusted = True
            if "temperature" in msg and not drop_temperature:
                drop_temperature = True; adjusted = True
            if "response_format" in msg and not drop_response_format:
                drop_response_format = True; adjusted = True
            if adjusted:
                logger.info("[%s] adjusting OpenAI params and retrying: %s", bib_key, e)
                continue
            if attempt < max_retries:
                wait = (2 ** attempt) + 0.5
                logger.warning("[%s] reference-match call failed (attempt %d/%d): %s — retry in %.1fs",
                               bib_key, attempt + 1, max_retries + 1, e, wait)
                time.sleep(wait)
                attempt += 1
                continue
            break

    return {**_empty_match(f"OpenAI request failed after {attempt + 1} attempts: {last_err}",
                           model=model), "error": "network"}


# ============================================================
# Batch
# ============================================================

def run_batch(slug, *, force=False, model_override=None,
              cancel_flag=None, on_progress=None):
    """Check title/author match for every reference in the project that has a .md.

    - Manual verdicts are ALWAYS sticky — they're the user's explicit decision and
      only the Clear action removes them. force=True does NOT override manual.
    - Skips refs whose existing match is a non-setup-failure verdict, unless force=True.
    - Refs with no .md get an "unverifiable" match recorded so the UI shows status.

    Persists each match via project_store.save_ref_match as it completes.

    Returns: {ok, processed, total, counts, cancelled?}.
    """
    import project_store
    from concurrent.futures import ThreadPoolExecutor, as_completed

    proj = project_store.get_project(slug)
    if proj is None:
        return {"ok": False, "error": "Project not found"}

    settings = get_reference_match_settings()
    api_key = get_openai_api_key()
    if not api_key:
        return {"ok": False, "error": "OpenAI API key not configured"}

    model = model_override or settings.get("openai_model") or "gpt-5-mini"
    try:
        max_parallel = max(1, int(settings.get("max_parallel", 4)))
    except (TypeError, ValueError):
        max_parallel = 4

    project_dir = project_store.get_project_dir(slug)
    results = proj.get("results") or []

    counts = {"matched": 0, "not_matched": 0, "unverifiable": 0,
              "manual_matched": 0, "manual_not_matched": 0,
              "skipped_cached": 0, "skipped_no_md": 0, "errors": 0}
    counts_lock = threading.Lock()

    def _bump(bucket):
        with counts_lock:
            counts[bucket] = counts.get(bucket, 0) + 1

    total = len(results)
    api_jobs = []  # [(bib_key, title, authors, md)]

    # ---- Pass 1: classify each result ----
    for r in results:
        if cancel_flag and cancel_flag():
            return {"ok": True, "cancelled": True, "processed": 0, "total": total, "counts": counts}

        bib_key = r.get("bib_key")
        if not bib_key:
            continue

        existing = r.get("ref_match")
        # Manual verdicts are ALWAYS sticky — user's explicit decision wins over
        # any auto-check. Use Clear to remove them if a fresh check is wanted.
        if existing and is_manual_match(existing):
            _bump(existing.get("verdict", "unverifiable"))
            if on_progress:
                on_progress(bib_key, existing)
            continue

        # Use cached non-error verdict unless force=True
        if existing and not force and not is_setup_failure_match(existing):
            _bump("skipped_cached")
            if on_progress:
                on_progress(bib_key, existing)
            continue

        title = r.get("title")
        authors = r.get("authors")
        md = load_reference_md(project_dir, bib_key)

        if not md:
            match = _empty_match("No reference content (.md) available")
            project_store.save_ref_match(slug, bib_key, match)
            _bump("skipped_no_md")
            if on_progress:
                on_progress(bib_key, match)
            continue

        api_jobs.append((bib_key, title, authors, md))

    # ---- Pass 2: parallel OpenAI calls ----
    if not api_jobs:
        return {"ok": True, "processed": total, "total": total, "counts": counts}

    def _call(job):
        bib_key, title, authors, md = job
        match = check_reference_match(bib_key, title, authors, md,
                                      model=model, api_key=api_key, settings=settings)
        return bib_key, match

    cancelled = False
    logger.info("ref-match batch: %d API calls, parallelism=%d", len(api_jobs), max_parallel)

    if max_parallel == 1:
        for job in api_jobs:
            if cancel_flag and cancel_flag():
                cancelled = True
                break
            bib_key, match = _call(job)
            project_store.save_ref_match(slug, bib_key, match)
            if match.get("error"):
                _bump("errors")
            _bump(match.get("verdict", "unverifiable"))
            if on_progress:
                on_progress(bib_key, match)
    else:
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {executor.submit(_call, j): j for j in api_jobs}
            for future in as_completed(futures):
                if cancel_flag and cancel_flag():
                    cancelled = True
                    for f in futures:
                        f.cancel()
                    break
                try:
                    bib_key, match = future.result()
                except Exception as e:
                    logger.exception("ref-match worker crashed: %s", e)
                    _bump("errors")
                    continue
                project_store.save_ref_match(slug, bib_key, match)
                if match.get("error"):
                    _bump("errors")
                _bump(match.get("verdict", "unverifiable"))
                if on_progress:
                    on_progress(bib_key, match)

    if cancelled:
        return {"ok": True, "cancelled": True, "processed": total, "total": total, "counts": counts}
    return {"ok": True, "processed": total, "total": total, "counts": counts}


# ============================================================
# Convenience for the auto-trigger path
# ============================================================

def check_and_save(slug, bib_key, *, force=False):
    """Convenience: load the result + .md from disk, run a single check, persist.

    Used by app.py auto-triggers (post-refresh, post-upload, post-paste etc.)
    when we want to update one reference's match without spinning up a batch.

    Manual verdicts are ALWAYS sticky — `force=True` does NOT override them.
    The user has to click Clear to wipe a manual verdict before a fresh check
    can run. This protects deliberate "this citation is wrong" decisions from
    being clobbered when files are re-downloaded.

    Returns the match dict (or None if the reference / project is gone).
    """
    import project_store
    proj = project_store.get_project(slug)
    if proj is None:
        return None
    result = None
    for r in proj.get("results") or []:
        if r.get("bib_key") == bib_key:
            result = r
            break
    if result is None:
        return None

    existing = result.get("ref_match")
    # Manual is sticky regardless of force — only the explicit Clear API removes it.
    if existing and is_manual_match(existing):
        return existing
    if existing and not force and not is_setup_failure_match(existing):
        return existing

    project_dir = project_store.get_project_dir(slug)
    md = load_reference_md(project_dir, bib_key)
    if not md:
        match = _empty_match("No reference content (.md) available")
        project_store.save_ref_match(slug, bib_key, match)
        return match

    match = check_reference_match(bib_key, result.get("title"), result.get("authors"), md)
    project_store.save_ref_match(slug, bib_key, match)
    return match
