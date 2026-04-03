# WORK LOG — Codex-Aider Bridge App
> Branch: `chatbot_llm`
> Tracks all work in sequence: who did what, what's done, what's missing.

---

## LEGEND
- ✅ Done
- ⚠️ Partial
- ❌ Missing
- 🔵 Claude (Supervisor / Architect)
- 🟡 Aider (Developer / Executor)

---

## WORKFLOW AUDIT (2026-03-25)
**Done by:** 🔵 Claude — codebase scan + audit against intended workflow

### Intended Workflow Being Built:
```
User Goal
  → [1] Agentic AI reads requirements → creates sequential JSON plan
  → [2] Bridge passes plan to Aider step by step
  → [3] Aider executes each step → reports back to bridge
  → [4] Agentic AI reviews git diff → checks correctness per step
  → [5] If issue found → Agentic AI creates SUB-PLAN for that step
  → [6] Agentic AI = Technical Supervisor only (no code writing)
  → [7] Aider = Developer only (executes instructions)
```

---

## AUDIT RESULTS

| # | Workflow Step | Status | Implemented In | Notes |
|---|---|---|---|---|
| 1 | Goal → Sequential JSON Plan | ✅ YES | `supervisor/agent.py:37-94`, `main.py:119-156` | Schema validated, retries with feedback |
| 2 | Bridge passes plan to Aider step by step | ✅ YES | `main.py:323-327`, `executor/aider_runner.py:33-74` | Sequential loop, scoped files, atomic instructions |
| 3 | Aider executes & reports back | ✅ YES | `executor/aider_runner.py:48-74`, `executor/diff_collector.py:19-47` | Exit code + stdout/stderr + git diff captured |
| 4 | AI reviews diff & checks correctness | ⚠️ PARTIAL | `supervisor/agent.py:48-113`, `validator/validator.py:35-54` | Supervisor PASS/REWORK works — but no structured diff analysis |
| 5 | Create sub-plan for issues | ❌ MISSING | N/A | **NOT IMPLEMENTED** — brute-force retry only |
| 6 | AI as Technical Supervisor only | ✅ YES | `supervisor/agent.py` (entire module) | Strict role enforced via prompts + subprocess isolation |
| 7 | Aider as Developer only | ✅ YES | `executor/aider_runner.py:12-19` | Scoped files, atomic instruction, no deviation |

---

## WHAT'S WORKING (Confirmed)

### ✅ Plan Generation (Step 1)
- `SupervisorAgent.generate_plan()` builds a detailed sequential JSON plan
- Schema requires: `id`, `files[]`, `instruction`, `type` per task
- Invalid JSON retried with error feedback to supervisor
- Repo tree injected into planning prompt for context

### ✅ Sequential Execution (Step 2)
- Main loop in `main.py` iterates one task at a time
- Each task gets only its own files (`--file` flags in Aider command)
- Atomic instruction passed as single `--message`

### ✅ Aider Reports Back (Step 3)
- `subprocess.run()` captures exit code, stdout, stderr
- `DiffCollector` runs `git diff HEAD` immediately after each task
- Diff capped at 4000 chars, passed to supervisor for review

### ✅ Supervisor Review (Step 4 — partial)
- `SupervisorAgent.review_task(TaskReport)` sends diff to supervisor
- Supervisor returns exactly `PASS` or `REWORK: <new instruction>`
- On REWORK: new instruction looped back to Aider
- Mechanical pre-check (file existence, Python syntax) runs BEFORE supervisor (saves tokens)

### ✅ Role Separation (Steps 6 & 7)
- Supervisor: planning + review only (no code, no file writes)
- Aider: executes only what it's told, scoped to specific files
- Enforced by prompt design + subprocess isolation

---

## WHAT'S MISSING

### ❌ Sub-Plan Generation (Step 5) — CRITICAL GAP

**Problem:**
When Aider's output fails mechanical validation (e.g. syntax error, missing file), the bridge just retries the **same instruction** up to `max_task_retries` times.

**Current code (main.py ~line 229-235):**
```python
if not validation_result.succeeded:
    if attempt >= config.max_task_retries:
        raise RuntimeError(...)
    continue  # ← brute retry, no intelligence
```

**What should happen:**
```python
# MISSING: supervisor.generate_subplan(task, error) → list[Task]
# MISSING: inject corrective sub-tasks into execution queue
# MISSING: re-attempt main task after sub-tasks succeed
```

**Impact:**
- Mechanical errors not fixed intelligently
- All retries use same instruction (Aider keeps making same mistake)
- User must manually intervene after retries exhaust

---

### ⚠️ Diff Analysis Not Structured (Step 4 — partial gap)
- Supervisor receives raw diff text (4000 char max)
- No structured parsing: files changed, lines added/removed, intent vs actual
- Supervisor review is qualitative only, no deep verification

---

### ⚠️ No Task Hierarchy Tracking
- No way to mark "Task 3b is a sub-task of Task 3"
- No resume-from-task functionality
- Failure on any task aborts entire run

---

## WORK SEQUENCE — PLANNED

| # | Task | Owner | Status | Notes |
|---|---|---|---|---|
| W-01 | Audit existing codebase against workflow | 🔵 Claude | ✅ Done 2026-03-25 | This document |
| W-02 | Implement `SupervisorAgent.generate_subplan()` | TBD | ❌ Pending | Core missing piece |
| W-03 | Add sub-task queue to main execution loop | TBD | ❌ Pending | Depends on W-02 |
| W-04 | Add task hierarchy tracking (task → sub-tasks) | TBD | ❌ Pending | Depends on W-03 |
| W-05 | Send mechanical errors to supervisor on 2nd retry | TBD | ❌ Pending | Depends on W-02 |
| W-06 | Structured diff parsing for supervisor context | TBD | ❌ Pending | Enhancement |
| W-07 | Resume-from-task-N support | TBD | ❌ Pending | Enhancement |
| W-08 | UI: Show sub-task cards in live run view | TBD | ❌ Pending | Depends on W-03 |

---

## FILES REFERENCE MAP

| Component | File | Key Lines |
|---|---|---|
| Plan generation | `supervisor/agent.py` | 37-94 |
| Plan schema | `supervisor/agent.py` | 250-278 |
| Plan parsing + validation | `parser/task_parser.py` | 29-131 |
| Main orchestration loop | `main.py` | 182-265, 323-327 |
| Aider execution | `executor/aider_runner.py` | 33-74 |
| Diff collection | `executor/diff_collector.py` | 19-47 |
| Mechanical validation | `validator/validator.py` | 35-139 |
| Supervisor review | `supervisor/agent.py` | 48-147 |
| Task retry logic | `main.py` | 182-265 |
| Web UI server | `ui/app.py` | all |
| SSE event streaming | `ui/bridge_runner.py` | 112-267 |
| Data models | `models/task.py` | all |

---

## SESSION LOG

| Date | Who | Action |
|---|---|---|
| 2026-03-25 | 🔵 Claude | Branch `chatbot_llm` created from `main` |
| 2026-03-25 | 🔵 Claude | Full codebase scan completed |
| 2026-03-25 | 🔵 Claude | Workflow audit completed — 6/7 steps working, sub-plan missing |
| 2026-03-25 | 🔵 Claude | `WORK_LOG.md` created |
| 2026-03-27 | 🔵 Codex | Read onboarding, work log, and bridge progress for supervisor workflow |
| 2026-03-27 | 🔵 Codex | Read target project brief and file tree for `D:\AI_Project\valorant-clip-data-extractor-v2` |
| 2026-03-27 | 🔵 Codex | Wrote `plan_001_core_pipeline.json` and attempted bridge run |
| 2026-03-27 | 🔵 Codex | Diagnosed bridge blockers: missing `aider` on PATH, UTF-8 console issue, required `must_exist` assertions |
| 2026-03-27 | 🔵 Codex | Detected Aider model mismatch: `ollama/qwen2.5-coder:14b` not installed; local model is `qwen2.5-coder:7b` |
| 2026-03-27 | 🔵 Codex | Wrote recovery plan `plan_002_config_bootstrap.json` and relaunched bridge with `qwen2.5-coder:7b` |
| 2026-03-27 | 🔵 Codex | Reviewed task 1 result for `config.py` and issued `REWORK` decision via manual supervisor JSON |
| 2026-03-27 | 🔵 Codex | Reviewed second `config.py` result and issued final targeted `REWORK` decision |
| 2026-03-27 | 🔴 Bridge | Crash after writing manual review request due stdout emit `OSError: [Errno 22] Invalid argument` |
| 2026-03-27 | 🔵 Codex | Reviewed final `config.py` result, recorded `PASS`, and re-ran recovery plan successfully |
| 2026-03-27 | 🔴 Bridge | Recovery plan `plan_002_config_bootstrap.json` completed successfully with `qwen2.5-coder:7b` |
| 2026-03-29 | 🔵 Codex | Read onboarding & generated `plan_001_cinematic_shorts.json` for Valorant project v3 |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_001_config` and issued `PASS` via decision JSON |
| 2026-03-29 | 🟡 Aider | Started execution of `task_002_utils_grouping` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_002_utils_grouping` and issued `PASS` via decision JSON |
| 2026-03-29 | 🟡 Aider | Started execution of `task_003_main_grouping` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_003_main_grouping` (attempt 1) and issued `REWORK` via decision JSON |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_003_main_grouping` (attempt 2) and issued `PASS` via decision JSON |
| 2026-03-29 | 🟡 Aider | Started execution of `task_004_video_editor_signature` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_004_video_editor_signature` and issued `PASS` via decision JSON |
| 2026-03-29 | 🟡 Aider | Started execution of `task_005_video_editor_shake_flash` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_005_video_editor_shake_flash` and issued `PASS` via decision JSON |
| 2026-03-29 | 🟡 Aider | Started execution of `task_006_video_editor_replay` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_006_video_editor_replay` (attempt 1) and issued `REWORK` via decision JSON |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_006_video_editor_replay` (attempt 2) and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Task plan `plan_001_cinematic_shorts.json` executed completely and successfully! |
| 2026-03-29 | 🔵 Codex | Detected `IndexError` bug caused by `clip.iter_frames()` hallucination in Task 4 |
| 2026-03-29 | 🔵 Codex | Generated `plan_002_fix_bug.json` to repair `_build_clip_segment` signature |
| 2026-03-29 | 🟡 Aider | Started execution of `task_100_fix_build_clip_segment` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_100_fix_build_clip_segment` and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Detected `TypeError` during zoom scale calculation due to a list replacing a float parameter |
| 2026-03-29 | 🔵 Codex | Generated `plan_003_fix_zoom.json` to properly map lists of float timestamps inside `_calculate_zoom_scale` |
| 2026-03-29 | 🟡 Aider | Started execution of `task_200_fix_zoom_scale_typeerror` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_200_fix_zoom_scale_typeerror` and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Bug fix sequence `plan_003_fix_zoom.json` executed successfully |
| 2026-03-29 | 🔵 Codex | Detected `AttributeError` for deprecated `vfx.speedx` removed in moviepy 2.0 |
| 2026-03-29 | 🔵 Codex | Generated `plan_004_fix_moviepy.json` to update speedx to MultiplySpeed |
| 2026-03-29 | 🟡 Aider | Started execution of `task_300_update_moviepy_speedx` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_300_update_moviepy_speedx` and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Bug fix sequence `plan_004_fix_moviepy.json` executed successfully |
| 2026-03-29 | 🔵 Codex | User requested 1 input = 1 output exactly. Designed `plan_005_single_clip.json` to concatenate kill groups into a single montage highlight |
| 2026-03-29 | 🟡 Aider | Started execution of `task_400_concat_kill_groups` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_400_concat_kill_groups` and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Feature `plan_005_single_clip.json` executed successfully |
| 2026-03-29 | 🔵 Codex | Caught `NameError`. Aider accidentally removed the `_append_loop_outro` definition while refactoring the loop! |
| 2026-03-29 | 🔵 Codex | Designed `plan_006_restore_outro.json` to restore the missing method. |
| 2026-03-29 | 🟡 Aider | Started execution of `task_500_restore_outro_function` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_500_restore_outro_function` and issued `PASS` via decision JSON |
| 2026-03-29 | 🔵 Codex | Bug fix sequence `plan_006_restore_outro.json` executed successfully |
| 2026-03-29 | 🔵 Codex | Identified severe rendering bottleneck: `method='compose'` in concat + per-frame `min()` + per-frame `np.random.randint` in shake loop |
| 2026-03-29 | 🔵 Codex | Designed `plan_007_performance.json` to fix all three bottlenecks |
| 2026-03-29 | 🟡 Aider | Started execution of `task_600_performance_fix` |
| 2026-03-29 | 🔵 Codex | Reviewed result of `task_600_performance_fix` and issued `PASS` via decision JSON. All 3 optimizations confirmed in diff |
