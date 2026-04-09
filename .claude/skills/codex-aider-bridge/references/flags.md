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

# 4. Bridge main.py exists
ls <bridge_root>/main.py
# bridge_root = root of codex-aider-bridge-app repo
```

---

## Standard Invocation (use this 90% of the time)

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --session-tokens <N>
```

Replace:
- `<bridge_root>` — absolute path to `codex-aider-bridge-app/`
- `<REPO_ROOT>` — absolute path to the project being modified
- `<N>` — your token count from `/cost` or estimate (helps the bridge report accurately; won't affect execution)

---

## Situational Variants

### Working on this repo itself (bridge_root == REPO_ROOT)

```bash
python main.py \
  --plan-file TASK_PLAN_active.json \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --session-tokens <N>
```

`--repo-root` defaults to cwd, so omit it when already in the bridge root.

### Resume an interrupted run

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --resume \
  --manual-supervisor \
  --auto-approve
```

`--resume` reads the checkpoint from `<REPO_ROOT>/bridge_progress/checkpoint.json` and skips already-completed tasks.

### Dry run (inspect plan without executing)

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --dry-run
```

Use this to validate the plan JSON parses correctly before committing to a run.

### Specific Aider model

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --aider-model ollama/qwen2.5-coder:14b
```

Common model values:
- `ollama/qwen2.5-coder:7b` — fast, good for simple tasks
- `ollama/qwen2.5-coder:14b` — more capable, slower
- `ollama/deepseek-coder:6.7b` — fast alternative
- `ollama/mistral` — general fallback

### Unity / large non-code projects (disable repo map)

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --aider-no-map \
  --project-type unity
```

Use `--aider-no-map` when the repo has large non-code directories (Unity Library/, node_modules/) that cause Aider to hang during initial scan.

### With validation command (CI gate after each task)

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --validation-command "python -m pytest tests/ -x -q"
```

The validation command runs after each task. If it exits non-zero, the task is marked failed.

---

## Flag Quick Reference

| Flag | Default | When to use |
|---|---|---|
| `--repo-root PATH` | cwd | Always set when target repo ≠ cwd |
| `--plan-file PATH` | none | Always — point to `TASK_PLAN_active.json` |
| `--manual-supervisor` | off | **Always** — makes YOU the reviewer |
| `--workflow-profile micro` | standard | **Always** — enforces one-file atomic tasks |
| `--auto-approve` | off | **Always** — you review via manual-supervisor protocol instead |
| `--session-tokens N` | estimated | Set from `/cost` for accurate reporting |
| `--resume` | off | Only when resuming a crashed/interrupted run |
| `--dry-run` | off | Only to validate plan JSON before running |
| `--aider-model MODEL` | Aider default | Only when you want to override model selection |
| `--aider-no-map` | off | Unity/Unreal/large asset repos |
| `--project-type TYPE` | auto-detect | Set if auto-detect fails or for faster startup |
| `--validation-command CMD` | none | When you have a CI command to gate each task |
| `--max-task-retries N` | 10 | Reduce to 3 for faster failure on bad plans |
| `--no-auto-commit` | off | Only if user explicitly wants unstaged changes |
| `--confirm-plan` | off | Avoid — you already confirmed in Stage 2 |
| `--auto-split-threshold N` | 0 | Avoid unless user asks — changes task granularity |
| `--model-lock` | off | Avoid — disables smart model routing |

---

## Manual Supervisor Protocol

With `--manual-supervisor`, the bridge writes review requests and waits for decision files.

**Review request path:**
```
<REPO_ROOT>/bridge_progress/manual_supervisor/<session_id>/review_request_<task_id>.json
```

**Decision file path** (written by you):
The review request JSON contains a `response_path` field — write your decision there.

**Decision file format:**

PASS:
```json
{ "decision": "approved", "notes": "looks correct" }
```

REWORK:
```json
{ "decision": "rework", "reason": "specific actionable reason here" }
```

The bridge polls for the decision file every 5 seconds (configurable via `--manual-review-poll-seconds`). Write the file to unblock it.

---

## Project Knowledge Files

These files persist between runs and improve plan quality:

| File | Location | Purpose |
|---|---|---|
| `project_knowledge.json` | `<REPO_ROOT>/bridge_progress/` | File roles, run history, learned facts |
| `checkpoint.json` | `<REPO_ROOT>/bridge_progress/` | Completed task IDs for resume |
| `task_metrics.json` | `<REPO_ROOT>/bridge_progress/` | Per-task status, timing, retry counts |
| `improvement_plan.json` | `<REPO_ROOT>/bridge_progress/` | Last executed plan (used by --resume) |

Read `project_knowledge.json` during Stage 2 planning to get context about the target project before producing your task plan.
