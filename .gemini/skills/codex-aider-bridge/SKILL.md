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
- Use `view_file` to read files
- Use `replace_file_content` / `write_to_file` to write decision files
- Use `run_command` to execute bridge commands and poll for requests
- Use `grep_search` to find symbols and patterns
- Use MCP `code-review-graph` tools directly (no external skill needed)
- Use Knowledge Items (KIs) instead of claude-mem for session memory

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

A cold repo has none of the bridge's infrastructure. Everything must be built from scratch before the first run. Work through this checklist in order — do not skip steps.

### CS-1: Confirm the repo is a git repository

```powershell
git -C "<REPO_ROOT>" rev-parse --git-dir
```

If this fails → stop. Tell the user:
> *"The bridge requires a git repository. Please run `git init` in `<REPO_ROOT>` and make an initial commit before continuing."*

### CS-2: Detect project type

Check for these markers in order:

| Marker found | Project type to use |
|---|---|
| `*.sln` or `*.csproj` + `Assets/` directory | `unity` |
| `*.sln` or `*.csproj` (no Assets/) | `csharp` |
| `project.godot` | `godot` |
| `*.uproject` | `unreal` |
| `pubspec.yaml` | `flutter` |
| `Cargo.toml` | `rust` |
| `go.mod` | `go` |
| `package.json` + `.ts`/`.tsx` files | `typescript` |
| `package.json` (no TypeScript) | `javascript` |
| `requirements.txt` or `pyproject.toml` or `setup.py` | `python` |
| None of the above | `other` — ask the user |

Tell the user what you detected:
> *"Detected project type: `python`. I'll use this to pick the right validator and plan hints."*

### CS-2b: Auto-detect validation command

After detecting the project type, scan for a validation command to pass as `--validation-command`. Check in this order:

| Project type | Files to check | Command to use |
|---|---|---|
| `python` | `pytest.ini`, `pyproject.toml` (`[tool.pytest]`), `setup.cfg` (`[tool:pytest]`) | `pytest --tb=short -q` |
| `python` | `pyproject.toml` (`[tool.ruff]`) | `ruff check .` |
| `javascript` / `typescript` | `package.json` → `scripts.test` key | `npm test` |
| `rust` | `Cargo.toml` present | `cargo check` |
| `go` | `go.mod` present | `go build ./...` |
| `csharp` | `*.sln` present | `dotnet build` |

Store the detected command — it will be appended automatically to the Stage 3 invocation.

### CS-3: Check for large non-code directories

```powershell
Get-ChildItem "<REPO_ROOT>/node_modules" -ErrorAction SilentlyContinue | Measure-Object
Get-ChildItem "<REPO_ROOT>/Library" -ErrorAction SilentlyContinue | Measure-Object
Get-ChildItem "<REPO_ROOT>/.venv" -ErrorAction SilentlyContinue | Measure-Object
```

If any of these exist and are large → **add `--aider-no-map` to Stage 3 invocation automatically**.

### CS-4: Run code-review-graph (mandatory on cold start)

On a cold repo you have zero prior knowledge. Use the MCP `code-review-graph` tools directly:

1. `mcp_code-review-graph_build_or_update_graph_tool` with `full_rebuild=True` and `repo_root=<REPO_ROOT>`
2. `mcp_code-review-graph_get_architecture_overview_tool`
3. `mcp_code-review-graph_get_hub_nodes_tool`

Use the graph output as the primary repo context in Stage 2.

### CS-5: Check working tree is clean

```powershell
git -C "<REPO_ROOT>" status --porcelain
```

If dirty → stop and tell the user to commit or stash first.

### CS-6: Determine `--auto-split-threshold`

Ask which Aider model will be used:
- 7B model → note `--auto-split-threshold 3`
- 14B+ model → no split threshold needed
- Not sure → default to `--auto-split-threshold 3`

---

## Stage 1 — Setup Checks

Run these checks before any planning. If any fail, stop and help the user fix them.

See `references/flags.md` → **Setup Checks** section for the exact commands.

| Check | Command | Pass condition |
|---|---|---|
| Python available | `python --version` | Python 3.9+ |
| Aider installed | `aider --version` | exits 0 |
| Ollama running | `ollama list` | exits 0, shows at least one model |
| main.py exists | `Test-Path main.py` | file present in cwd |

`bridge_root` = **current working directory (`cwd`)**. The skill always runs from inside the bridge repo. All bridge commands use `python main.py`.

---

## Stage 1.5 — Knowledge Retrieval (Antigravity KIs)

This stage runs automatically — no user decision needed. Inform the user with a single line:

> *"Checking Knowledge Items for past runs on this project..."*

Check for existing KIs about this project by listing the KI directory and reading relevant metadata.

**What to do with the results:**

| What KIs return | How to use it in Stage 2 |
|---|---|
| Previous task plans that succeeded | Reuse their instruction style and task structure |
| Known failure types for specific files | Pre-split those tasks more aggressively |
| Models that performed well on this repo | Set as preferred `--aider-model` in Stage 3 |
| Files that repeatedly needed REWORK | Flag them in the plan with a note |
| Repo-specific quirks (e.g. "always use --aider-no-map") | Apply those flags automatically in Stage 3 |

If no KIs found (first run), silently continue — Stage 2 proceeds on graph + `project_knowledge.json` alone.

---

## Stage 1.6 — Run Code-Review-Graph (Every Session Start)

**Skip this stage if this is a cold start** — CS-4 already ran the graph.

For all other sessions, use the MCP code-review-graph tools:

```
mcp_code-review-graph_build_or_update_graph_tool (incremental update)
mcp_code-review-graph_get_minimal_context_tool
```

> *"Indexing `<REPO_ROOT>` with code-review-graph..."*

Use the graph output as the primary repo context in Stage 2.

**Important:** The graph is a session-start snapshot. Do not re-run between tasks mid-session.

---

## Stage 2 — Simulate Planning (Supervisor Role)

Before invoking the real executor, YOU produce the task plan.

### How to produce the plan

1. Get repo context, layered in priority order:
   - **Highest:** `code-review-graph` MCP output from Stage 1.6
   - **Then:** Knowledge Items from Stage 1.5
   - **Then:** `bridge_progress/project_knowledge.json` in `REPO_ROOT` if present
   - **Fallback:** `git -C "<REPO_ROOT>" ls-files` (all tracked files, cap at 300 lines)
2. Produce a JSON plan following the **exact schema** in `references/pipeline.md` → **Task Schema**
3. Apply the MICRO-TASK PROFILE rules (one file per task, surgical instructions, explicit assertions)
4. When `code-review-graph` context is available:
   - Include exact `file:line` locations in every instruction
   - **Auto-populate `context_files` using dependency edges:** use `mcp_code-review-graph_query_graph_tool` with `pattern=imports_of` for each target file
5. Set per-task `"model"` field based on complexity:
   - `"model": "ollama/qwen2.5-coder:7b"` — single-class utility, no complex imports
   - `"model": "ollama/qwen2.5-coder:14b"` — multi-import orchestration, complex logic
   - `"model": null` — inherit bridge default
6. Show the plan to the user as a numbered list and ask: *"Does this plan look right before I hand it to the executor?"*
7. Once confirmed, save the plan to `TASK_PLAN_active.json` (in cwd = bridge_root). **Always include `"goal"` at root level** — `{ "goal": "...", "tasks": [...] }`

---

## Stage 2.5 — Plan Self-Check (MICRO-TASK Validation)

Before saving, run this checklist against **every task**:

| Rule | Check | Auto-fix |
|---|---|---|
| **One file per task** | `files[]` must have exactly 1 entry | Split into separate tasks |
| **Word count** | `instruction` must be ≥ 15 words and ≤ 120 words | Expand or trim |
| **No banned verbs** | Must NOT start with: "Refactor", "Clean up", "Improve", "Update", "Fix" (alone) | Rewrite with precise imperative |
| **Exact location** | If graph context available: every instruction must name at least one `file:line` or function name | Inject from graph output |
| **`must_exist` for create tasks** | If task creates a file: `must_exist[]` must list it | Add the expected path |
| **File exists for modify tasks** | Confirm the file appears in git or graph output | Flag to user if not found |
| **No "and" in instructions** | Must not describe two independent actions joined by "and" | Split into two tasks |

Report:
> **Plan self-check:** 7 tasks checked — 2 split, 1 instruction rewritten, 0 blockers. Ready to save.

---

## Stage 3 — Executor Handoff

**`bridge_root` = cwd.** All `python main.py` commands run from the current working directory.

### Step 3-A: Dry run

```powershell
python main.py --repo-root "<REPO_ROOT>" --plan-file "TASK_PLAN_active.json" --dry-run
```

If dry run fails → fix the plan and re-run. Do not proceed until it exits 0.

### Step 3-B: Real run

```powershell
python main.py --repo-root "<REPO_ROOT>" --plan-file "TASK_PLAN_active.json" --manual-supervisor --workflow-profile micro --session-tokens <N>
```

**Do NOT add `--auto-approve`.** The bridge writes review request files and waits for your decision files.

---

## Stage 4 — Manual Review Loop

With `--manual-supervisor`, the bridge pauses after each task and writes a review request file. Run the bridge in the background using `run_command`.

### Step 1 — Start bridge in background

Use `run_command` with a long `WaitMsBeforeAsync` (500ms) to run the bridge as a background process.

### Step 2 — Poll for review requests

```powershell
Get-ChildItem "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/" -ErrorAction SilentlyContinue
```

### Step 3 — Read the review request

Use `view_file` to read the request JSON containing `task_id`, `instruction`, `diff`, `execution`, `validation`, `files`.

### Step 4 — Inspect the diff against the task instruction using Review Criteria from `references/pipeline.md`.

### Step 5 — Write decision file

Use `write_to_file` to create:
```
<REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_XXXX_decision.json
```

PASS:
```json
{ "decision": "pass" }
```

REWORK:
```json
{ "decision": "rework", "instruction": "specific actionable instruction" }
```

SUBPLAN:
```json
{
  "decision": "subplan",
  "sub_tasks": [
    { "instruction": "create X at src/x.ts", "files": ["src/x.ts"], "type": "create" }
  ]
}
```

---

## Stage 5 — Checkpoint & Completion

### Step 5-A: Read all generated files

Read these files from `<REPO_ROOT>/bridge_progress/` using `view_file`:
1. `task_metrics.json` — verify status
2. `RUN_REPORT.md` — show to user verbatim
3. `RUN_DIAGNOSTICS.json` — check blocking patterns
4. `last_run.json` — quick confirmation
5. `token_log.json` — extract savings %
6. `LATEST_REPORT.md` — status overview

### Step 5-B: Confirm git commits

```powershell
git -C "<REPO_ROOT>" log --oneline -<N>
```

### Step 5-C: Cleanup

```powershell
Remove-Item "<bridge_root>/TASK_PLAN_active.json" -Force -ErrorAction SilentlyContinue
```

### Step 5-D: Present the run summary

```
Run complete — <REPO_ROOT basename>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tasks:    <completed> / <planned> completed  (<skipped> skipped)
Reworks:  <N> task(s) needed rework
Commits:  <N> git commits made
Duration: <elapsed_seconds>s

Token usage (from RUN_REPORT.md):
  Supervisor (plan + review):  <total> tokens
  Aider / Ollama (local):      ~<estimated> tokens (free)
  Total cloud AI:              <total_ai> tokens

Savings vs doing this without the bridge:
  Estimated direct cost:  <estimated_direct> tokens
  Actual cost:            <total_ai> tokens
  Saved:                  <tokens_saved> tokens (<savings_pct>%)

Blocking patterns detected: <none / list>
Reports written to: <REPO_ROOT>/bridge_progress/
```

---

## Hard Rules

These are non-negotiable:

- **Never write or edit code directly** — all code changes go through Aider via the bridge. **Boilerplate exception:** you may write `package.json`, `tsconfig.json`, `.env.example`, `docker-compose.yml`, `AGENTS.md`, `.gitignore` directly — these are pure config with no imports. Every `.ts`, `.py`, `.js`, `.cs` source file must go through bridge.
- **Always include `"goal"` at plan JSON root** — `{ "goal": "...", "tasks": [...] }`
- **Never use `--auto-approve`** — it bypasses all review
- **Never run main.py without `--manual-supervisor`** — without it, the bridge calls an external supervisor CLI and ignores you
- **Never skip Stage 1 (setup checks)** — a broken Aider install will corrupt bridge_progress state
- **Never skip Stage 2 (simulate plan)** — user must see and approve the task plan first
- **If a task fails 3+ times** → pause and execute the failure escalation procedure below

### Failure Escalation Procedure (3+ failures on same task)

1. Read `bridge_progress/RUN_DIAGNOSTICS.json` → find the failing task
2. Identify the failure type using `references/pipeline.md` → **Failure Taxonomy**
3. Apply the escalation action:

| Failure type | Escalation action |
|---|---|
| `interactive_prompt` | Add the file to `context_files` |
| `timeout` | Split the task or increase `--task-timeout` |
| `silent_failure` | Rewrite instruction with exact function/class/symbol |
| `repeated_validation_failure` | Rewrite `must_exist` / `must_contain` |
| `supervisor_rework_loop` | Clarify acceptance criteria |
| `model_capability_gap` | Tell user: switch to a larger model |
| `missing_dependency` | Add a preceding task for the dependency |

4. Show revised instruction and ask user to approve or abort

---

## Reference Files

- `references/pipeline.md` — Task JSON schema, MICRO-TASK rules, review criteria, failure taxonomy
- `references/flags.md` — All `main.py` flags, standard invocations, situational variants
