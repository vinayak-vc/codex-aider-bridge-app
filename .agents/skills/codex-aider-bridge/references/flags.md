# CLI Flag Reference

## Setup Checks

Run these before any bridge invocation:

```bash
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
# If no models: ollama pull qwen2.5-coder:7b  (or user's preferred model)

# 4. Bridge main.py exists (bridge_root = cwd)
ls main.py
# bridge_root = cwd. Never navigate to a parent directory.
```

---

## Standard Invocation (use this 90% of the time)

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --session-tokens <N>
```

Replace:

- `<REPO_ROOT>` — absolute path to the project being modified
- `<N>` — your token count from `/cost` or estimate (helps the bridge report accurately; won't affect execution)

**Notes:**

- `bridge_root` is always `cwd`. Run `python main.py` directly — never use a hardcoded path.
- `TASK_PLAN_active.json` lives in cwd (bridge root).
- **Never add `--auto-approve`** — it bypasses all review. Use `--manual-supervisor` and write decision files instead.

---

## Situational Variants

### Working on this repo itself (bridge_root == REPO_ROOT)

```bash
python main.py \
  --plan-file TASK_PLAN_active.json \
  --manual-supervisor \
  --workflow-profile micro \
  --session-tokens <N>
```

`--repo-root` defaults to cwd, so omit it when already in the bridge root.

### Resume an interrupted run

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --resume \
  --manual-supervisor
```

`--resume` reads the checkpoint from `<REPO_ROOT>/bridge_progress/checkpoint.json` and skips already-completed tasks.

### Dry run (inspect plan without executing)

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --dry-run
```

Use this to validate the plan JSON parses correctly before committing to a run.

### Specific Aider model

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --aider-model ollama/qwen2.5-coder:14b
```

Common model values:

- `ollama/qwen2.5-coder:7b` — fast, good for simple single-file tasks
- `ollama/qwen2.5-coder:14b` — more capable, for complex multi-import tasks
- `ollama/deepseek-coder:6.7b` — fast alternative
- `ollama/mistral` — general fallback

**Prefer per-task `"model"` fields in the plan JSON** over a global `--aider-model`. Set the global only when the entire plan targets a single complexity tier.

### Unity / large non-code projects (disable repo map)

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --aider-no-map \
  --project-type unity
```

Use `--aider-no-map` when the repo has large non-code directories (Unity Library/, node_modules/) that cause Aider to hang during initial scan.

### With validation command (CI gate after each task)

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --validation-command "python -m pytest tests/ -x -q"
```

The validation command runs after each task. If it exits non-zero, the task is marked failed and retry logic kicks in.

---

## Flag Quick Reference


| Flag                             | Default       | When to use                                                                     |
| -------------------------------- | ------------- | ------------------------------------------------------------------------------- |
| `--repo-root PATH`               | cwd           | Always set when target repo ≠ cwd                                               |
| `--plan-file PATH`               | none          | Always — point to `TASK_PLAN_active.json` (cwd)                                 |
| `--manual-supervisor`            | off           | **Always** — makes YOU the reviewer via file-based protocol                     |
| `--workflow-profile micro`       | standard      | **Always** — enforces one-file atomic tasks                                     |
| `--auto-approve`                 | off           | **NEVER** — bypasses all review; defeats the manual supervisor protocol         |
| `--session-tokens N`             | estimated     | Set from `/cost` for accurate reporting                                         |
| `--resume`                       | off           | Only when resuming a crashed/interrupted run                                    |
| `--dry-run`                      | off           | Always run before first real invocation to validate plan JSON                   |
| `--aider-model MODEL`            | Aider default | Global model override — prefer per-task `"model"` field in plan JSON            |
| `--aider-no-map`                 | off           | Unity/Unreal/large asset repos or when Aider hangs on initial scan              |
| `--project-type TYPE`            | auto-detect   | Set if auto-detect fails or for faster startup                                  |
| `--validation-command CMD`       | none          | When you have a CI command to gate each task                                    |
| `--max-task-retries N`           | 10            | Reduce to 3 for faster failure on clearly bad plans                             |
| `--auto-split-threshold N`       | 0             | Use `3` for 7B models — splits tasks that touch >N files                        |
| `--model-lock`                   | off           | Locks all tasks to the global `--aider-model`; disables per-task routing        |
| `--task-timeout N`               | 300           | Max seconds per Aider task before it is killed and retried                      |
| `--no-auto-commit`               | off           | Only if you explicitly want unstaged changes (unusual)                          |
| `--skip-onboarding-scan`         | off           | Skip initial project knowledge scan (useful on very large repos)                |
| `--confirm-plan`                 | off           | Avoid — plan is already confirmed in Stage 2 before invocation                  |
| `--relay-session-id ID`          | auto          | Carry session ID across resumed runs for continuity                             |
| `--manual-review-poll-seconds N` | 5             | How often the bridge polls for your decision file                               |
| `--idea-file PATH`               | none          | Inject an architecture brief / product spec into planning (max 2000 chars used) |
| `--plan-output-file PATH`        | none          | Write the generated plan JSON to a file for inspection before execution         |


---

## Manual Supervisor Protocol

With `--manual-supervisor`, the bridge writes review requests and waits for decision files. **Run the bridge in the background** so you can write decisions without a separate terminal.

### Step 1 — Start bridge in background

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --session-tokens <N> &
```

### Step 2 — Poll for review requests

After each task completes, the bridge writes a request file:

```
<REPO_ROOT>/bridge_progress/manual_supervisor/requests/task_XXXX_request.json
```

Where `XXXX` is the zero-padded task ID (e.g. `task_0001_request.json`). With `--relay-session-id`, includes session suffix: `task_0001_<session_id>_request.json`.

Poll:

```bash
ls "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/"
```

Read the request to get `task_id`, `instruction`, `diff`, `execution`, `validation`, `files`.

### Step 3 — Write decision file

Decision path (same task ID, decisions subdirectory):

```
<REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_XXXX_decision.json
```

**PASS:**

```json
{ "decision": "pass" }
```

**REWORK:**

```json
{ "decision": "rework", "instruction": "specific actionable instruction — what exactly is wrong and what to do instead" }
```

**SUBPLAN** (break task into sub-tasks):

```json
{
  "decision": "subplan",
  "sub_tasks": [
    { "instruction": "create X at src/x.ts", "files": ["src/x.ts"], "type": "create" },
    { "instruction": "import X in src/index.ts", "files": ["src/index.ts"], "type": "modify" }
  ]
}
```

The bridge polls the decisions directory every 2 seconds (configurable via `--manual-review-poll-seconds`).

---

## Retry Strategy (10 Attempts)

When a task fails validation or gets a REWORK decision, the bridge retries with escalating strategy:


| Attempt | Strategy                                                                           |
| ------- | ---------------------------------------------------------------------------------- |
| 1–3     | Standard instruction, same model                                                   |
| 4–6     | Simplified instruction + extra context files injected                              |
| 7       | Diagnostic rewrite — bridge rewrites the instruction based on the failure pattern  |
| 8–9     | Diagnostic-informed retry with full error context                                  |
| 10      | Supervisor takeover — bridge asks the supervisor LLM to generate a new instruction |


If all 10 attempts fail → task is marked as permanently failed, bridge moves to next task (or halts if `--halt-on-failure` is set).

To reduce the retry budget for faster feedback on bad plans: `--max-task-retries 3`

---

## Project Knowledge Files

These files persist between runs and improve plan quality:


| File                     | Location                       | Purpose                                         |
| ------------------------ | ------------------------------ | ----------------------------------------------- |
| `project_knowledge.json` | `<REPO_ROOT>/bridge_progress/` | File roles, run history, learned facts          |
| `checkpoint.json`        | `<REPO_ROOT>/bridge_progress/` | Completed task IDs for resume                   |
| `task_metrics.json`      | `<REPO_ROOT>/bridge_progress/` | Per-task status, timing, retry counts           |
| `improvement_plan.json`  | `<REPO_ROOT>/bridge_progress/` | Last executed plan (used by --resume)           |
| `last_run.json`          | `<REPO_ROOT>/bridge_progress/` | Summary of last run: status, counts, model used |


Read `project_knowledge.json` during Stage 2 planning to get context about the target project before producing your task plan.