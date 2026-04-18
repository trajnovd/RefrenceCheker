# References Checker v10 — Self-Healing via Claude Agent

## Context

Across v1–v6 we've established a recurring debugging pattern: a user spots a problem with a reference (wrong PDF downloaded, ref not found, wrong claim verdict), reports it in chat, and the developer (1) inspects state, (2) traces the lookup chain, (3) identifies the bug, (4) writes a code fix, (5) writes a regression test, (6) heals the affected project state.

Every recent fix in this codebase followed that pattern — the SR 11-7 corporate-author bug, the SEC press release doc-id bug, the OUP fragile-publisher bug, the alphagpt2023 arXiv-abs URL bug. Each took ~10–30 minutes of focused work. Many of them are amenable to automation: the diagnostic steps are mechanical (read project.json, replay the lookup with logging, identify which step misbehaved), and the fixes follow stable patterns (add to a list, tighten a regex, normalize a URL, add a test).

**Goal:** turn this loop into an in-app feature. When a user sees a problematic reference, they click **Self-heal** in the UI. A Claude-powered agent receives the bug context, diagnoses the root cause, proposes (and optionally applies) a code change + a regression test, and re-runs the affected pipeline. The user reviews and accepts/rejects.

---

## 1. Design goals

1. **One-click trigger.** Self-heal lives wherever a problem can be observed: per-reference card, per-citation row, dashboard issue panel.
2. **Full context, no hand-typing.** The agent receives the bib entry, current result state, relevant logs, settings, and pointers to the repo. The user adds at most one sentence of intent.
3. **Code changes are reviewed.** No silent rewrites. Every patch is presented as a diff; the user explicitly approves before commit.
4. **Tests gate every fix.** A patch that breaks the existing suite cannot be auto-applied. The agent must write a regression test and prove it passes.
5. **Recoverable failure.** Every change is committed to a git branch (or worktree); if the heal makes things worse, `git revert` or branch-delete restores the prior state.
6. **Cost-bounded.** A per-session token / dollar cap prevents runaway agent loops.
7. **Developer-only feature.** Self-heal modifies the codebase. Gated behind a settings flag + (optionally) an authentication check. Disabled by default.

---

## 2. Three integration paths

The app needs to invoke a coding agent. Three viable approaches, with tradeoffs:

| Approach | Pros | Cons |
|---|---|---|
| **A. Claude Agent SDK** (in-process Python) | Tightest integration; programmatic tool use; structured outputs; we control all I/O. Cleanest UX. | New dep (`claude-agent-sdk`); requires API key; pricing per call. |
| **B. Claude Code CLI as subprocess** | Reuses existing CLI install; familiar developer ergonomics; rich tool ecosystem (file edit, bash, grep). | Couples app to CLI binary; harder to capture structured output; user-environment dependent. |
| **C. Anthropic API direct** (just messages.create) | Minimal dep (`anthropic` SDK); maximum flexibility; can be model-agnostic later. | We re-implement tool-use loops, file ops, sandboxing — duplicating the SDK's work. |

**Recommended: Path A (Claude Agent SDK)**. It's purpose-built for this — agentic loops with file/bash tools, structured I/O, and direct Anthropic API access.

We can ship a fallback to Path B (CLI subprocess) for users who already run Claude Code locally and don't want a separate API key. Settings selector: `"agent_mode": "sdk" | "cli" | "off"`.

---

## 3. Trigger surfaces

Self-heal entry points across the app:

### 3.1 Per-reference (Review view, right panel)

A new **🩺 Self-heal** button next to **Refresh** / **Set Link** / **Upload PDF** / **Paste Content**.

Click → opens a small modal:
- **Pre-filled context preview** — bib_key, current state summary, last 20 debug.log lines for this key
- **Problem description textbox** — pre-populated based on detected issue:
  - "Wrong PDF downloaded" if `files.pdf` exists but title mismatch heuristic flags it
  - "PDF not downloaded" if `pdf_url` set but `files.pdf` missing
  - "No reference content" if `files.md` missing
  - User can edit / append free-text
- **[Cancel]** / **[Diagnose]** / **[Diagnose + propose fix]** buttons

### 3.2 Per-citation (Verification Table)

Each row's actions strip gets a 🩺 button next to ↻ / →. Self-heal here scopes to **claim-check** problems:
- Wrong verdict (user marks the LLM verdict as wrong → agent investigates)
- Citation has no `.md` to check against → agent traces why (which is then a reference-level heal)

### 3.3 Dashboard issues panel

Each Issues row gets a 🩺 button. Same modal, scoped to the surfaced issue.

### 3.4 Free-form

A **"Investigate problem"** button on the dashboard that opens a textarea: the user describes a problem in their own words, optionally selecting one or more refs as context. Used for problems that don't fit a fixed UI surface ("the lookup is too slow", "manage rate-limits better", "add support for X").

---

## 4. Problem classification

The modal pre-classifies the issue from observable state. Each class maps to a starting prompt template the agent receives.

| Class | Trigger heuristic | Prompt template (excerpt) |
|---|---|---|
| **Wrong reference downloaded** | User-flagged; or LLM claim-check returns `not_supported` for multiple cites of the same ref | "The downloaded content for `<key>` doesn't match the bib entry. Bib title: `<title>`. Downloaded `.md` excerpt: `<first 500 chars>`. Diagnose why the wrong document was selected and propose a code fix in the lookup pipeline." |
| **Reference not downloaded** | `pdf_url` set + `files.pdf` missing, OR `pdf_url` empty + non-empty bib lookup attempted | "The reference `<key>` failed to download a PDF. Bib URL: `<url>`. Lookup chain trace: `<recent log lines>`. Diagnose and propose a fix." |
| **No content (.md missing)** | `files.md` empty | "No `.md` was built for `<key>`. Files on disk: `<list>`. Trace `_build_reference_md` and propose a fix." |
| **Wrong claim verdict** | User-flagged; verdict explanation seems incorrect | "Citation #<idx> for `<key>` got verdict `<v>` with explanation: `<expl>`. The user reports this is wrong. Inspect the prompt-construction code, the truncated reference content sent to the LLM, and propose either a prompt fix or a flag to bypass." |
| **Pipeline error** | Exception logged; result has `error` field | "Reference `<key>` raised: `<traceback>`. Diagnose and propose a fix." |
| **Free-form** | User typed | (verbatim user text + last debug.log tail + project state) |

---

## 5. Context bundle sent to the agent

Every heal session opens with a structured payload. The agent doesn't have to grep — the relevant context is preassembled.

```json
{
  "problem_class": "wrong_reference_downloaded",
  "user_description": "This downloads an Amazon page instead of the actual paper.",
  "reference": {
    "bib_key": "Hasbrouck2007EmpiricalMicrostructure",
    "raw_bib": "@book{Hasbrouck2007...,\n  title = {...},\n  ...\n}",
    "result": { ... full project_store result dict ... },
    "files_on_disk": [
      {"name": "..._page.html", "size": 46000, "first_kb": "..."}
    ]
  },
  "recent_logs": [
    "2026-04-18 10:23:01 INFO [Hasbrouck2007] GoogleSearch: OK url=amazon.com pdf=None",
    ...
  ],
  "repo_pointers": {
    "lookup_chain":   "lookup_engine.py::process_reference",
    "google_search":  "api_clients/google_search.py",
    "downloader":     "file_downloader.py",
    "tests":          "tests/test_google_search.py, tests/test_bib_url_download.py"
  },
  "test_command": "python -m pytest tests/test_set_link.py tests/test_claim_checker.py tests/test_bib_url_download.py tests/test_google_search.py",
  "settings": { ... non-secret settings.json contents ... },
  "ref_v_docs": ["ref_v3.md", "ref_v4.md", "ref_v5.md", "ref_v6.md"]
}
```

The agent's system prompt establishes the contract: "You are diagnosing and fixing a reference-checking app. Tools available: Read, Grep, Edit, Bash, Pytest. Always write a regression test for any fix. Never modify settings.json secrets."

---

## 6. Agent capabilities (tool list)

The agent has a curated set of tools — not the full filesystem.

| Tool | Allowed actions |
|---|---|
| `Read(path)` | Any file in the repo |
| `Grep(pattern, path?)` | Repo search |
| `Edit(path, old, new)` | Files in `lookup_engine.py`, `file_downloader.py`, `api_clients/`, `tests/`, `claim_checker.py`, `download_rules.py`. **Not** `app.py` routes (touchier), `config.py` defaults (touchier), or `static/` (UI). |
| `Bash(cmd)` | Allowlist: `python -m pytest …`, `python -c "from <module> import …"`, `git diff`, `git status`, `git log -p`. **Denylist**: `pip install`, `rm`, `git push`, `git reset --hard`, anything with shell-injection-prone characters. |
| `RunPipeline(bib_key)` | Re-runs `process_reference` for a single ref against the live config; returns result dict + log tail. |
| `ProposePatch(diff, justification, regression_test)` | Submits a candidate fix. Triggers test run + user-approval modal. |
| `HealDataOnly(action_spec)` | Heal project.json state without code changes (e.g., clear stale verdicts, drop bad files). For data-only problems. Logged but doesn't require code review. |

A boundary scope file (`.heal-allowed-paths`) makes the editable area explicit and version-controlled.

---

## 7. Workflow

```
[1] User clicks 🩺 Self-heal on a reference
[2] Modal opens with pre-classified problem + context preview
[3] User clicks [Diagnose]
[4] App opens an isolated git worktree at /heal-<session-id>/
[5] App spawns the agent with context bundle + tool list
[6] Agent works:
      - Reads relevant files
      - Runs pipeline / pytest in the worktree
      - Iterates
[7] Agent submits ProposePatch(diff, test, justification)
[8] App runs the test suite in the worktree
       → if FAIL: send result back to agent for another iteration (max N rounds)
       → if PASS: present diff to user
[9] User sees in the UI:
      - The diagnosed root cause (1–2 sentences)
      - The diff (rendered)
      - The new regression test
      - Test summary: "N tests passed, M new"
      - Cost so far: $0.43 (3 LLM calls)
      - [Cancel] / [Apply patch] / [Apply + commit]
[10] On Apply:
      - merge worktree → main
      - re-run the affected pipeline
      - verify the original problem is gone
      - close worktree
[11] Audit log entry written to project_store activity log
```

---

## 8. Safety controls

| Control | Why |
|---|---|
| **Disabled by default** in settings.json (`"self_heal": {"enabled": false}`) | New attack surface; opt-in only |
| **API key in env var only** (`ANTHROPIC_API_KEY`) | Same model as OPENAI_API_KEY today |
| **Git worktree isolation** | Agent's edits don't touch live tree until approval |
| **Path allowlist** for Edit tool | Agent can't touch settings.json, secrets, infra |
| **Bash command allowlist** | No `pip install`, no `rm -rf`, no `git push --force` |
| **Test-must-pass gate** | A failing-test patch is auto-rejected before user even sees it |
| **Diff size cap** | Reject patches > 500 changed lines (smells like a misdiagnosis) |
| **Per-session token cap** | Default 200k input / 50k output tokens (~$1.50). Configurable. |
| **Per-day total cap** | Default $20/day across all heal sessions. Hard stop. |
| **Audit log** | Every heal session writes a row: `{ts, bib_key, problem_class, outcome, tokens, $, diff_url}` to `project.json["heal_log"]`. |
| **Manual rollback** | "Undo last heal" button in dashboard → `git revert <commit>` |

---

## 9. Settings additions

```json
{
  "self_heal": {
    "enabled": false,
    "agent_mode": "sdk",                 // "sdk" | "cli" | "off"
    "model": "claude-sonnet-4-6",
    "max_iterations": 8,
    "max_session_tokens_in": 200000,
    "max_session_tokens_out": 50000,
    "max_session_usd": 1.50,
    "max_daily_usd": 20.00,
    "auto_apply_when_tests_pass": false,  // if true, skip the user-approval modal
    "git_branch_prefix": "heal/",
    "allowed_edit_paths": [
      "lookup_engine.py",
      "file_downloader.py",
      "claim_checker.py",
      "download_rules.py",
      "api_clients/",
      "tests/"
    ]
  }
}
```

API key stays env-var-only: `ANTHROPIC_API_KEY` (mirrors `OPENAI_API_KEY`).

---

## 10. Backend additions

### 10.1 New module: `self_heal.py`

```python
def open_session(slug, problem_class, context, user_description="") -> SessionId
def get_session_state(sid) -> {"status", "messages", "patches", "test_results", "cost"}
def respond_to_session(sid, user_input) -> ...   # for follow-up clarification
def apply_patch(sid, patch_id) -> {"ok", "commit_sha"}
def reject_patch(sid) -> ...
def cancel_session(sid) -> ...
def get_recent_sessions(slug, limit=20) -> [SessionSummary]
```

Uses the Claude Agent SDK under the hood. Manages git worktree creation/cleanup, tool dispatch, cost tracking.

### 10.2 New API routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/projects/<slug>/heal/start` | Open a session. Returns `session_id`. |
| `GET`  | `/api/projects/<slug>/heal/<sid>/stream` | SSE feed of agent activity (reads, tool calls, patches) |
| `POST` | `/api/projects/<slug>/heal/<sid>/respond` | User clarification mid-session |
| `POST` | `/api/projects/<slug>/heal/<sid>/apply` | Approve a patch — runs tests + merges |
| `POST` | `/api/projects/<slug>/heal/<sid>/cancel` | Tear down |
| `GET`  | `/api/projects/<slug>/heal/history` | Audit log entries |
| `POST` | `/api/projects/<slug>/heal/undo/<commit_sha>` | Revert a previously-applied heal |

---

## 11. Frontend additions

### 11.1 Components

- **HealButton** — small 🩺 button reusable in Review-view right panel, Verification Table rows, and Dashboard issues
- **HealModal** — main session UI:
  - Top: classified problem + context preview (collapsible)
  - Middle: SSE-driven activity log (reads, tool calls, partial agent reasoning)
  - Bottom: patch preview pane (diff renderer using something like `diff2html` or a simple inline `<pre>`)
  - Buttons: Apply / Reject / Continue / Cancel
- **HealHistoryPanel** — added to dashboard, shows recent heal sessions (success/failure, cost)

### 11.2 New JS state

```javascript
let healSession = null;       // { sid, sse, patches: [], cost: 0, ... }
let healHistory = [];         // recent sessions for dashboard
```

---

## 12. Implementation phases

### Phase A — Data-only heal (no code changes, no LLM)
~1 day. **Foundation, zero AI risk.**

- Add `HealDataOnly` action: cleans stale verdicts, drops bad files, restores from parsed_refs
- New `🩺` button in UI, but only triggers data healing
- Action library: `clear_setup_failures`, `restore_raw_bib_from_parsed_refs`, `drop_files_for_bib_key`, etc.
- These are the heals we've manually written ad-hoc throughout v1–v6 sessions, codified

**Result:** users can self-fix the most common stale-state problems without an LLM.

### Phase B — LLM diagnosis only (read-only)
~2 days. **First AI integration, no write access.**

- Add Anthropic SDK / Claude Agent SDK as dep
- Implement `open_session` with read-only tools (Read, Grep, Bash for tests)
- Agent diagnoses, returns text explanation only — no code edits
- UI shows the diagnosis; user manually fixes

**Result:** agent serves as a "first-line debugger" — explains problems, points at files. No risk to codebase.

### Phase C — LLM with patch proposals (no auto-apply)
~3 days. **Patches surfaced, never applied without explicit click.**

- Add Edit tool with path allowlist
- Worktree isolation
- Diff renderer in the modal
- "Apply" button runs tests then merges

**Result:** full self-healing loop; user reviews every change.

### Phase D — Auto-apply when tests pass
~1 day. **Optional convenience for trusted use cases.**

- Settings: `auto_apply_when_tests_pass: true`
- Skips the modal when the agent's patch passes the test gate
- Notification (toast) shows what was applied; full diff still in audit log

### Phase E — Background problem detection
~3 days. **Proactive healing.**

- Periodic scan (e.g., on dashboard load) for known patterns: refs with stale verdicts, refs with `pdf_url` set but `files.pdf` missing, refs failing the same way as in past heal sessions
- Each detected problem gets a "Self-heal this" button surfaced in dashboard's Issues panel
- Optional: run heals in batch overnight (with budget cap)

### Phase F — Heal-ledger learning
~1 week. Speculative.

- The `heal_log` becomes training data: when a similar problem recurs, the agent first searches past heals for an analogous case and proposes the same fix. Reduces token usage and time on repeat problems.

---

## 13. Files to create / modify

| File | Action | What changes |
|---|---|---|
| `self_heal.py` | NEW | Session manager, agent orchestration, cost tracking |
| `heal_actions.py` | NEW | Library of pre-built data-only heals (Phase A) |
| `app.py` | MODIFY | 7 new routes (start, stream, respond, apply, cancel, history, undo) |
| `config.py` | MODIFY | New `self_heal` settings block + `get_anthropic_api_key()` helper |
| `project_store.py` | MODIFY | `add_heal_log_entry()`, `get_heal_log()` |
| `templates/index.html` | MODIFY | Add HealButton instances, HealModal section, HealHistoryPanel |
| `static/js/app.js` | MODIFY | Heal session state, modal renderer, SSE handler, diff display |
| `static/css/style.css` | MODIFY | Heal button + modal + diff styles |
| `requirements.txt` | MODIFY | Add `claude-agent-sdk` (Phase B+) |
| `.heal-allowed-paths` | NEW | Allowlist of files the agent may Edit |
| `tests/test_self_heal.py` | NEW | Mock the SDK; test session lifecycle, gate logic, audit log |

---

## 14. Cost expectations

Rough per-heal cost using `claude-sonnet-4-6` (priced ~$3/M input, $15/M output as of writing):

| Phase | Typical token usage | Cost per heal |
|---|---|---|
| **Phase B** (read-only diagnosis) | 30k in / 2k out | ~$0.12 |
| **Phase C** (with patch) | 100k in / 8k out | ~$0.42 |
| **Phase D** (auto-apply) | 100k in / 8k out + 1 retry on test fail | ~$0.60 |

For a project with 100 refs and ~5% needing manual heal per session, that's ~$2-3 per project debug session — comparable to the claim-check costs we already accept.

The per-day cap ($20 default) covers ~30-50 heals per day.

---

## 15. Open questions

1. **SDK vs CLI vs API:** Path A (Agent SDK) is preferred but couples to Anthropic. Path B (CLI) is more portable. Path C (raw API) is most flexible but most work. Decide before Phase B.

2. **Auto-apply default:** safer to keep `auto_apply_when_tests_pass: false`. Power-users can opt in. But the friction of a modal might erode the "self-healing" value prop. Trade off ergonomics vs safety.

3. **Multi-user safety:** if this app is ever deployed for multiple users (not just the developer), self-heal MUST require admin auth. Currently single-user assumption. Consider how the spec changes for multi-tenant.

4. **Pattern memory (Phase F):** is past-heals retrieval worth the complexity? Embeds-and-search adds infra. Could start as: store `heal_log` entries with `problem_class` + brief diagnosis; on new heal, agent reads recent same-class entries before doing its own thing.

5. **Failure modes the agent shouldn't fix:** there are problems where the right answer is "manual intervention" (e.g., bib_key collision requires user judgment). The classifier should detect these and skip the agent path with a clear message.

6. **Patches across files:** most fixes touch a single file (add to a list, tighten a regex). But some (the doc_id work, the fragile-domain work) touched 2–3 files. The diff cap (500 lines) accommodates this; the user-review step keeps it sane. Watch for the agent over-refactoring "while I'm in here."

7. **Test runtime budget:** repeatedly running the full suite slows iteration. Should the agent be told which test files to run for which problem class? E.g., google_search problems → `test_google_search.py`, claim-check problems → `test_claim_checker.py`.

8. **Privacy of bib data:** the context bundle includes user bib entries. For projects with sensitive references (unpublished work), is sending content to a third-party LLM acceptable? Add a per-project `self_heal_allowed: false` opt-out for sensitive projects.

9. **Heal-log surfacing:** should the dashboard show recent heals automatically, or only on demand? Auto-surfacing builds confidence; on-demand reduces noise. Suggest auto with collapse.

10. **Reverting a bad heal:** if a heal lands and a new problem appears later traceable to it, the user should be able to find and revert. The audit log + commit-sha link make this possible. Need a UI for "browse heal history → revert."

---

## 16. What this is NOT

- **Not** an autonomous agent that runs without user trigger. Every heal starts with a user action.
- **Not** a deployment / production-issue response system. This heals codebase bugs, not infra.
- **Not** a replacement for tests written by hand. Self-heal complements test-driven development; it doesn't substitute for thinking about edge cases up front.
- **Not** suitable for closed-source distribution without further work. Self-heal is fundamentally an OSS / self-hosted tool — the user must own and trust the code being modified.
