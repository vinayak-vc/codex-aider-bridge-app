# How To Use the Codex Aider Bridge App
### Practical guide for manual-supervisor workflow

---

## What This Tool Is For

This bridge is designed for a three-part workflow:

- **You / an agentic AI session** acts as the technical supervisor
- **Aider** acts as the developer
- **The bridge** only executes, validates, records, and waits for the next decision

It also keeps a persistent memory of the external project under `bridge_progress/` so the supervising AI can resume from structured state instead of rebuilding context manually.

The recommended mode is:
- `--manual-supervisor`
- `--workflow-profile micro`

That means:
- the AI writes the plan
- the bridge does not call another AI CLI
- Aider performs the file changes
- the bridge pauses after each task for review

---

## The Recommended Flow

1. Choose a target project.
2. Create a brief or goal file if needed.
3. Have the agentic AI read:
   - the brief
   - a file tree of the target repo
   - any project summary file if available
4. Have the AI write a plan JSON into:
   - `<target_repo>/taskJsons/`
5. Make sure the target repo is a git repository.
6. Run the bridge against that plan.
7. Review each task request JSON and write a decision JSON.
8. Let the bridge continue until done.

---

## The Plan Format You Should Use

For this bridge, the best plan format is extremely atomic.

### Good plan rules

- One file per task
- One concern per task
- Specific instruction
- Observable post-condition
- No vague “refactor everything” tasks

### Recommended JSON shape

```json
{
  "tasks": [
    {
      "id": 1,
      "files": ["src/app/config.py"],
      "instruction": "Create the configuration loader for runtime settings.",
      "type": "create",
      "must_exist": ["src/app/config.py"],
      "must_not_exist": []
    },
    {
      "id": 2,
      "files": ["src/app/main.py"],
      "instruction": "Update the CLI entry point to load settings from config.py before bootstrapping the app.",
      "type": "modify",
      "must_exist": ["src/app/main.py"],
      "must_not_exist": []
    }
  ]
}
```

---

## How To Run The Bridge

From the bridge repo root:

```powershell
python main.py "Short goal headline" `
  --repo-root "D:\ExternalProject" `
  --plan-file "D:\ExternalProject\taskJsons\plan_001_feature.json" `
  --workflow-profile micro `
  --manual-supervisor `
  --aider-model ollama/qwen2.5-coder:14b
```

### What each flag means

- `--repo-root` points to the external project being edited
- `--plan-file` gives the prewritten atomic task plan
- `--workflow-profile micro` enforces high-accuracy one-file tasks
- `--manual-supervisor` keeps review inside the current AI session
- `--aider-model` chooses the local coding model

Before Aider starts, the bridge now shows a git-readiness summary for the target repo.
If the target folder is not a git repository, the bridge will not proceed until:
- you create the repo yourself and rerun
- or you allow the bridge to initialize the local git repo and baseline commit for you

---

## Where Review Files Appear

During a manual-supervisor run, the bridge creates:

- Requests:
  - `<target_repo>/bridge_progress/manual_supervisor/requests/`
- Decisions:
  - `<target_repo>/bridge_progress/manual_supervisor/decisions/`
- Completed receipts:
  - `<target_repo>/bridge_progress/manual_supervisor/completed/`

It also maintains:

- `<target_repo>/bridge_progress/project_knowledge.json`
- `<target_repo>/bridge_progress/project_snapshot.json`
- `<target_repo>/bridge_progress/task_metrics.json`
- `<target_repo>/bridge_progress/token_log.json`
- `<target_repo>/bridge_progress/LATEST_REPORT.md`
- `<target_repo>/bridge_progress/last_run.json`

### Request file contains

- task id
- task type
- target files
- instruction
- execution result
- validator result
- unexpected files
- git diff

### Knowledge and analytics files contain

- `project_knowledge.json`
  - what each known file is responsible for
  - features already completed
  - run history
- `project_snapshot.json`
  - current file tree snapshot
  - completed and pending task ids
- `task_metrics.json`
  - machine-readable per-run task state
  - per-task `commit_sha` values when auto-commit succeeds
- `token_log.json`
  - token/savings history
- `LATEST_REPORT.md`
  - quick human-readable summary

---

## How To Approve Or Correct A Task

When a request file appears, read it and write a decision JSON.

### PASS

```json
{
  "task_id": 4,
  "decision": "pass"
}
```

### REWORK

```json
{
  "task_id": 4,
  "decision": "rework",
  "instruction": "In src/app/main.py, fix the CLI parsing so --help works without requiring the runtime-only arguments."
}
```

### SUBPLAN

```json
{
  "task_id": 4,
  "decision": "subplan",
  "sub_tasks": [
    {
      "instruction": "In src/app/main.py, repair the syntax error near the top of the file.",
      "files": ["src/app/main.py"],
      "type": "modify"
    }
  ]
}
```

The bridge will archive the processed request and decision files automatically.
It will also keep updating the knowledge/snapshot/metrics files while the run is in progress.
If a task was already approved and the bridge crashed before checkpointing it, the completed receipt lets the next run resume that approval without asking Aider to rewrite the file.

---

## How To Prepare An Agentic AI For External Projects

If you want to use this bridge with another repo, the supervising AI should follow this checklist:

1. Do not directly edit the target repo.
2. Read only the minimum context:
   - brief / goal file
   - file tree
   - optional knowledge cache
3. Generate a plan JSON, not code.
4. Save the plan under the external repo’s `taskJsons/` directory.
5. Keep tasks micro-atomic:
   - one file
   - one concern
   - assertions included
6. Run the bridge in manual-supervisor mode.
7. Review every task before allowing the next one.
8. Let the bridge auto-commit each approved task so future diffs stay small and recoverable.

This keeps the agent acting as a technical lead instead of as the coder.

---

## Picking A Local Model

For your bridge workflow:

- Use the biggest coding model your hardware can actually run comfortably.
- If your GPU is weak, shrink the tasks instead of forcing a huge model.

On smaller hardware, prefer:

- `qwen2.5-coder:14b` if it fits
- smaller coding models only with very tiny tasks

Avoid large models that barely fit, because slow local coding increases retries and lowers accuracy.

---

## Troubleshooting

### The bridge says manual-supervisor mode requires a plan file

That is expected. In manual mode, the bridge does not ask another AI to plan.
You must supply `--plan-file`.

### Aider created junk files

Most common causes:

- tasks were too large
- tasks were multi-file
- no assertions were included
- local model was too weak
- auto-approve was used instead of manual review

Fix:

- use `--workflow-profile micro`
- keep one file per task
- require `must_exist` or `must_not_exist`
- review every task

### The bridge is waiting forever

It is waiting for a decision file.

Check:

- `<target_repo>/bridge_progress/manual_supervisor/requests/`
- `<target_repo>/bridge_progress/manual_supervisor/decisions/`

If a request exists, write the matching decision JSON.

If both the request and decision already exist from a prior interrupted run, rerun the same bridge command first. The bridge now consumes a matching pair automatically.

### The bridge says the target project must be a git repository

That is expected.

The bridge now requires git because diff-driven review and rollback depend on it.

If you are running interactively, choose one of:
- create the repo yourself and rerun
- let the bridge initialize the local git repo and create the first baseline commit

### The bridge crashed with `OSError: [Errno 22] Invalid argument`

That usually means stdout event emission failed, not that the task itself was lost.

Fix:
- rerun the exact same bridge command
- keep the same plan file
- let checkpointing and the completed receipt recover the last approved task

The bridge now treats stdout emission as best-effort, so the progress files in `bridge_progress/` should still be present.

### Token usage looks too high

Check `bridge_progress/token_log.json` for:
- `savings_percent_weighted`
- `savings_percent_successful_avg`
- `wasted_tokens_total`
- `waste_reason_counts`

If `tasks_executed` is `0`, treat that session as overhead rather than productive savings.

### Aider asks follow-up questions or lint-fix prompts

That means the task is still too broad or the model/tool pairing surfaced interactive output.

Fix:
- make the task narrower
- keep one file per task
- rerun with the same plan when possible so checkpointing skips finished work
- inspect stderr because the bridge now flags interactive prompt output explicitly

### The old knowledge JSON was not generated

Current bridge behavior:
- knowledge and snapshot files are persisted during the run
- they are also written on failed runs

So if a future run stops midway, you should still have usable state in `bridge_progress/`.

---

## Short Version

```text
AI reads brief + file tree
AI writes plan JSON into target_repo/taskJsons/
Bridge runs one atomic task through Aider
Bridge writes review request JSON
Bridge updates project knowledge + analytics files
AI reviews diff and writes decision JSON
Bridge continues
Repeat until done
```

That is the intended way to use this bridge on external projects.
