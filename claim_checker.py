"""LLM-based citation claim verification (v4).

Given a paragraph + sentence around a citation and the referenced paper's .md,
ask an LLM whether the reference supports the claim. Returns a structured verdict.
"""

import os
import re
import json
import time
import threading
import hashlib
import logging
from datetime import datetime, timezone

from config import get_claim_check_settings, get_openai_api_key
from tex_parser import extract_claim_context

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are an expert academic reviewer. The user will give you:\n"
    "  1. A claim from a paper (paragraph + the specific sentence containing a citation).\n"
    "  2. The text of the referenced work.\n\n"
    "Decide whether the referenced work supports the claim. Be strict: support means the\n"
    "reference contains evidence, results, or assertions that directly back the claim.\n"
    'If the reference is only tangentially related, mark it "partial". If it contradicts or\n'
    'does not address the claim, mark it "not_supported". If the reference text is too\n'
    'short or off-topic to judge, mark it "unknown".\n\n'
    "Respond with valid JSON only, matching this schema:\n"
    "{\n"
    '  "verdict": "supported" | "partial" | "not_supported" | "unknown",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "explanation": "<= 2 sentences. Explain the verdict.",\n'
    '  "evidence_quote": "<= 300 chars verbatim from the reference, or empty string if none."\n'
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


def truncate_reference_md(md_content, max_chars):
    """Keep header + abstract intact; truncate body to fit budget.

    The .md is structured as:
      # Title
      - **Bib key:** ...
      ...
      ## Abstract
      <abstract>
      ## Full text
      <body>
    We always keep everything up to and including the abstract. The body is
    truncated to fit within max_chars (counted over the whole result).
    """
    if not md_content:
        return ""
    if len(md_content) <= max_chars:
        return md_content
    full_text_marker = "\n## Full text"
    idx = md_content.find(full_text_marker)
    if idx < 0:
        # No body section; just hard-truncate.
        return md_content[:max_chars]
    head = md_content[:idx]  # header + abstract
    body = md_content[idx:]
    remaining = max_chars - len(head)
    if remaining <= 0:
        return head[:max_chars]
    truncated_body = body[:remaining]
    if len(body) > remaining:
        truncated_body += "\n\n[... truncated ...]"
    return head + truncated_body


def cache_key_for(paragraph_clean, sentence_clean, ref_md_content, model):
    blob = f"{model}|{paragraph_clean}|{sentence_clean}|{ref_md_content}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate_input_tokens(paragraph_clean, sentence_clean, ref_md_content):
    """Rough token estimate (~4 chars/token). Used for pre-flight cost calc only."""
    chars = len(SYSTEM_PROMPT) + len(paragraph_clean) + len(sentence_clean) + len(ref_md_content) + 200
    return max(1, chars // 4)


def estimate_cost_usd(input_tokens, output_tokens=300, model="gpt-5-mini"):
    """Rough cost estimate. Prices per 1M tokens — verify before shipping."""
    # Defaults match gpt-5-mini approximate pricing.
    rates = {
        "gpt-5-mini":   (0.15, 0.60),
        "gpt-5":        (1.25, 10.00),
        "gpt-4o-mini":  (0.15, 0.60),
        "gpt-4o":       (2.50, 10.00),
    }
    in_rate, out_rate = rates.get(model, (0.15, 0.60))
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _build_user_message(paragraph_clean, sentence_clean, reference_md, bib_key, title):
    return (
        f"CLAIM PARAGRAPH:\n{paragraph_clean}\n\n"
        f"CLAIM SENTENCE (the cite is marked [CITE:{bib_key}]):\n{sentence_clean}\n\n"
        f'REFERENCED WORK (bib_key={bib_key}, title="{title or ""}"):\n{reference_md}\n'
    )


def is_manual_verdict(verdict):
    """True if the verdict was set manually by the user (overrides auto-checks)."""
    return bool(verdict) and bool(verdict.get("manual"))


def make_manual_verdict(verdict_value, *, note=None):
    """Build a manual verdict dict for the given verdict label."""
    if verdict_value not in ("supported", "partial", "not_supported", "unknown"):
        raise ValueError(f"Invalid verdict value: {verdict_value}")
    return {
        "verdict": verdict_value,
        "confidence": 1.0,
        "explanation": note or "Set manually by the user.",
        "evidence_quote": "",
        "model": "manual",
        "manual": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "input_tokens": 0,
        "output_tokens": 0,
    }


def manual_cache_key(slug, citation_index):
    """Stable cache key for a manual verdict — independent of paragraph/sentence/ref hash."""
    return "manual-" + hashlib.sha256(f"{slug}|{citation_index}".encode("utf-8")).hexdigest()[:32]


def is_setup_failure_verdict(verdict):
    """Return True if a cached verdict represents a transient setup error
    (no API key, openai not installed, transport failure, truncation) that
    should NOT be trusted from cache — it deserves a real retry.

    Semantic 'unknown' verdicts (no .md, no matching ref) are NOT setup failures
    and should remain cached.
    """
    if not verdict:
        return True
    if verdict.get("error") in ("truncated", "malformed_json"):
        return True
    if verdict.get("model") is None:
        # All API-call paths set model=<name>. model=None means we never reached the API.
        # Distinguish setup issues from intentional skips by the explanation text.
        expl = (verdict.get("explanation") or "").lower()
        intentional = ("no reference content" in expl
                       or "citation key not found" in expl
                       or "no .md" in expl)
        return not intentional
    return False


def _empty_verdict(reason, *, verdict="unknown"):
    return {
        "verdict": verdict,
        "confidence": 0.0,
        "explanation": reason,
        "evidence_quote": "",
        "model": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "input_tokens": 0,
        "output_tokens": 0,
    }


def check_citation(paragraph_clean, sentence_clean, reference_md, *,
                   bib_key, title="", model=None, api_key=None, settings=None):
    """Make one OpenAI call. Never raises — returns a verdict dict on errors too.

    Returns:
      {
        verdict, confidence, explanation, evidence_quote,
        model, checked_at, input_tokens, output_tokens,
        error  (only on failure)
      }
    """
    if settings is None:
        settings = get_claim_check_settings()
    if api_key is None:
        api_key = get_openai_api_key()
    if not api_key:
        return _empty_verdict("OpenAI API key not configured")
    if not reference_md:
        return _empty_verdict("No reference content (.md) available to check against")

    model = model or settings.get("openai_model") or "gpt-5-mini"
    timeout = settings.get("request_timeout_s", 60)
    max_retries = settings.get("max_retries", 3)

    try:
        from openai import OpenAI
    except ImportError:
        return _empty_verdict("openai package not installed")

    client = OpenAI(api_key=api_key, timeout=timeout)
    user_msg = _build_user_message(paragraph_clean, sentence_clean, reference_md, bib_key, title)

    # gpt-5 family uses different parameter names + restricts temperature.
    is_gpt5 = model.startswith("gpt-5")

    use_max_completion = is_gpt5
    drop_temperature = is_gpt5
    drop_response_format = False
    # Reasoning models (gpt-5 family) eat budget on internal reasoning tokens;
    # they need much more headroom than a non-reasoning model.
    max_out_tokens = 8000 if is_gpt5 else 800

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

    # Budget bumps for reasoning-model truncation are tracked separately from retries.
    last_err = None
    attempt = 0
    budget_bumps = 0
    MAX_BUDGET_BUMPS = 3
    while True:
        try:
            resp = client.chat.completions.create(**_kw())
            content = resp.choices[0].message.content or ""
            finish_reason = getattr(resp.choices[0], "finish_reason", None)
            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
            out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
            # Reasoning model truncated before producing content: bump budget and retry.
            if not content.strip() and finish_reason == "length":
                if max_out_tokens < 32000 and budget_bumps < MAX_BUDGET_BUMPS:
                    logger.warning("[%s] %s returned empty content (finish_reason=length, used %d tokens). Bumping budget and retrying.",
                                   bib_key, model, out_tok)
                    max_out_tokens = min(32000, max_out_tokens * 2)
                    budget_bumps += 1
                    continue
                return {**_empty_verdict(f"{model} truncated before producing content (finish_reason=length, used {out_tok} tokens). Increase max_completion_tokens."),
                        "model": model, "input_tokens": in_tok, "output_tokens": out_tok,
                        "error": "truncated"}
            content = content or "{}"
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                return {
                    **_empty_verdict(f"LLM returned malformed JSON: {e}"),
                    "model": model,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "error": "malformed_json",
                }
            verdict = (parsed.get("verdict") or "unknown").lower()
            if verdict not in ("supported", "partial", "not_supported", "unknown"):
                verdict = "unknown"
            try:
                confidence = float(parsed.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            return {
                "verdict": verdict,
                "confidence": max(0.0, min(1.0, confidence)),
                "explanation": str(parsed.get("explanation") or "").strip(),
                "evidence_quote": str(parsed.get("evidence_quote") or "").strip(),
                "model": model,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
            }
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            # Adjust params on parameter-related 400s and retry immediately (don't count as a failure).
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
                logger.warning("[%s] OpenAI call failed (attempt %d/%d): %s — retrying in %.1fs",
                               bib_key, attempt + 1, max_retries + 1, e, wait)
                time.sleep(wait)
                attempt += 1
                continue
            break

    return {**_empty_verdict(f"OpenAI request failed after {attempt + 1} attempts: {last_err}"),
            "model": model,
            "error": str(last_err)}


# ============================================================
# Batch orchestrator
# ============================================================

def estimate_batch_cost(tex_content, citations, project_dir, results_by_key,
                       *, model, settings):
    """Pre-flight cost estimate. Skips citations with no .md (no LLM call needed)."""
    max_para = settings.get("max_paragraph_chars", 4000)
    max_sent = settings.get("max_sentence_chars", 1500)
    max_ref = settings.get("max_ref_chars", 100000)
    total_in_tokens = 0
    n_callable = 0
    for citation in citations:
        bib_key = citation.get("bib_key")
        ref_md = load_reference_md(project_dir, bib_key) if bib_key else None
        if not ref_md:
            continue
        ctx = extract_claim_context(tex_content, citation,
                                    max_paragraph_chars=max_para,
                                    max_sentence_chars=max_sent)
        ref_md_t = truncate_reference_md(ref_md, max_ref)
        total_in_tokens += estimate_input_tokens(ctx["paragraph_clean"], ctx["sentence_clean"], ref_md_t)
        n_callable += 1
    cost = estimate_cost_usd(total_in_tokens, output_tokens=300 * max(1, n_callable), model=model)
    return {"n_total": len(citations), "n_callable": n_callable,
            "estimated_input_tokens": total_in_tokens, "estimated_cost_usd": round(cost, 6)}


def run_batch(slug, *, force=False, model_override=None,
              cancel_flag=None, on_progress=None, save_callbacks=None):
    """Iterate every citation, check it, persist via callbacks.

    Two-pass design:
      Pass 1 (fast, serial): filter out cache hits, manual verdicts, and
        citations with no .md. These are settled immediately and emit progress.
      Pass 2 (parallel, concurrent OpenAI calls): only citations that need a
        fresh LLM call. Parallelism from settings.claim_check.max_parallel.

    cancel_flag: a callable returning True if the batch should stop early.
    on_progress: callable(index, total, citation, verdict, cache_key).
    save_callbacks: dict with keys:
       'save_verdict'(cache_key, verdict_dict)
       'set_cite_key'(idx, cache_key)
    """
    import project_store
    from concurrent.futures import ThreadPoolExecutor, as_completed

    proj = project_store.get_project(slug)
    if proj is None:
        return {"ok": False, "error": "Project not found"}

    settings = get_claim_check_settings()
    api_key = get_openai_api_key()
    if not api_key:
        return {"ok": False, "error": "OpenAI API key not configured"}

    model = model_override or settings.get("openai_model") or "gpt-5-mini"
    max_para = settings.get("max_paragraph_chars", 4000)
    max_sent = settings.get("max_sentence_chars", 1500)
    max_ref = settings.get("max_ref_chars", 100000)
    try:
        max_parallel = max(1, int(settings.get("max_parallel", 4)))
    except (TypeError, ValueError):
        max_parallel = 4

    citations = proj.get("citations") or []
    tex_content = proj.get("tex_content") or ""
    results_by_key = {r.get("bib_key"): r for r in (proj.get("results") or [])}
    project_dir = project_store.get_project_dir(slug)

    save_verdict = (save_callbacks or {}).get("save_verdict")
    set_cite_key = (save_callbacks or {}).get("set_cite_key")

    counts = {"supported": 0, "partial": 0, "not_supported": 0, "unknown": 0,
              "skipped": 0, "errors": 0, "cached": 0, "manual": 0}
    counts_lock = threading.Lock()

    def _bump(bucket, verdict_key=None):
        with counts_lock:
            counts[bucket] = counts.get(bucket, 0) + 1
            if verdict_key:
                counts[verdict_key] = counts.get(verdict_key, 0) + 1

    total = len(citations)
    api_jobs = []  # [(idx, citation, bib_key, title, ctx, ref_md_t, cache_key)]

    # ---- Pass 1: walk citations serially, emit fast-path verdicts immediately ----
    for idx, citation in enumerate(citations):
        if cancel_flag and cancel_flag():
            return {"ok": True, "cancelled": True, "processed": idx, "total": total, "counts": counts}

        # Respect manual verdicts unless force=True overrides
        existing_key = citation.get("claim_check_key")
        if existing_key and not force:
            existing_verdict = project_store.get_claim_check(slug, existing_key)
            if is_manual_verdict(existing_verdict):
                _bump("manual", existing_verdict.get("verdict", "unknown"))
                if on_progress:
                    on_progress(idx, total, citation, existing_verdict, existing_key)
                continue

        bib_key = citation.get("bib_key")
        ref_result = results_by_key.get(bib_key)
        ref_md = load_reference_md(project_dir, bib_key) if bib_key else None
        ctx = extract_claim_context(tex_content, citation,
                                    max_paragraph_chars=max_para,
                                    max_sentence_chars=max_sent)

        if not ref_md:
            verdict = _empty_verdict(
                "No reference content (.md) available to check against."
                if ref_result else "Citation key not found in project results."
            )
            ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], "", model)
            if save_verdict:
                save_verdict(ck, verdict)
            if set_cite_key:
                set_cite_key(idx, ck)
            _bump("skipped", "unknown")
            if on_progress:
                on_progress(idx, total, citation, verdict, ck)
            continue

        ref_md_t = truncate_reference_md(ref_md, max_ref)
        ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], ref_md_t, model)
        existing = project_store.get_claim_check(slug, ck)
        if existing and not force and not is_setup_failure_verdict(existing):
            if set_cite_key:
                set_cite_key(idx, ck)
            _bump("cached", existing.get("verdict", "unknown"))
            if on_progress:
                on_progress(idx, total, citation, existing, ck)
            continue

        # Needs a fresh LLM call — queue for Pass 2
        title = (ref_result or {}).get("title") or ""
        api_jobs.append((idx, citation, bib_key, title, ctx, ref_md_t, ck))

    # ---- Pass 2: parallel OpenAI calls ----
    if not api_jobs:
        return {"ok": True, "processed": total, "total": total, "counts": counts}

    def _call(job):
        idx, citation, bib_key, title, ctx, ref_md_t, ck = job
        verdict = check_citation(
            ctx["paragraph_clean"], ctx["sentence_clean"], ref_md_t,
            bib_key=bib_key, title=title, model=model,
            api_key=api_key, settings=settings,
        )
        return job, verdict

    cancelled = False
    if max_parallel == 1:
        # Serial path (preserves original behavior exactly when user sets max_parallel=1)
        for job in api_jobs:
            if cancel_flag and cancel_flag():
                cancelled = True
                break
            _job, verdict = _call(job)
            idx, citation, bib_key, _t, _c, _r, ck = _job
            if save_verdict:
                save_verdict(ck, verdict)
            if set_cite_key:
                set_cite_key(idx, ck)
            if verdict.get("error"):
                _bump("errors")
            _bump(verdict.get("verdict", "unknown"))
            if on_progress:
                on_progress(idx, total, citation, verdict, ck)
    else:
        logger.info("claim-check batch: %d API calls, parallelism=%d", len(api_jobs), max_parallel)
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {executor.submit(_call, j): j for j in api_jobs}
            for future in as_completed(futures):
                if cancel_flag and cancel_flag():
                    cancelled = True
                    # Cancel pending futures; in-flight ones finish but we ignore them.
                    for f in futures:
                        f.cancel()
                    break
                try:
                    _job, verdict = future.result()
                except Exception as e:
                    logger.exception("claim-check worker crashed: %s", e)
                    _bump("errors")
                    continue
                idx, citation, bib_key, _t, _c, _r, ck = _job
                if save_verdict:
                    save_verdict(ck, verdict)
                if set_cite_key:
                    set_cite_key(idx, ck)
                if verdict.get("error"):
                    _bump("errors")
                _bump(verdict.get("verdict", "unknown"))
                if on_progress:
                    on_progress(idx, total, citation, verdict, ck)

    if cancelled:
        return {"ok": True, "cancelled": True, "processed": total, "total": total, "counts": counts}
    return {"ok": True, "processed": total, "total": total, "counts": counts}
