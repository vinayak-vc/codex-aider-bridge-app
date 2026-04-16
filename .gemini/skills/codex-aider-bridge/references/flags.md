# CLI Flag Reference

## Setup Checks

Run these before any bridge invocation using `run_command`:

```powershell
# 1. Python version
python --version
# expect: Python 3.9.x or higher

# 2. Aider installed
aider --version
# expect: exits 0. If missing: pip install aider-chat

# 3. Ollama running + has a model
ollama list
# expect: exits 0 and shows at least one model name
# If not running: ollama serve (in a separate terminal)
# If no models: ollama pull qwen2.5-coder:7b

# 4. Bridge main.py exists (bridge_root = cwd)
Test-Path main.py
# bridge_root = cwd. Never navigate to a parent directory.
```

---

## Standard Invocation (use this 90% of the time)

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --plan-file "TASK_PLAN_active.json" `
  --manual-supervisor `
  --workflow-profile micro `
  --session-tokens <N>
```

Replace:
- `<REPO_ROOT>` — absolute path to the project being modified
- `<N>` — your token count estimate (helps the bridge report accurately; won't affect execution)

**Notes:**
- `bridge_root` is always `cwd`. Run `python main.py` directly — never use a hardcoded path.
- `TASK_PLAN_active.json` lives in cwd (bridge root).
- **Never add `--auto-approve`** — it bypasses all review. Use `--manual-supervisor` and write decision files instead.

---

## Situational Variants

### Working on this repo itself (bridge_root == REPO_ROOT)

```powershell
python main.py `
  --plan-file TASK_PLAN_active.json `
  --manual-supervisor `
  --workflow-profile micro `
  --session-tokens <N>
```

`--repo-root` defaults to cwd, so omit it when already in the bridge root.

### Resume an interrupted run

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --resume `
  --manual-supervisor
```

`--resume` reads the checkpoint from `<REPO_ROOT>/bridge_progress/checkpoint.json` and skips already-completed tasks.

### Dry run (inspect plan without executing)

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --plan-file "TASK_PLAN_active.json" `
  --dry-run
```

Use this to validate the plan JSON parses correctly before committing to a run.

### Specific Aider model

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --plan-file "TASK_PLAN_active.json" `
  --manual-supervisor `
  --workflow-profile micro `
  --aider-model ollama/qwen2.5-coder:14b
```

Common model values:
- `ollama/qwen2.5-coder:7b` — fast, good for simple single-file tasks
- `ollama/qwen2.5-coder:14b` — more capable, for complex multi-import tasks
- `ollama/deepseek-coder:6.7b` — fast alternative
- `ollama/mistral` — general fallback

**Prefer per-task `"model"` fields in the plan JSON** over a global `--aider-model`.

### Unity / large non-code projects (disable repo map)

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --plan-file "TASK_PLAN_active.json" `
  --manual-supervisor `
  --workflow-profile micro `
  --aider-no-map `
  --project-type unity
```

Use `--aider-no-map` when the repo has large non-code directories (Unity Library/, node_modules/).

### With validation command (CI gate after each task)

```powershell
python main.py `
  --repo-root "<REPO_ROOT>" `
  --plan-file "TASK_PLAN_active.json" `
  --manual-supervisor `
  --workflow-profile micro `
  --validation-command "python -m pytest tests/ -x -q"
```

---

## Flag Quick Reference

| Flag | Default | When to use |
|---|---|---|
| `--repo-root PATH` | cwd | Always set when target repo ≠ cwd |
| `--plan-file PATH` | none | Always — point to `TASK_PLAN_active.json` (cwd) |
| `--manual-supervisor` | off | **Always** — makes YOU the reviewer via file-based protocol |
| `--workflow-profile micro` | standard | **Always** — enforces one-file atomic tasks |
| `--auto-approve` | off | **NEVER** — bypasses all review |
| `--session-tokens N` | estimated | Set for accurate reporting |
| `--resume` | off | Only when resuming a crashed/interrupted run |
| `--dry-run` | off | Always run before first real invocation to validate plan JSON |
| `--aider-model MODEL` | Aider default | Global model override — prefer per-task `"model"` field |
| `--aider-no-map` | off | Unity/Unreal/large asset repos |
| `--project-type TYPE` | auto-detect | Set if auto-detect fails |
| `--validation-command CMD` | none | When you have a CI command to gate each task |
| `--max-task-retries N` | 10 | Reduce to 3 for faster failure on bad plans |
| `--auto-split-threshold N` | 0 | Use `3` for 7B models |
| `--model-lock` | off | Locks all tasks to the global `--aider-model` |
| `--task-timeout N` | 300 | Max seconds per Aider task |
| `--no-auto-commit` | off | Only if you want unstaged changes |
| `--skip-onboarding-scan` | off | Skip initial project knowledge scan |
| `--relay-session-id ID` | auto | Carry session ID across resumed runs |
| `--manual-review-poll-seconds N` | 5 | How often bridge polls for your decision file |
| `--idea-file PATH` | none | Inject architecture brief into planning |
| `--plan-output-file PATH` | none | Write generated plan JSON for inspection |

---

## Manual Supervisor Protocol

With `--manual-supervisor`, the bridge writes review requests and waits for decision files. **Run the bridge in the background** using `run_command` so you can write decisions in parallel.

### Step 1 — Start bridge in background

Use `run_command` with `WaitMsBeforeAsync: 500` to send the bridge to background.

### Step 2 — Poll for review requests

After each task completes, the bridge writes a request file:
```
<REPO_ROOT>/bridge_progress/manual_supervisor/requests/task_XXXX_request.json
```

Poll with `run_command`:
```powershell
Get-ChildItem "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/" -ErrorAction SilentlyContinue
```

Read the request with `view_file` to get `task_id`, `instruction`, `diff`, `execution`, `validation`, `files`.

### Step 3 — Write decision file

Decision path (same task ID, decisions subdirectory):
```
<REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_XXXX_decision.json
```

Use `write_to_file` to create the decision:

**PASS:**
```json
{ "decision": "pass" }
```

**REWORK:**
```json
{ "decision": "rework", "instruction": "specific actionable instruction" }
```

**SUBPLAN:**
```json
{
  "decision": "subplan",
  "sub_tasks": [
    { "instruction": "create X at src/x.ts", "files": ["src/x.ts"], "type": "create" },
    { "instruction": "import X in src/index.ts", "files": ["src/index.ts"], "type": "modify" }
  ]
}
```

The bridge polls the decisions directory every 2–5 seconds (configurable via `--manual-review-poll-seconds`).

---

## Retry Strategy (10 Attempts)

When a task fails validation or gets a REWORK decision, the bridge retries with escalating strategy:

| Attempt | Strategy |
|---|---|
| 1–3 | Standard instruction, same model |
| 4–6 | Simplified instruction + extra context files injected |
| 7 | Diagnostic rewrite — bridge rewrites the instruction based on the failure pattern |
| 8–9 | Diagnostic-informed retry with full error context |
| 10 | Supervisor takeover — bridge asks the supervisor LLM to generate a new instruction |

To reduce the retry budget: `--max-task-retries 3`

---

## Project Knowledge Files

These files persist between runs and improve plan quality:

| File | Location | Purpose |
|---|---|---|
| `project_knowledge.json` | `<REPO_ROOT>/bridge_progress/` | File roles, run history, learned facts |
| `checkpoint.json` | `<REPO_ROOT>/bridge_progress/` | Completed task IDs for resume |
| `task_metrics.json` | `<REPO_ROOT>/bridge_progress/` | Per-task status, timing, retry counts |
| `improvement_plan.json` | `<REPO_ROOT>/bridge_progress/` | Last executed plan (used by --resume) |
| `last_run.json` | `<REPO_ROOT>/bridge_progress/` | Summary of last run |

Read `project_knowledge.json` during Stage 2 planning to get context about the target project.
