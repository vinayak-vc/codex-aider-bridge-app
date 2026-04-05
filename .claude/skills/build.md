---
name: build
description: Generate a task plan, write it to disk, launch the bridge, and auto-review all tasks. Usage: /build <goal> --project <path>
command: build
---

# /build — Automated Bridge Runner

When the user says `/build <goal>` or "build X", execute this full workflow:

## Step 1: Parse Arguments

Extract from user message:
- **goal**: what to build/fix/implement
- **project**: repo root path (use `--project <path>` or ask if not provided)
- **model**: Ollama model (default: `ollama/qwen2.5-coder:7b`)

If no project path given, check current settings:
```
cat <bridge_root>/ui/data/settings.json | look for repo_root
```

## Step 2: Understand the Project

Read these files from the **target project** (not the bridge):
1. `<project>/bridge_progress/project_knowledge.json` — file roles, patterns, features done
2. `<project>/bridge_progress/LATEST_REPORT.md` — last run results (if exists)

Also run the deep scanner to get code structure:
```python
from utils.deep_scanner import scan_project_signatures, signatures_to_context
from pathlib import Path
sigs = scan_project_signatures(Path("<project>"), max_files=30)
code_ctx = signatures_to_context(sigs)
```

If feature specs are referenced in the goal (e.g., "from missingFeatures/"), read those .md files too.

## Step 3: Generate Task JSON

Based on the project knowledge + code structure + goal, generate a task JSON following these rules:

- Each task targets ONE file, ONE concern
- Instructions name exact functions, parameters, line numbers
- Describe code structure (array literal vs function call vs property)
- Say which VALUE to change, not the whole pattern
- Keep instructions under 200 words each
- Use `"model": "ollama/qwen2.5-coder:7b"` for simple tasks, `"ollama/qwen2.5-coder:14b"` for complex logic
- Include `must_exist` for create tasks

Write the JSON to: `<project>/taskJsons/plan_<timestamp>.json`

## Step 4: Launch the Bridge

Run the bridge subprocess in the background:
```bash
cd <bridge_root>
python main.py "<goal>" \
  --repo-root "<project>" \
  --plan-file "<project>/taskJsons/plan_<timestamp>.json" \
  --manual-supervisor \
  --aider-model "ollama/qwen2.5-coder:7b" \
  --task-timeout 600 \
  --max-task-retries 10 \
  --log-level INFO
```

Run this with `run_in_background: true` so we can monitor it.

## Step 5: Set Up Auto-Review Cron

Create a cron job that polls every minute:
```
CronCreate "* * * * *":
  Check <project>/bridge_progress/manual_supervisor/requests/ for new request files.
  For each request without a corresponding decision:
  1. Read the request JSON (contains task instruction + diff)
  2. Analyze: does the diff correctly implement the task?
  3. Write PASS or REWORK decision to decisions/ folder

  Decision format: {"task_id": N, "decision": "pass"}
  or: {"task_id": N, "decision": "rework", "instruction": "specific fix..."}

  PASS criteria:
  - Diff is non-empty
  - Correct file(s) modified
  - Changes match the task instruction intent
  - No syntax errors visible in diff

  REWORK criteria:
  - Empty diff (no changes made)
  - Wrong file modified
  - Only whitespace/comments changed
  - Obvious logic error in diff
```

## Step 6: Monitor & Report

After launching, tell the user:
- "Bridge running with N tasks. Auto-review active."
- "Watching: <project>/bridge_progress/manual_supervisor/requests/"
- "Open http://127.0.0.1:7823/run to see live progress (optional)"

When the bridge finishes (background task completes), report:
- How many tasks completed
- Any failures
- Total time elapsed

## Example Usage

User: `/build implement max-videos-control --project D:\BridgeProjectExperiment`

Response:
1. Read project knowledge → understand uploadService.js, useAppStore.js
2. Generate 3-task plan → write to taskJsons/plan_20260405_200000.json
3. Launch bridge subprocess → running in background
4. Cron watcher active → auto-reviewing every 60s
5. "Bridge running with 3 tasks. Auto-review active. I'll report when done."
