# AI Supervisor Reference

This document explains how an agentic AI should behave when supervising Aider through this bridge.

The recommended operating mode is no longer “spawn another AI CLI and hope it plans well”.

The recommended mode is:

- the active AI session creates the task plan
- the bridge runs Aider
- the bridge validates and records results
- the active AI session reviews each task
- the bridge resumes from the AI’s decision
- the bridge keeps a structured project memory under `bridge_progress/`

---

## The Supervisor Role

The supervisor is the **technical lead**.

The supervisor:
- reads the user goal
- reads minimal project context
- creates an atomic JSON task plan
- reviews each task diff
- decides `pass`, `rework`, or `subplan`

The supervisor does **not**:
- write code directly
- edit project files directly
- let tasks continue without review

---

## Recommended Bridge Mode

Use:

```bash
python main.py "Short goal headline" \
  --repo-root "D:\ExternalProject" \
  --plan-file "D:\ExternalProject\taskJsons\plan_001_feature.json" \
  --workflow-profile micro \
  --manual-supervisor \
  --aider-model ollama/qwen2.5-coder:14b
```

### Why this mode is preferred

- no external supervisor CLI required
- low supervisor token use
- Aider does the coding
- the bridge stays deterministic
- each task is reviewed before the next one starts

---

## Micro-Task Planning Rules

When creating a plan for this bridge, the supervisor should follow these rules:

1. Exactly one file per task
2. Exactly one concern per task
3. Use `create`, `modify`, `delete`, or `validate`
4. Add `must_exist` for create tasks
5. Add `must_not_exist` for delete tasks
6. Add at least one observable assertion for modify tasks
7. Keep instructions specific and code-free
8. Order tasks by dependency

### Good task example

```json
{
  "id": 3,
  "files": ["src/server/app.py"],
  "instruction": "Update the CLI entry point to accept an optional config path argument before bootstrapping the server.",
  "type": "modify",
  "must_exist": ["src/server/app.py"],
  "must_not_exist": []
}
```

### Bad task example

```json
{
  "id": 3,
  "files": ["src/server/app.py", "src/server/config.py", "README.md"],
  "instruction": "Refactor the app and improve config and docs.",
  "type": "modify"
}
```

Why bad:
- too many files
- too many concerns
- no observable assertion

---

## Review Request Files

In manual-supervisor mode the bridge writes review requests to:

```text
<repo_root>/bridge_progress/manual_supervisor/requests/
```

Each request contains:

- task id
- task type
- files
- original instruction
- execution result
- validator result
- unexpected files
- git diff

The supervisor should read the request file and produce exactly one decision file.

The supervisor should also prefer reading these files before opening arbitrary source:

- `bridge_progress/project_knowledge.json`
- `bridge_progress/project_snapshot.json`
- `bridge_progress/LATEST_REPORT.md`

---

## Decision File Formats

Write decision files to:

```text
<repo_root>/bridge_progress/manual_supervisor/decisions/
```

### PASS

```json
{
  "task_id": 12,
  "decision": "pass"
}
```

### REWORK

```json
{
  "task_id": 12,
  "decision": "rework",
  "instruction": "In src/server/app.py, fix the CLI argument parsing so --help works without requiring runtime-only inputs."
}
```

### SUBPLAN

```json
{
  "task_id": 12,
  "decision": "subplan",
  "sub_tasks": [
    {
      "instruction": "In src/server/app.py, repair the syntax error near the top of the file.",
      "files": ["src/server/app.py"],
      "type": "modify"
    }
  ]
}
```

---

## External Project Readiness Checklist

Before using this bridge on another repo, the supervising AI should:

1. Read the goal file or project brief
2. Read only a file tree of the target repo
3. Read any project knowledge cache if available
4. Avoid opening arbitrary source files unless required by the workflow
5. Save the plan under the target repo’s `taskJsons/`
6. Run the bridge in manual-supervisor mode
7. Review every task request before allowing the next task

### Preferred context order for follow-up sessions

1. `bridge_progress/LATEST_REPORT.md`
2. `bridge_progress/project_knowledge.json`
3. `bridge_progress/project_snapshot.json`
4. request/decision archive if a specific task needs investigation
5. only then open source files if still necessary

This keeps supervision token-efficient and preserves the bridge's purpose as a project-memory layer.

This keeps the AI acting as a supervisor, not as the coder.

---

## External Supervisor CLI Mode

External supervisor CLIs are still supported, but they are no longer the recommended default.

Use them only when you explicitly want the bridge to spawn a planning/review subprocess:

```bash
python main.py "Build feature X" \
  --repo-root "D:\ExternalProject" \
  --supervisor-command "claude --print" \
  --aider-model ollama/deepseek-coder
```

For Codex-style in-session supervision, prefer manual mode instead.
