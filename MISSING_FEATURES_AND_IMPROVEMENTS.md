# Missing Features & Improvements — Codex-Aider Bridge App
> Audit Date: 2026-03-25 | Branch: chatbot_llm | Done by: 🔵 Claude (Technical Supervisor)

---

## SEVERITY LEGEND
- 🔴 CRITICAL — Can crash or freeze the bridge
- 🟠 HIGH — Breaks workflow, data loss risk
- 🟡 MEDIUM — Degrades reliability or usability
- 🟢 LOW — Nice to have, polish

---

## 1. CRITICAL ISSUES

### 🔴 C-01 — No Timeout on Subprocess Calls
**Files:** `executor/aider_runner.py:49-56`, `supervisor/agent.py:187-204`
**Problem:** `subprocess.run()` has no `timeout=` parameter. A hung Ollama instance, frozen network, or stuck process blocks the entire bridge indefinitely.
**Impact:** UI freezes. No user recourse except killing the whole process.
**Fix:**
```python
subprocess.run(cmd, timeout=config.task_timeout_seconds)  # default: 300
```

---

### 🔴 C-02 — Shell Injection via Validation Command
**File:** `validator/validator.py:129`
**Problem:** User-supplied `--validation-command` runs with `shell=True` without sanitization.
**Impact:** `pytest; rm -rf .` would silently delete the repo.
**Fix:**
```python
import shlex
cmd = shlex.split(validation_command)
subprocess.run(cmd, shell=False)
```

---

### 🔴 C-03 — Race Condition in Process Management
**File:** `ui/bridge_runner.py:92-110`
**Problem:** `is_running` flag is set outside the thread lock. If `stop()` is called immediately after `start()`, `_process` may not exist yet.
**Impact:** UI shows "running" when process never started; stop button crashes.
**Fix:** Move `thread.start()` inside the lock or use a threading Event.

---

### 🔴 C-04 — No Path Traversal Validation
**File:** `context/file_selector.py:17-23`
**Problem:** File paths are resolved but not checked for `../` escape attempts.
**Impact:** Supervisor could specify `../../../etc/passwd` and Aider would try to modify it.
**Fix:**
```python
if not resolved.resolve().is_relative_to(repo_root):
    raise ValueError(f"Path escapes repo root: {fp}")
```

---

### 🔴 C-05 — No Plan Checkpoint / Resume
**File:** `main.py` (global)
**Problem:** If bridge crashes mid-run (task 7 of 29), next run restarts from task 1.
**Impact:** All completed work is ignored; risk of duplicate changes; wasted tokens.
**Fix:** Save completed task IDs to `.bridge_checkpoint.json` after each PASS. On start, skip already-completed tasks.

---

### 🔴 C-06 — Duplicate History Entry IDs
**File:** `ui/state_store.py:76-82`
**Problem:** Entry IDs use `int(time.time() * 1000)`. Two runs starting within the same millisecond silently overwrite each other.
**Fix:** Use `uuid.uuid4()` instead of timestamp.

---

## 2. MISSING FEATURES

### 🟠 F-01 — No Sub-Plan Generation for Mechanical Failures
**File:** `main.py:229-235`
**Problem:** Mechanical validation failure just retries with the same instruction up to `max_task_retries` times. No intelligence.
**What's needed:**
```python
# Does NOT exist yet:
sub_tasks = supervisor.generate_subplan(task, mechanical_error)
# → inject sub_tasks into execution queue before re-attempting main task
```
**Impact:** Aider keeps making the same mistake; all retries fail; user must intervene manually.

---

### 🟠 F-02 — No Rollback on Failure
**File:** `main.py` (global)
**Problem:** If task 15 of 29 breaks the codebase and all retries fail, the repo is left in a broken state.
**Fix:** Create a backup branch before plan execution:
```bash
git checkout -b bridge/backup-{timestamp}
git checkout -
```
On complete failure: `git checkout bridge/backup-{timestamp}`.

---

### 🟠 F-03 — No Pre-flight Checks
**File:** `main.py` (start of main())
**Problem:** Bridge plans for 5 minutes, then fails immediately at task 1 because Aider isn't installed.
**Checks missing:**
- `which aider` — Aider installed?
- `git rev-parse --git-dir` — Is repo a git repo?
- Supervisor API key present?
- Disk space > 100MB?
**Fix:** Run all pre-flight checks before `obtain_plan()`.

---

### 🟠 F-04 — No Cost / Token Tracking
**Problem:** No accounting for Claude/Codex API calls, token counts, or elapsed time per task.
**Impact:** User is blind on cost. Can't optimize expensive tasks.
**Fix needed:** New module `metrics/tracker.py`:
```python
class RunMetrics:
    supervisor_calls: int
    supervisor_tokens_est: int
    aider_tasks: int
    elapsed_seconds: float
    rework_count: int
```

---

### 🟡 F-05 — No Task Dependency Graph
**Problem:** If Task 2 depends on Task 1 output but Task 1 failed, Task 2 still runs and produces garbage.
**Fix:** Plan schema should support optional `depends_on: ["T-001"]` field. Bridge skips dependents if prerequisite fails.

---

### 🟡 F-06 — No Pre/Post Task Hooks
**Problem:** No way to run setup commands before a plan (e.g., `npm install`) or cleanup after (e.g., `git add -p`).
**Fix:** Add `hooks` section to plan schema:
```json
{
  "hooks": {
    "before_plan": ["npm install"],
    "after_task": ["black ."],
    "after_plan": ["pytest"]
  }
}
```

---

### 🟡 F-07 — No Multi-Language Validation
**File:** `validator/validator.py`
**Problem:** Only validates Python syntax (compileall). No support for TypeScript, Go, Rust, Java, etc.
**Fix:** Pluggable validator per file extension:
```python
validators = {
    ".py": PythonValidator,
    ".ts": TypeScriptValidator,
    ".go": GoValidator,
}
```

---

### 🟡 F-08 — No Parallel Task Execution
**Problem:** All 29 tasks run sequentially even when tasks are independent.
**Impact:** A 29-task plan that could be parallelised takes 3x longer.
**Fix:** Parse dependency graph; run independent tasks in parallel using `ThreadPoolExecutor`.

---

## 3. CODE QUALITY ISSUES

### 🟡 Q-01 — Diff Truncation Cuts Mid-Hunk
**File:** `executor/diff_collector.py:43-45`
**Problem:** Diff is truncated at exactly 4,000 chars which may cut in the middle of a `@@ hunk @@` block.
**Impact:** Supervisor sees malformed diff context and may misread what changed.
**Fix:** Truncate at line boundary; log how many lines were omitted.

---

### 🟡 Q-02 — No Attempt Metadata in ExecutionResult
**File:** `models/task.py:24-30`
**Problem:** `ExecutionResult` doesn't track attempt number or elapsed time.
**Impact:** Logs say "attempt 2/3" but can't correlate to specific ExecutionResult for analysis.
**Fix:** Add fields:
```python
attempt_number: int = 1
duration_seconds: float = 0.0
```

---

### 🟡 Q-03 — Empty Instruction Not Caught Before Strip
**File:** `parser/task_parser.py:119`
**Problem:** `instruction.strip()` is validated but a whitespace-only string passes the initial check.
**Fix:**
```python
if not instruction or not instruction.strip():
    raise PlanParseError("instruction cannot be empty or whitespace")
```

---

### 🟡 Q-04 — `..` Path Traversal Not Checked in Parser
**File:** `parser/task_parser.py:106-117`
**Problem:** Checks for absolute paths and Windows drive letters but not `..` components.
**Fix:**
```python
from pathlib import Path
if ".." in Path(fp).parts:
    raise PlanParseError(f"path traversal not allowed: {fp}")
```

---

### 🟡 Q-05 — No Exponential Backoff on Retries
**File:** `main.py:133-156`
**Problem:** Rapid retries hit the supervisor service without delay.
**Fix:**
```python
import time
wait = min(2 ** attempt, 30)  # cap at 30 seconds
time.sleep(wait)
```

---

### 🟡 Q-06 — Supervisor Prompt Chars Not Sanitised
**File:** `supervisor/agent.py:225-228`
**Problem:** Prompt string injected into command without sanitization.
**Fix:** Use subprocess list args (not string concat) so shell never interprets the prompt content.

---

### 🟢 Q-07 — All Hardcoded Values Should Be Config
| File | Line | Value | Make Configurable |
|---|---|---|---|
| `diff_collector.py` | 14 | `_MAX_CHARS = 4000` | `config.diff_max_chars` |
| `repo_scanner.py` | 24 | `max_depth = 4` | `--repo-depth` CLI arg |
| `repo_scanner.py` | 24 | `max_entries = 100` | `--repo-max-entries` |
| `launch_ui.py` | 52 | `PORT = 7823` | `--port` or env var |
| `launch_ui.py` | 53 | `HOST = "127.0.0.1"` | `--host` or env var |
| `state_store.py` | 16 | `MAX_HISTORY = 50` | `config.max_history` |
| `supervisor/agent.py` | 67 | `idea_text[:2000]` | `config.idea_max_chars` |

---

## 4. MISSING ERROR HANDLING

### 🟠 E-01 — Git Not Initialized Check
**File:** `executor/diff_collector.py:26-31`
**Problem:** Falls back silently if `git diff HEAD` fails. Never checks if repo is a git repo at all.
**Fix:** Run `git rev-parse --git-dir` first; return specific error if not a git repo.

---

### 🟠 E-02 — Aider Not Installed
**File:** `executor/aider_runner.py:49-56`
**Problem:** OSError on missing binary returns exit code `-1` with buried error message.
**Fix:** In `main.py`, run pre-flight: `shutil.which("aider")` before plan generation.

---

### 🟡 E-03 — File Not Writable
**File:** `context/file_selector.py:17-23`
**Problem:** Checks existence but not write permission.
**Fix:**
```python
if not os.access(path, os.W_OK):
    raise PermissionError(f"File not writable: {path}")
```

---

### 🟡 E-04 — UTF-8 Encoding Errors in Supervisor Output
**File:** `supervisor/agent.py:207-209`
**Problem:** Reads supervisor output file without error handling.
**Fix:**
```python
output_file.read_text(encoding='utf-8', errors='replace')
```

---

### 🟡 E-05 — Disk Space Not Checked
**Problem:** No check for available disk space before running Aider.
**Impact:** Silent mid-task failure if disk is full.
**Fix:** `shutil.disk_usage(repo_root).free > MIN_FREE_BYTES` check in pre-flight.

---

## 5. MISSING LOGGING

| # | File | What's Missing | Priority |
|---|---|---|---|
| L-01 | `supervisor/agent.py:37-52` | Supervisor prompts not logged (can't debug misunderstood instructions) | 🟠 |
| L-02 | `diff_collector.py:43-45` | No log when diff is truncated (supervisor has no context) | 🟡 |
| L-03 | `file_selector.py:17-23` | Resolved paths not logged (can't debug wrong file selection) | 🟡 |
| L-04 | `aider_runner.py:76-99` | Full command with `--file` list not logged | 🟡 |
| L-05 | `validator/validator.py:35-54` | Validation step timing not logged | 🟡 |
| L-06 | `ui/app.py:212-237` | SSE client join/leave not logged | 🟢 |

---

## 6. NEW MODULES TO BUILD

| Module | Purpose | Priority |
|---|---|---|
| `config.py` | Centralized typed config class; replace all hardcoded values | 🔴 |
| `executor/timeout_handler.py` | Decorator for subprocess with timeout + process kill | 🔴 |
| `security/path_validator.py` | Central file path security: traversal, symlinks, absolute | 🔴 |
| `metrics/tracker.py` | Track token counts, call durations, rework rates per run | 🟠 |
| `utils/retry_handler.py` | Exponential backoff with jitter for all retry loops | 🟠 |
| `models/plan.py` | Rich Plan object with task dependencies, critical path | 🟠 |
| `integrations/github_client.py` | Auto-create PRs, link commits to runs | 🟡 |
| `integrations/slack_notifier.py` | Send run start/complete/fail notifications | 🟡 |
| `tests/integration_tests.py` | End-to-end tests with mock supervisor + mock aider | 🟠 |

---

## 7. UI / UX GAPS

| # | Gap | Impact | Fix |
|---|---|---|---|
| U-01 | No live diff preview | User can't see what Aider changed in real-time | Stream diffs to UI via SSE |
| U-02 | No dry-run preview in UI | User can't review plan before committing | Add plan preview screen before run |
| U-03 | No manual pause/approve per task | User can't intervene mid-run | Add "pause and inspect" button |
| U-04 | No progress bar | User doesn't know how far along the plan is | `tasks_done / total_tasks * 100` |
| U-05 | No search/filter on history | Hard to find old runs | Filter by goal, date, status |
| U-06 | Error messages truncated | Can't see full stderr in UI | Expandable error detail pane |
| U-07 | No copy-to-clipboard | Must manually copy commands | Add copy button on log lines |
| U-08 | No model performance stats | Can't compare models | Track avg time/task by model |

---

## 8. FUTURE INTEGRATIONS

| Integration | Why | Effort |
|---|---|---|
| **GitHub** | Auto-open PRs, link commits, add reviewers | Medium |
| **Slack** | Notify on run complete/fail; pause from Slack | Medium |
| **Cost tracking** | Track Claude/Codex API spend per run | Low |
| **SQLite run history** | Replace flat JSON files; enable queries + stats | Medium |
| **OAuth for supervisors** | UI-based auth for Claude CLI, Codex, etc. | High |
| **Webhooks** | Trigger bridge runs from CI events | Medium |
| **Interactive plan editor** | Edit/reorder/skip tasks before running | High |

---

## IMMEDIATE ACTION ITEMS (Priority Order)

| Priority | Task | File | Est. Time |
|---|---|---|---|
| 1 | Add subprocess timeouts (C-01) | `aider_runner.py`, `agent.py` | 30 min |
| 2 | Fix shell injection in validator (C-02) | `validator/validator.py` | 15 min |
| 3 | Add path traversal check (C-04) | `file_selector.py`, `task_parser.py` | 30 min |
| 4 | Add pre-flight checks (F-03) | `main.py` | 45 min |
| 5 | Add plan checkpoint/resume (C-05) | `main.py` | 2 hours |
| 6 | Implement sub-plan generation (F-01) | `supervisor/agent.py`, `main.py` | 3 hours |
| 7 | Centralize config (Q-07) | New `config.py` | 1.5 hours |
| 8 | Add exponential backoff (Q-05) | `main.py` | 45 min |
| 9 | Add rollback on failure (F-02) | `main.py` | 1 hour |
| 10 | Add cost/token tracking (F-04) | New `metrics/tracker.py` | 2 hours |

---

*Total identified issues: 39 | Critical: 6 | High: 8 | Medium: 17 | Low: 5*
*Estimated effort to fix all Critical + High: ~12 hours of Aider developer time*
