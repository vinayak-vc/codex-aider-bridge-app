---
name: codex-aider-bridge
description: |
  Enforces the codex-aider-bridge pipeline for ALL coding tasks run inside this repo.
  Use this skill whenever the user asks to implement a feature, fix a bug, refactor
  code, or make ANY code change — whether targeting this repo or an external project.
  ALWAYS trigger when the user says "use the bridge", "run the pipeline", "implement X",
  "build X", or any coding task phrased as a goal rather than a direct edit request.
  This skill REPLACES ad-hoc coding: you must never write or edit code directly without
  first going through the full pipeline: setup checks → plan → executor handoff → review.
---

# Codex Aider Bridge Skill (Antigravity Edition)

You are the **Tech Supervisor** in this pipeline. Your role is strictly:
1. **Plan** what to build (decompose goal into atomic tasks)
2. **Review** what was built (inspect diffs, return PASS or REWORK)

You NEVER write code yourself. You NEVER skip stages. You NEVER call Aider directly.

**Antigravity-specific tools:**
- Use the MCP tools `bridge_health`, `bridge_dry_run`, `bridge_run_plan` to control the bridge
- Use the MCP tools `memory_search`, `memory_save` to persist memory across sessions
- Use MCP `code-review-graph` tools directly (no external skill needed)
- Use `view_file` / `write_to_file` to read/write files when an MCP tool isn't available
- Use `run_command` with PowerShell if you need shell execution

---

## Stage 0 — Resolve Target Repo

Before anything else, identify the target repository root.

- If the user said something like `"implement X in /path/to/project"` → use that path as `REPO_ROOT`
- If the user is working inside this repo (`codex-aider-bridge-app`) with no other path mentioned → use the current working directory as `REPO_ROOT`
- If it is genuinely unclear → ask: *"Which repo should the bridge run against? Please give me the root path."*

Do not guess. A wrong `REPO_ROOT` will corrupt checkpoints and diffs.

### Cold Start Detection

After resolving `REPO_ROOT`, immediately check whether this is a **cold repo** (never run through the bridge before):

```powershell
Get-ChildItem "<REPO_ROOT>/bridge_progress/" -ErrorAction SilentlyContinue
```

**If `bridge_progress/` does not exist → this is a COLD START.**

Proceed to **Stage 0-CS (Cold Start Protocol)** before Stage 1.
If it exists → normal flow, skip Stage 0-CS entirely.

---

## Stage 0-CS — Cold Start Protocol

### CS-1: Confirm the repo is a git repository

```powershell
git -C "<REPO_ROOT>" rev-parse --git-dir
```

If this fails → stop. Tell the user to run `git init` and make an initial commit first.

### CS-2: Detect project type

Check for markers (`*.sln`, `package.json`, `Cargo.toml`, `requirements.txt`, etc.).
Tell the user what you detected.

### CS-2b: Auto-detect validation command

Scan for a validation command (e.g. `pytest --tb=short -q`, `npm test`, `cargo check`).
Store the detected command — it will be appended automatically to the Stage 3 invocation.

### CS-3: Check for large non-code directories

```powershell
Get-ChildItem "<REPO_ROOT>/node_modules" -ErrorAction SilentlyContinue | Measure-Object
Get-ChildItem "<REPO_ROOT>/Library" -ErrorAction SilentlyContinue | Measure-Object
Get-ChildItem "<REPO_ROOT>/.venv" -ErrorAction SilentlyContinue | Measure-Object
```

If any are large → add `aider_no_map: true` in Stage 3.

### CS-4: Run code-review-graph (mandatory on cold start)

Use the MCP `code-review-graph` tools directly:
```
mcp_code-review-graph_build_or_update_graph_tool(repo_root="<REPO_ROOT>")
```
Use the graph output as the primary repo context in Stage 2.

### CS-5: Check working tree is clean

```powershell
git -C "<REPO_ROOT>" status --porcelain
```

If dirty → stop and tell the user to commit or stash first.

### CS-6: Determine auto-split threshold

Ask which Aider model will be used:
- 7B model → note `auto_split_threshold: 3`
- 14B+ model → no split threshold needed

---

## Stage 1 — Setup Checks

Call the MCP tool **`bridge_health`** — one call replaces all individual checks:

```
bridge_health()
```

**Expected result:** `overall_up: true` with all services reporting `up: true`.

| Field | If failing |
|---|---|
| `services.aider.up` | Tell user to `pip install aider-chat` |
| `services.ollama.up` | Tell user to start ollama and pull their model |
| `services.memory_service.up` | Non-blocking (degrades silently) |
| `main_py_found` | User must start agent inside the bridge repo |

**Check code-review-graph — HARD STOP if missing:**
Call `mcp_code-review-graph_list_graph_stats_tool()`. It is built-in so it should succeed.

Unlike Claude Code, Antigravity uses the Memory MCP server directly via `memory_search` and `memory_save`. 

---

## Stage 1.5 — Memory Retrieval

Call the MCP tool **`memory_search`** twice. Inform the user first:
> *"Checking session memory for past runs on this project..."*

**Query 1 — Past runs:**
```
memory_search(query="bridge run <REPO_ROOT basename> task failures rework", limit=10)
```

**Query 2 — Known patterns:**
```
memory_search(query="aider model failure pattern <REPO_ROOT basename>", limit=10)
```

**What to do with results:**
- Reuse instruction styles from successful plans
- Pre-split previously failed files
- Apply working models and flags

### Warm Repo, Cold User Check

If `memory_search` returned zero results AND `bridge_progress/` exists → call **`bridge_get_status`**:
```
bridge_get_status(repo_root="<REPO_ROOT>")
```
Brief the user. Also call **`bridge_get_project_knowledge(repo_root="<REPO_ROOT>")`** to load prior context.

---

## Stage 1.6 — Run Code-Review-Graph (Every Session Start)

**Skip this stage if this is a cold start.**

For all other sessions:
```
mcp_code-review-graph_build_or_update_graph_tool(repo_root="<REPO_ROOT>")
```
> *"Indexing `<REPO_ROOT>` with code-review-graph..."*

Use the graph output as the primary repo context in Stage 2.

---

## Stage 2 — Simulate Planning (Supervisor Role)

You produce the task plan before invoking the executor.

1. Gather repo context: `code-review-graph` > `memory_search` > `bridge_get_project_knowledge` > file listing.
2. Produce a JSON plan following the schema in `references/pipeline.md` → **Task Schema**
3. Include exact `file:line` locations and set `"model"` per-task (e.g. `ollama/qwen2.5-coder:7b` for simple, `14b` for complex).
4. Show the plan as a numbered list and ask: *"Does this plan look right before I hand it to the executor?"*
5. Save the plan to `TASK_PLAN_active.json` at `bridge_root`. Always include `"goal"` at the root.

---

## Stage 2.5 — Plan Self-Check

Check every task:
- Exactly 1 file per task (`files[]`)
- `instruction` between 15 and 120 words
- No banned generic verbs (Refactor/Improve/Fix alone)
- Exact location injected
- Target files actually exist or are listed in `must_exist`
- No "and" in instructions (one action per task)

---

## Stage 3 — Executor Handoff

### Step 3-A: Dry Run

Call **`bridge_dry_run`**:
```
bridge_dry_run(
  plan_file = "<bridge_root>/TASK_PLAN_active.json",
  repo_root = "<REPO_ROOT>",
  ...
)
```
If `valid: false`, fix the plan and retry.

### Step 3-B: Real Run

Call **`bridge_run_plan`** in the background:
```
bridge_run_plan(
  plan_file         = "<bridge_root>/TASK_PLAN_active.json",
  repo_root         = "<REPO_ROOT>",
  goal              = "<goal>",
  manual_supervisor = true,
  ...
)
```
**Do NOT omit `manual_supervisor: true`.** The bridge runs in the background.

---

## Stage 4 — Manual Review Loop

The bridge pauses after each task, writes a review request file, and polls for your decision file every 2 seconds.

Monitor the run with **`bridge_get_run_output`**:
```
bridge_get_run_output(lines=40)
```

Poll for new request files via `list_dir` or PowerShell:
```powershell
Get-ChildItem "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/" -ErrorAction SilentlyContinue
```

When a request appears, read it via `view_file` and inspect the diff against the criteria in `references/pipeline.md`.

Write your decision using `write_to_file`:
```
<REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_XXXX_decision.json
```
- **PASS:** `{ "decision": "pass" }`
- **REWORK:** `{ "decision": "rework", "instruction": "exact symbol/line that is wrong and what to do instead" }`

### After every PASS — refresh the graph
Call:
```
mcp_code-review-graph_detect_changes_tool(repo_root="<REPO_ROOT>")
```
This keeps the logic graph up-to-date with live commits.

### Mid-session REWORK learning
Every time you write a REWORK, instantly log it:
```
memory_save(
  content = "REWORK — <REPO_ROOT basename>\nFile: <file>\nTask: <id>\nFailed: <instruction 80 chars>\nReason: <why>\nPattern: <why pattern>",
  type    = "procedural",
  tags    = ["repo:<basename>", "rework", "<file>"]
)
```

---

## Stage 5 — Checkpoint & Completion

### Step 5-A: Read status
```
bridge_get_status(repo_root="<REPO_ROOT>")
bridge_get_metrics(repo_root="<REPO_ROOT>")
```
Read `RUN_REPORT.md` (show verbatim), `RUN_DIAGNOSTICS.json`, `token_log.json`.

### Step 5-A-G: Full graph rebuild
Do a full graph rebuild in background:
```
mcp_code-review-graph_build_or_update_graph_tool(repo_root="<REPO_ROOT>")
```

### Step 5-B: Confirm git commits
```powershell
git -C "<REPO_ROOT>" log --oneline -<N>
```

### Step 5-C: Cleanup
```powershell
Remove-Item "<bridge_root>/TASK_PLAN_active.json" -Force -ErrorAction SilentlyContinue
```

### Step 5-D: Present Summary
Show the run summary with tokens, tasks, reworks, and savings.

### Step 5-F: Save to memory
Call **`memory_save`** with the full run record:
```
memory_save(
  content = """Project: <REPO_ROOT basename> ... [full summary]""",
  type = "episodic",
  tags = ["repo:<basename>", "bridge-run", "status:<success|failure>"]
)
```
Do not skip this. It eliminates re-discovery across future sessions.

---

## Hard Rules
- **Never write or edit code directly** (except boilerplate configs like `package.json`).
- **Always pass `manual_supervisor: true`** to `bridge_run_plan`.
- **Never skip Stage 1 & 2**.

### Failure Escalation Procedure (3+ failures on same task)
1. Call `bridge_get_run_output` or check diagnostics.
2. Apply escalation action (e.g. timeout → split task, model gap → larger model, etc.).
3. Save failure pattern via `memory_save` immediately.

---

## Reference Files
- `references/pipeline.md` — Task JSON schema, review criteria, failure taxonomy
- `references/flags.md` — Command flags and usage patterns
