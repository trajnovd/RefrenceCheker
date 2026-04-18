"""Tests for reference_matcher — LLM-based identity verification.

Covers:
- check_reference_match (single OpenAI call): matched / not_matched / unverifiable
- corporate-author handling
- make_manual_match override
- extract_first_pages excerpt logic
- run_batch (parallel) with cache, force, manual-respect
- check_and_save convenience helper
"""

import json
import os
from unittest.mock import patch, MagicMock

import pytest

from reference_matcher import (
    check_reference_match,
    extract_first_pages,
    is_manual_match,
    is_setup_failure_match,
    make_manual_match,
    load_reference_md,
    run_batch,
    check_and_save,
    SYSTEM_PROMPT,
)


# ============================================================
# extract_first_pages
# ============================================================

class TestExtractFirstPages:
    def test_skips_metadata_header(self):
        md = (
            "# Some Title\n"
            "- **Bib key:** ManTrendFollowing\n"
            "- **Authors:** Man Group\n"
            "- **Year:** 2026\n\n"
            "## Abstract\n\n"
            "This is the abstract.\n\n"
            "## Full text\n\n"
            "Real document body content here.\n"
        )
        out = extract_first_pages(md, 100)
        assert "Real document body" in out
        # The bib metadata lines must NOT be in the excerpt — otherwise the
        # title would always pass and the LLM check is meaningless.
        assert "**Bib key:**" not in out
        assert "Man Group" not in out

    def test_falls_back_to_abstract_when_no_body(self):
        md = (
            "# T\n\n"
            "## Abstract\n\n"
            "This abstract IS the document content.\n"
        )
        out = extract_first_pages(md, 100)
        assert "This abstract" in out

    def test_truncates_to_max_chars(self):
        body = "A" * 10000
        md = "# T\n\n## Full text\n\n" + body
        out = extract_first_pages(md, 500)
        assert len(out) == 500

    def test_handles_empty_input(self):
        assert extract_first_pages("", 100) == ""
        assert extract_first_pages(None, 100) == ""


# ============================================================
# load_reference_md
# ============================================================

class TestLoadReferenceMd:
    def test_loads_existing_file(self, tmp_path):
        path = tmp_path / "myref.md"
        path.write_text("# title\nbody", encoding="utf-8")
        assert load_reference_md(str(tmp_path), "myref") == "# title\nbody"

    def test_missing_file_returns_none(self, tmp_path):
        assert load_reference_md(str(tmp_path), "nope") is None

    def test_safe_filename_strips_invalid(self, tmp_path):
        # bib_key with characters that get replaced by safe_filename
        path = tmp_path / "x_y_z.md"
        path.write_text("body", encoding="utf-8")
        assert load_reference_md(str(tmp_path), "x/y\\z") == "body"


# ============================================================
# Manual override
# ============================================================

class TestManualMatch:
    def test_make_manual_matched(self):
        m = make_manual_match("matched", note="Looks fine to me")
        assert m["verdict"] == "manual_matched"
        assert m["title_found"] is True
        assert m["authors_found"] is True
        assert m["manual"] is True
        assert m["evidence"] == "Looks fine to me"
        assert m["model"] == "manual"

    def test_make_manual_not_matched(self):
        m = make_manual_match("not_matched")
        assert m["verdict"] == "manual_not_matched"
        assert m["title_found"] is False
        assert m["authors_found"] is False
        assert m["manual"] is True

    def test_invalid_verdict_raises(self):
        with pytest.raises(ValueError):
            make_manual_match("maybe")

    def test_is_manual_match(self):
        assert is_manual_match(make_manual_match("matched")) is True
        assert is_manual_match({"verdict": "matched", "manual": False}) is False
        assert is_manual_match(None) is False


# ============================================================
# Setup-failure detection (cache trust)
# ============================================================

class TestIsSetupFailureMatch:
    def test_no_match_is_setup_failure(self):
        assert is_setup_failure_match(None) is True

    def test_truncated_is_setup_failure(self):
        assert is_setup_failure_match({"error": "truncated", "verdict": "unverifiable"}) is True

    def test_network_error_is_setup_failure(self):
        assert is_setup_failure_match({"error": "network", "verdict": "unverifiable"}) is True

    def test_no_api_key_is_setup_failure(self):
        assert is_setup_failure_match({"error": "no_api_key"}) is True

    def test_normal_unverifiable_is_not_setup_failure(self):
        # No .md content — semantic, not transient
        assert is_setup_failure_match({"verdict": "unverifiable", "evidence": "No .md"}) is False

    def test_real_verdicts_are_not_setup_failures(self):
        assert is_setup_failure_match({"verdict": "matched", "model": "gpt-5-mini"}) is False
        assert is_setup_failure_match({"verdict": "not_matched", "model": "gpt-5-mini"}) is False


# ============================================================
# check_reference_match — guard rails (no API call)
# ============================================================

class TestCheckMatchGuards:
    def test_no_title_returns_unverifiable(self):
        m = check_reference_match("k", title=None, authors="Smith",
                                   md_content="some text", api_key="dummy")
        assert m["verdict"] == "unverifiable"
        assert "title" in m["evidence"].lower()

    def test_no_md_returns_unverifiable(self):
        m = check_reference_match("k", title="T", authors="Smith",
                                   md_content="", api_key="dummy")
        assert m["verdict"] == "unverifiable"
        assert "no reference content" in m["evidence"].lower()

    def test_no_api_key_returns_unverifiable_with_error(self):
        m = check_reference_match("k", title="T", authors="Smith",
                                   md_content="## Full text\n\nbody", api_key="")
        assert m["verdict"] == "unverifiable"
        assert m.get("error") == "no_api_key"

    def test_empty_excerpt_returns_unverifiable(self):
        # md exists but has no body or abstract — extract_first_pages may return empty
        m = check_reference_match("k", title="T", authors="Smith",
                                   md_content="   ", api_key="dummy")
        assert m["verdict"] == "unverifiable"


# ============================================================
# check_reference_match — mocked OpenAI call
# ============================================================

def _mock_openai_response(content, finish_reason="stop", in_tok=100, out_tok=50):
    """Build a MagicMock that mimics the openai client response shape."""
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg; choice.finish_reason = finish_reason
    usage = MagicMock(); usage.prompt_tokens = in_tok; usage.completion_tokens = out_tok
    resp = MagicMock(); resp.choices = [choice]; resp.usage = usage
    return resp


class TestCheckMatchOpenAI:
    """Mock the openai.OpenAI client and verify we parse / shape correctly."""

    def _patched_call(self, response_content, **call_kwargs):
        with patch("reference_matcher.OpenAI", create=True) as MockOpenAI:
            client = MagicMock()
            client.chat.completions.create.return_value = _mock_openai_response(response_content)
            MockOpenAI.return_value = client
            # Inject the symbol so the inner `from openai import OpenAI` works
            with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=MockOpenAI)}):
                return check_reference_match(
                    bib_key="k", title="My Paper Title", authors="Smith, J.",
                    md_content="## Full text\n\nReal body text",
                    api_key="dummy", **call_kwargs)

    def test_matched_verdict_parsed(self):
        m = self._patched_call(json.dumps({
            "title_found": True, "authors_found": True,
            "verdict": "matched", "reasoning": "Title and authors are on page 1."
        }))
        assert m["verdict"] == "matched"
        assert m["title_found"] is True
        assert m["authors_found"] is True
        assert "page 1" in m["evidence"]
        assert m["model"]
        assert m["manual"] is False

    def test_not_matched_verdict_parsed(self):
        m = self._patched_call(json.dumps({
            "title_found": False, "authors_found": False,
            "verdict": "not_matched", "reasoning": "Wrong paper."
        }))
        assert m["verdict"] == "not_matched"
        assert m["title_found"] is False

    def test_authors_found_null_kept(self):
        """authors_found can be null when the byline is not in the excerpt —
        the LLM should still be able to return matched on title alone."""
        m = self._patched_call(json.dumps({
            "title_found": True, "authors_found": None,
            "verdict": "matched", "reasoning": "Title found, byline not in excerpt"
        }))
        assert m["verdict"] == "matched"
        assert m["authors_found"] is None

    def test_unexpected_verdict_label_falls_back_to_unverifiable(self):
        m = self._patched_call(json.dumps({
            "title_found": True, "authors_found": True,
            "verdict": "kinda_matched", "reasoning": "..."
        }))
        assert m["verdict"] == "unverifiable"

    def test_malformed_json_response(self):
        m = self._patched_call("not valid json {[")
        assert m["verdict"] == "unverifiable"
        assert m.get("error") == "malformed_json"

    def test_evidence_truncated_to_600(self):
        m = self._patched_call(json.dumps({
            "title_found": True, "authors_found": True, "verdict": "matched",
            "reasoning": "x" * 1000,
        }))
        assert len(m["evidence"]) <= 600


class TestPromptHandlesCorporateAuthors:
    """The system prompt must instruct the LLM to handle institutional authors —
    a regression for the Man Group / Federal Reserve case."""

    def test_prompt_mentions_institution(self):
        assert "INSTITUTION" in SYSTEM_PROMPT.upper() or "institution" in SYSTEM_PROMPT
        # And includes concrete examples so the LLM has anchors
        assert "Federal Reserve" in SYSTEM_PROMPT or "OECD" in SYSTEM_PROMPT or "Man Group" in SYSTEM_PROMPT

    def test_prompt_mentions_authors_can_be_null(self):
        # When the excerpt doesn't include the byline, the LLM must be allowed
        # to return authors_found=null instead of guessing
        assert "null" in SYSTEM_PROMPT.lower()


# ============================================================
# check_and_save (auto-trigger convenience)
# ============================================================

class TestCheckAndSave:
    def test_returns_none_when_project_missing(self):
        with patch("project_store.get_project", return_value=None):
            assert check_and_save("nope", "k") is None

    def test_returns_none_when_ref_missing(self):
        with patch("project_store.get_project", return_value={"results": []}):
            assert check_and_save("slug", "k") is None

    def test_saves_unverifiable_when_no_md(self, tmp_path):
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A"}]}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("project_store.save_ref_match") as save:
            m = check_and_save("slug", "k")
        assert m["verdict"] == "unverifiable"
        save.assert_called_once_with("slug", "k", m)

    def test_manual_is_sticky_against_force(self, tmp_path):
        """Manual verdicts must survive force=True — the user explicitly set them.
        Auto-triggers (post-download) and Recheck buttons must not clobber a
        deliberate 'this citation is wrong' decision. Only the Clear API removes
        a manual verdict.

        Regression: findpo2024. After a Refresh, the auto-check ran with force=True
        and overwrote the user's manual_not_matched flag with a fresh LLM verdict,
        making the "wrong" citation look valid again."""
        manual = make_manual_match("not_matched")
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A", "ref_match": manual}]}
        md_path = tmp_path / "k.md"
        md_path.write_text("## Full text\n\nbody", encoding="utf-8")

        # force=False → returns existing manual without re-checking
        with patch("project_store.get_project", return_value=proj):
            m = check_and_save("slug", "k", force=False)
            assert m == manual

        # force=True → STILL respects manual; the API is not called
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("project_store.save_ref_match") as save, \
             patch("reference_matcher.check_reference_match") as mock_check:
            m = check_and_save("slug", "k", force=True)
        assert m == manual
        mock_check.assert_not_called()
        save.assert_not_called()


# ============================================================
# run_batch
# ============================================================

class TestRunBatch:
    def test_no_api_key_returns_error(self):
        with patch("project_store.get_project", return_value={"results": []}), \
             patch("reference_matcher.get_openai_api_key", return_value=""):
            r = run_batch("slug")
        assert r["ok"] is False
        assert "api key" in r["error"].lower()

    def test_skips_cached_when_not_force(self, tmp_path):
        cached = {"verdict": "matched", "title_found": True, "authors_found": True,
                  "evidence": "ok", "model": "test", "manual": False}
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A", "ref_match": cached}]}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("reference_matcher.get_openai_api_key", return_value="dummy"), \
             patch("reference_matcher.check_reference_match") as mock_check:
            r = run_batch("slug", force=False)
        # Did not re-call the API
        mock_check.assert_not_called()
        assert r["ok"] is True
        assert r["counts"]["skipped_cached"] == 1

    def test_skips_no_md_with_unverifiable(self, tmp_path):
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A"}]}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("reference_matcher.get_openai_api_key", return_value="dummy"), \
             patch("project_store.save_ref_match") as save:
            r = run_batch("slug", force=True)
        assert r["counts"]["skipped_no_md"] == 1
        save.assert_called_once()
        saved_match = save.call_args[0][2]
        assert saved_match["verdict"] == "unverifiable"

    def test_runs_api_for_refs_with_md(self, tmp_path):
        # Setup: one ref with .md, no existing match → should call the API
        (tmp_path / "k.md").write_text("## Full text\n\nbody", encoding="utf-8")
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A"}]}
        api_match = {"verdict": "matched", "title_found": True, "authors_found": True,
                     "evidence": "ok", "model": "test", "manual": False}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("reference_matcher.get_openai_api_key", return_value="dummy"), \
             patch("reference_matcher.check_reference_match", return_value=api_match), \
             patch("project_store.save_ref_match") as save:
            r = run_batch("slug", force=True)
        assert r["counts"]["matched"] == 1
        save.assert_called_once_with("slug", "k", api_match)

    def test_force_does_not_override_manual(self, tmp_path):
        """Even force=True must not clobber manual verdicts — the user explicitly
        set them. To re-check, the user must Clear first."""
        (tmp_path / "k.md").write_text("## Full text\n\nbody", encoding="utf-8")
        manual = make_manual_match("matched")
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A", "ref_match": manual}]}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("reference_matcher.get_openai_api_key", return_value="dummy"), \
             patch("reference_matcher.check_reference_match") as mock_check, \
             patch("project_store.save_ref_match") as save:
            r = run_batch("slug", force=True)
        mock_check.assert_not_called()
        save.assert_not_called()
        assert r["counts"]["manual_matched"] == 1

    def test_respects_manual_when_not_force(self, tmp_path):
        manual = make_manual_match("matched")
        proj = {"results": [{"bib_key": "k", "title": "T", "authors": "A", "ref_match": manual}]}
        with patch("project_store.get_project", return_value=proj), \
             patch("project_store.get_project_dir", return_value=str(tmp_path)), \
             patch("reference_matcher.get_openai_api_key", return_value="dummy"), \
             patch("reference_matcher.check_reference_match") as mock_check:
            r = run_batch("slug", force=False)
        mock_check.assert_not_called()
        assert r["counts"]["manual_matched"] == 1
