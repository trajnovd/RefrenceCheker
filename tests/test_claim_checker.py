"""Unit tests for v4 claim-check pipeline.

Covers:
- tex_parser.extract_claim_context (paragraph + sentence boundaries, LaTeX cleanup)
- claim_checker.truncate_reference_md (header preservation, body truncation)
- claim_checker.cache_key_for (determinism, model sensitivity)
- claim_checker.is_setup_failure_verdict (cache invalidation policy)
- claim_checker.check_citation (mocked OpenAI: happy path, empty content / length truncation,
  malformed JSON, missing reference, missing API key)
- claim_checker.run_batch (orchestration: cache hits skip, setup-failure cache re-runs,
  no-md citations are skipped without LLM call, force=True ignores cache)
"""

import json
import os
from unittest.mock import patch, MagicMock
import pytest

from tex_parser import parse_tex_citations, extract_claim_context, clean_latex
from claim_checker import (
    check_citation, run_batch,
    truncate_reference_md, cache_key_for, is_setup_failure_verdict,
    load_reference_md, _empty_verdict,
    is_manual_verdict, make_manual_verdict, manual_cache_key,
)


# ============================================================
# tex_parser.extract_claim_context
# ============================================================

class TestExtractClaimContext:
    def test_extracts_paragraph_bounded_by_blank_lines(self):
        tex = (
            "Some intro paragraph.\n\n"
            "This is the target paragraph with \\cite{smith2020} in it.\n\n"
            "Another paragraph after."
        )
        cites = parse_tex_citations(tex)
        assert len(cites) == 1
        ctx = extract_claim_context(tex, cites[0])
        assert "target paragraph" in ctx["paragraph"]
        assert "intro paragraph" not in ctx["paragraph"]
        assert "Another paragraph" not in ctx["paragraph"]

    def test_extracts_sentence_with_abbreviation_safety(self):
        tex = "We use this approach, e.g. as shown in \\cite{x2020}, with success."
        cites = parse_tex_citations(tex)
        ctx = extract_claim_context(tex, cites[0])
        # 'e.g.' should NOT split the sentence
        assert "e.g." in ctx["sentence"]

    def test_paragraph_breaks_at_section(self):
        tex = (
            "\\section{Old}\nFirst para.\n\n"
            "\\section{New}\nClaim sentence \\cite{key} here.\n"
        )
        cites = parse_tex_citations(tex)
        ctx = extract_claim_context(tex, cites[0])
        assert "First para" not in ctx["paragraph"]
        assert "Claim sentence" in ctx["paragraph"]

    def test_paragraph_breaks_at_environment(self):
        tex = (
            "Body text \\cite{key} here.\n\n"
            "\\begin{figure}\nblah\n\\end{figure}\n\n"
            "Other section."
        )
        cites = parse_tex_citations(tex)
        ctx = extract_claim_context(tex, cites[0])
        assert "Body text" in ctx["paragraph"]
        assert "blah" not in ctx["paragraph"]

    def test_clean_latex_replaces_cite_with_placeholder(self):
        out = clean_latex("see \\cite{smith2020} for details")
        assert "[CITE:smith2020]" in out
        assert "\\cite" not in out

    def test_clean_latex_strips_comments(self):
        out = clean_latex("real text % a comment to drop\nmore text")
        assert "comment" not in out
        assert "real text" in out
        assert "more text" in out

    def test_clean_latex_unwraps_formatting(self):
        out = clean_latex("\\textbf{bold} and \\emph{em}")
        assert "bold" in out
        assert "em" in out
        assert "textbf" not in out
        assert "emph" not in out

    def test_clean_latex_replaces_refs(self):
        out = clean_latex("see Figure \\ref{fig1}")
        assert "[REF]" in out
        assert "fig1" not in out


# ============================================================
# truncate_reference_md
# ============================================================

class TestTruncateReferenceMd:
    def test_short_md_returned_intact(self):
        md = "# Title\n\n## Abstract\nshort\n## Full text\nbody"
        assert truncate_reference_md(md, 10000) == md

    def test_keeps_header_and_abstract_intact(self):
        head = "# Title\n\n- meta\n\n## Abstract\nthe abstract\n"
        body = "## Full text\n" + "x" * 10000
        md = head + body
        out = truncate_reference_md(md, len(head) + 100)
        assert out.startswith(head)
        assert "## Full text" in out
        assert "[... truncated ...]" in out
        assert len(out) < len(md)

    def test_no_full_text_marker_hard_truncates(self):
        md = "x" * 5000
        out = truncate_reference_md(md, 1000)
        assert len(out) == 1000

    def test_empty_returns_empty(self):
        assert truncate_reference_md("", 100) == ""
        assert truncate_reference_md(None, 100) == ""


# ============================================================
# cache_key_for
# ============================================================

class TestCacheKey:
    def test_deterministic(self):
        a = cache_key_for("p", "s", "r", "m")
        b = cache_key_for("p", "s", "r", "m")
        assert a == b

    def test_changes_with_model(self):
        a = cache_key_for("p", "s", "r", "model-1")
        b = cache_key_for("p", "s", "r", "model-2")
        assert a != b

    def test_changes_with_paragraph(self):
        a = cache_key_for("p1", "s", "r", "m")
        b = cache_key_for("p2", "s", "r", "m")
        assert a != b


# ============================================================
# is_setup_failure_verdict
# ============================================================

class TestIsSetupFailure:
    def test_real_verdict_with_model_is_not_setup_failure(self):
        v = {"verdict": "supported", "model": "gpt-5-mini", "explanation": "ok"}
        assert is_setup_failure_verdict(v) is False

    def test_openai_not_installed_is_setup_failure(self):
        v = {"verdict": "unknown", "model": None,
             "explanation": "openai package not installed"}
        assert is_setup_failure_verdict(v) is True

    def test_no_api_key_is_setup_failure(self):
        v = {"verdict": "unknown", "model": None,
             "explanation": "OpenAI API key not configured"}
        assert is_setup_failure_verdict(v) is True

    def test_no_md_is_NOT_setup_failure_keeps_cache(self):
        v = {"verdict": "unknown", "model": None,
             "explanation": "No reference content (.md) available to check against."}
        assert is_setup_failure_verdict(v) is False

    def test_unmatched_key_is_NOT_setup_failure(self):
        v = {"verdict": "unknown", "model": None,
             "explanation": "Citation key not found in project results."}
        assert is_setup_failure_verdict(v) is False

    def test_truncated_is_setup_failure(self):
        v = {"verdict": "unknown", "model": "gpt-5-mini", "error": "truncated"}
        assert is_setup_failure_verdict(v) is True

    def test_malformed_json_is_setup_failure(self):
        v = {"verdict": "unknown", "model": "gpt-5-mini", "error": "malformed_json"}
        assert is_setup_failure_verdict(v) is True

    def test_none_verdict_is_setup_failure(self):
        assert is_setup_failure_verdict(None) is True


# ============================================================
# check_citation (mocked OpenAI)
# ============================================================

def _mock_openai_response(content, finish_reason="stop", in_tok=100, out_tok=50):
    """Build a mock response object matching openai SDK shape."""
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=in_tok, completion_tokens=out_tok)
    return resp


class TestCheckCitation:
    def test_no_api_key_returns_unknown(self):
        v = check_citation("p", "s", "ref text", bib_key="k", api_key="", settings={})
        assert v["verdict"] == "unknown"
        assert "api key" in v["explanation"].lower()
        assert v["model"] is None

    def test_no_reference_md_returns_unknown(self):
        v = check_citation("p", "s", "", bib_key="k", api_key="sk-xxx", settings={})
        assert v["verdict"] == "unknown"
        assert "reference content" in v["explanation"].lower()

    def test_happy_path(self):
        good_json = json.dumps({
            "verdict": "supported", "confidence": 0.9,
            "explanation": "It says so.", "evidence_quote": "the model wins",
        })
        with patch("openai.OpenAI") as Mock:
            Mock.return_value.chat.completions.create.return_value = _mock_openai_response(good_json)
            v = check_citation("p", "s", "ref", bib_key="k",
                               api_key="sk-xxx", settings={"max_retries": 0})
        assert v["verdict"] == "supported"
        assert v["confidence"] == 0.9
        assert v["explanation"] == "It says so."
        assert v["evidence_quote"] == "the model wins"
        assert v.get("error") is None

    def test_empty_content_with_length_finish_bumps_and_returns_truncated(self):
        # All retries return empty content with finish_reason=length until budget exhausted.
        with patch("openai.OpenAI") as Mock:
            Mock.return_value.chat.completions.create.return_value = _mock_openai_response(
                "", finish_reason="length", out_tok=8000
            )
            v = check_citation("p", "s", "ref", bib_key="k", model="gpt-5-mini",
                               api_key="sk-xxx", settings={"max_retries": 0})
        assert v["verdict"] == "unknown"
        assert v["error"] == "truncated"
        assert "truncated" in v["explanation"].lower()

    def test_malformed_json_returns_error(self):
        with patch("openai.OpenAI") as Mock:
            Mock.return_value.chat.completions.create.return_value = _mock_openai_response("not json{")
            v = check_citation("p", "s", "ref", bib_key="k",
                               api_key="sk-xxx", settings={"max_retries": 0})
        assert v["verdict"] == "unknown"
        assert v["error"] == "malformed_json"

    def test_invalid_verdict_value_normalized_to_unknown(self):
        # Model returned a verdict outside the allowed set.
        with patch("openai.OpenAI") as Mock:
            Mock.return_value.chat.completions.create.return_value = _mock_openai_response(
                json.dumps({"verdict": "maybe", "confidence": 0.5,
                            "explanation": "x", "evidence_quote": ""})
            )
            v = check_citation("p", "s", "ref", bib_key="k",
                               api_key="sk-xxx", settings={"max_retries": 0})
        assert v["verdict"] == "unknown"

    def test_confidence_clamped_to_unit_interval(self):
        with patch("openai.OpenAI") as Mock:
            Mock.return_value.chat.completions.create.return_value = _mock_openai_response(
                json.dumps({"verdict": "supported", "confidence": 5.0,
                            "explanation": "x", "evidence_quote": ""})
            )
            v = check_citation("p", "s", "ref", bib_key="k",
                               api_key="sk-xxx", settings={"max_retries": 0})
        assert 0.0 <= v["confidence"] <= 1.0
        assert v["confidence"] == 1.0


# ============================================================
# run_batch (orchestration with mocked check_citation + project_store)
# ============================================================

class _FakeStore:
    """Minimal fake project_store for run_batch tests."""
    def __init__(self, project):
        self.project = project
        self.checks = dict(project.get("claim_checks") or {})
        self.cite_keys = {}
        self.project_dir = "/tmp/fake-project"

    def get_project(self, slug):
        return self.project

    def get_claim_check(self, slug, ck):
        return self.checks.get(ck)

    def get_project_dir(self, slug):
        return self.project_dir


def _make_project(citations, results=None, claim_checks=None):
    return {
        "slug": "test",
        "tex_content": "Para one.\n\nClaim sentence \\cite{k1} \\cite{k2}.\n",
        "citations": citations,
        "results": results or [{"bib_key": "k1", "title": "T1"}, {"bib_key": "k2", "title": "T2"}],
        "claim_checks": claim_checks or {},
    }


@pytest.fixture
def patched_store(tmp_path):
    """Patch project_store + load_reference_md for run_batch tests."""
    fake = _FakeStore(_make_project([
        {"bib_key": "k1", "position": 24, "end_position": 35, "line": 3,
         "context_before": "Claim sentence ", "context_after": ""},
        {"bib_key": "k2", "position": 36, "end_position": 47, "line": 3,
         "context_before": " ", "context_after": "."},
    ]))
    fake.project_dir = str(tmp_path)
    # Create k1.md so it has reference content; k2 has none
    (tmp_path / "k1.md").write_text("# T1\n\n## Abstract\nabs\n## Full text\nbody", encoding="utf-8")

    saved_verdicts = {}
    saved_keys = {}

    def save_v(ck, v):
        fake.checks[ck] = v
        saved_verdicts[ck] = v

    def save_k(idx, ck):
        saved_keys[idx] = ck

    with patch("project_store.get_project", side_effect=lambda s: fake.project), \
         patch("project_store.get_claim_check", side_effect=lambda s, ck: fake.checks.get(ck)), \
         patch("project_store.get_project_dir", side_effect=lambda s: fake.project_dir), \
         patch("claim_checker.get_openai_api_key", return_value="sk-test"), \
         patch("claim_checker.get_claim_check_settings", return_value={
             "openai_model": "gpt-5-mini", "max_paragraph_chars": 4000,
             "max_sentence_chars": 1500, "max_ref_chars": 100000, "max_retries": 0,
         }):
        yield {
            "fake": fake, "saved_verdicts": saved_verdicts, "saved_keys": saved_keys,
            "save_callbacks": {"save_verdict": save_v, "set_cite_key": save_k},
        }


class TestRunBatch:
    def test_skips_no_md_citations_without_calling_llm(self, patched_store):
        # Only k1 has .md; k2 should be skipped without an LLM call.
        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "supported", "model": "gpt-5-mini",
                                        "confidence": 0.9, "explanation": "ok",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            result = run_batch("test", save_callbacks=patched_store["save_callbacks"])
        assert mock_check.call_count == 1  # only k1
        assert result["counts"]["supported"] == 1
        assert result["counts"]["skipped"] == 1  # k2 had no .md

    def test_cache_hit_skips_llm_call(self, patched_store):
        # Pre-seed a real cached verdict for k1.
        from claim_checker import cache_key_for, truncate_reference_md
        ref_md = (patched_store["fake"].project_dir + "/k1.md")
        from pathlib import Path
        ref_text = Path(ref_md).read_text(encoding="utf-8")
        ref_truncated = truncate_reference_md(ref_text, 100000)
        # We need the same paragraph_clean / sentence_clean as run_batch will compute.
        from tex_parser import extract_claim_context
        cite = patched_store["fake"].project["citations"][0]
        ctx = extract_claim_context(patched_store["fake"].project["tex_content"], cite)
        ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], ref_truncated, "gpt-5-mini")
        patched_store["fake"].checks[ck] = {
            "verdict": "supported", "model": "gpt-5-mini", "confidence": 0.8,
            "explanation": "cached", "evidence_quote": "",
        }

        with patch("claim_checker.check_citation") as mock_check:
            run_batch("test", save_callbacks=patched_store["save_callbacks"])
        # 0 calls because cache hit on k1; k2 has no .md (also 0 calls)
        assert mock_check.call_count == 0

    def test_setup_failure_cache_does_NOT_block_retry(self, patched_store):
        # Pre-seed a SETUP-FAILURE verdict (model=None, "openai not installed").
        from claim_checker import cache_key_for, truncate_reference_md
        from tex_parser import extract_claim_context
        from pathlib import Path
        ref_text = Path(patched_store["fake"].project_dir + "/k1.md").read_text(encoding="utf-8")
        ref_truncated = truncate_reference_md(ref_text, 100000)
        cite = patched_store["fake"].project["citations"][0]
        ctx = extract_claim_context(patched_store["fake"].project["tex_content"], cite)
        ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], ref_truncated, "gpt-5-mini")
        patched_store["fake"].checks[ck] = {
            "verdict": "unknown", "model": None,
            "explanation": "openai package not installed",
        }

        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "supported", "model": "gpt-5-mini",
                                        "confidence": 0.9, "explanation": "real",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            run_batch("test", save_callbacks=patched_store["save_callbacks"])
        # Should re-call because the cached verdict was a setup failure.
        assert mock_check.call_count == 1

    def test_force_ignores_cache_even_for_real_verdicts(self, patched_store):
        from claim_checker import cache_key_for, truncate_reference_md
        from tex_parser import extract_claim_context
        from pathlib import Path
        ref_text = Path(patched_store["fake"].project_dir + "/k1.md").read_text(encoding="utf-8")
        ref_truncated = truncate_reference_md(ref_text, 100000)
        cite = patched_store["fake"].project["citations"][0]
        ctx = extract_claim_context(patched_store["fake"].project["tex_content"], cite)
        ck = cache_key_for(ctx["paragraph_clean"], ctx["sentence_clean"], ref_truncated, "gpt-5-mini")
        # Real cached verdict
        patched_store["fake"].checks[ck] = {
            "verdict": "supported", "model": "gpt-5-mini", "confidence": 0.8,
            "explanation": "cached", "evidence_quote": "",
        }

        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "partial", "model": "gpt-5-mini",
                                        "confidence": 0.6, "explanation": "fresh",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            run_batch("test", force=True, save_callbacks=patched_store["save_callbacks"])
        assert mock_check.call_count == 1
        assert patched_store["saved_verdicts"][ck]["verdict"] == "partial"

    def test_progress_callback_invoked_per_citation(self, patched_store):
        events = []
        def on_progress(idx, total, citation, verdict, ck):
            events.append((idx, citation["bib_key"], verdict["verdict"]))

        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "supported", "model": "gpt-5-mini",
                                        "confidence": 0.9, "explanation": "",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            run_batch("test", on_progress=on_progress,
                      save_callbacks=patched_store["save_callbacks"])

        # Two-pass design: fast-path (no-md) verdicts fire first, then API results.
        # Both citations are still reported, but the order may differ from document order.
        assert len(events) == 2
        keys = {e[1] for e in events}
        assert keys == {"k1", "k2"}

    def test_parallel_max_parallel_calls_concurrently(self, patched_store, tmp_path):
        """When max_parallel > 1, LLM calls should run concurrently. We verify by
        instrumenting check_citation to track the max simultaneous in-flight count."""
        import threading, time as _time
        # Add more k_* refs so we have enough parallel work
        fake = patched_store["fake"]
        tex = "Para.\n\nSentence \\cite{a} \\cite{b} \\cite{c} \\cite{d}.\n"
        fake.project["tex_content"] = tex
        fake.project["results"] = [{"bib_key": k, "title": k.upper()} for k in "abcd"]
        fake.project["citations"] = [
            {"bib_key": k, "position": 20 + i, "end_position": 30 + i, "line": 3,
             "context_before": "", "context_after": ""}
            for i, k in enumerate("abcd")
        ]
        # Create .md for each
        for k in "abcd":
            (tmp_path / f"{k}.md").write_text(f"# {k}\n\n## Abstract\nabs\n## Full text\nbody", encoding="utf-8")
        fake.project_dir = str(tmp_path)

        # Override max_parallel via settings
        with patch("claim_checker.get_claim_check_settings", return_value={
            "openai_model": "gpt-5-mini", "max_paragraph_chars": 4000,
            "max_sentence_chars": 1500, "max_ref_chars": 100000, "max_retries": 0,
            "max_parallel": 4,
        }):
            active = [0]
            peak = [0]
            lock = threading.Lock()

            def slow_check(*args, **kwargs):
                with lock:
                    active[0] += 1
                    if active[0] > peak[0]:
                        peak[0] = active[0]
                _time.sleep(0.05)
                with lock:
                    active[0] -= 1
                return {"verdict": "supported", "model": "gpt-5-mini", "confidence": 0.9,
                        "explanation": "", "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}

            with patch("claim_checker.check_citation", side_effect=slow_check):
                result = run_batch("test", save_callbacks=patched_store["save_callbacks"])

        assert result["counts"]["supported"] == 4
        # With 4 citations and max_parallel=4, peak concurrency should be >1
        assert peak[0] > 1, f"expected concurrent execution, got peak={peak[0]}"

    def test_parallel_max_parallel_1_is_serial(self, patched_store, tmp_path):
        """max_parallel=1 should behave exactly like the old serial path."""
        import threading, time as _time
        fake = patched_store["fake"]
        tex = "Para.\n\nSentence \\cite{a} \\cite{b}.\n"
        fake.project["tex_content"] = tex
        fake.project["results"] = [{"bib_key": k, "title": k.upper()} for k in "ab"]
        fake.project["citations"] = [
            {"bib_key": k, "position": 20 + i, "end_position": 30 + i, "line": 3,
             "context_before": "", "context_after": ""}
            for i, k in enumerate("ab")
        ]
        for k in "ab":
            (tmp_path / f"{k}.md").write_text(f"# {k}\n\n## Full text\nbody", encoding="utf-8")
        fake.project_dir = str(tmp_path)

        with patch("claim_checker.get_claim_check_settings", return_value={
            "openai_model": "gpt-5-mini", "max_paragraph_chars": 4000,
            "max_sentence_chars": 1500, "max_ref_chars": 100000, "max_retries": 0,
            "max_parallel": 1,
        }):
            active = [0]
            peak = [0]
            lock = threading.Lock()

            def slow_check(*args, **kwargs):
                with lock:
                    active[0] += 1
                    if active[0] > peak[0]:
                        peak[0] = active[0]
                _time.sleep(0.05)
                with lock:
                    active[0] -= 1
                return {"verdict": "supported", "model": "gpt-5-mini", "confidence": 0.9,
                        "explanation": "", "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}

            with patch("claim_checker.check_citation", side_effect=slow_check):
                run_batch("test", save_callbacks=patched_store["save_callbacks"])

        assert peak[0] == 1, f"max_parallel=1 must be serial, got peak={peak[0]}"

    def test_cancel_flag_stops_iteration(self, patched_store):
        events = []
        def on_progress(idx, total, citation, verdict, ck):
            events.append(idx)

        cancelled = [False]
        def cancel_flag():
            return cancelled[0]

        # Cancel after first citation has been processed.
        def on_check(*args, **kwargs):
            cancelled[0] = True
            return {"verdict": "supported", "model": "gpt-5-mini", "confidence": 0.9,
                    "explanation": "", "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}

        with patch("claim_checker.check_citation", side_effect=on_check) as mock_check:
            result = run_batch("test", cancel_flag=cancel_flag, on_progress=on_progress,
                               save_callbacks=patched_store["save_callbacks"])
        assert result.get("cancelled") is True


# ============================================================
# Manual verdict override
# ============================================================

class TestManualVerdict:
    def test_make_manual_verdict_shape(self):
        v = make_manual_verdict("not_supported", note="I disagree.")
        assert v["verdict"] == "not_supported"
        assert v["manual"] is True
        assert v["model"] == "manual"
        assert v["confidence"] == 1.0
        assert "I disagree." in v["explanation"]

    def test_make_manual_verdict_default_note(self):
        v = make_manual_verdict("supported")
        assert "manually" in v["explanation"].lower()

    def test_make_manual_verdict_rejects_invalid(self):
        with pytest.raises(ValueError):
            make_manual_verdict("maybe")
        with pytest.raises(ValueError):
            make_manual_verdict("")

    def test_is_manual_verdict(self):
        assert is_manual_verdict(make_manual_verdict("partial")) is True
        assert is_manual_verdict({"verdict": "supported", "model": "gpt-5-mini"}) is False
        assert is_manual_verdict(None) is False

    def test_manual_cache_key_is_stable(self):
        a = manual_cache_key("project-x", 5)
        b = manual_cache_key("project-x", 5)
        assert a == b

    def test_manual_cache_key_per_index_per_slug(self):
        assert manual_cache_key("a", 1) != manual_cache_key("a", 2)
        assert manual_cache_key("a", 1) != manual_cache_key("b", 1)

    def test_run_batch_skips_manual_verdicts(self, patched_store):
        # Pre-set a manual verdict on the FIRST citation (k1).
        manual_v = make_manual_verdict("not_supported", note="user override")
        ck = manual_cache_key("test", 0)
        patched_store["fake"].checks[ck] = manual_v
        # Tag citation 0 as having this manual verdict
        patched_store["fake"].project["citations"][0]["claim_check_key"] = ck

        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "supported", "model": "gpt-5-mini",
                                        "confidence": 0.9, "explanation": "auto",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            result = run_batch("test", save_callbacks=patched_store["save_callbacks"])
        # k1 has manual → skipped; k2 has no .md → also no LLM call
        assert mock_check.call_count == 0
        assert result["counts"]["manual"] == 1
        # The manual verdict counts toward its own bucket (not_supported)
        assert result["counts"]["not_supported"] == 1

    def test_run_batch_with_force_overwrites_manual(self, patched_store):
        manual_v = make_manual_verdict("not_supported")
        ck = manual_cache_key("test", 0)
        patched_store["fake"].checks[ck] = manual_v
        patched_store["fake"].project["citations"][0]["claim_check_key"] = ck

        with patch("claim_checker.check_citation") as mock_check:
            mock_check.return_value = {"verdict": "supported", "model": "gpt-5-mini",
                                        "confidence": 0.9, "explanation": "auto",
                                        "evidence_quote": "", "input_tokens": 0, "output_tokens": 0}
            run_batch("test", force=True, save_callbacks=patched_store["save_callbacks"])
        # force=True bypasses the manual guard
        assert mock_check.call_count == 1


# ============================================================
# load_reference_md (filesystem helper)
# ============================================================

class TestLoadReferenceMd:
    def test_returns_content_when_present(self, tmp_path):
        (tmp_path / "smith2020.md").write_text("# x\n\nbody", encoding="utf-8")
        out = load_reference_md(str(tmp_path), "smith2020")
        assert out == "# x\n\nbody"

    def test_returns_none_when_missing(self, tmp_path):
        assert load_reference_md(str(tmp_path), "notthere") is None

    def test_handles_unsafe_keys(self, tmp_path):
        # Path-traversal characters in bib_key should not escape project_dir
        (tmp_path / "evil_key.md").write_text("ok", encoding="utf-8")
        out = load_reference_md(str(tmp_path), "evil/key")
        # _safe_filename rewrites '/' to '_' so it should find evil_key.md
        assert out == "ok"
