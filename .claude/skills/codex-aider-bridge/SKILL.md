---
name: codex-aider-bridge
description: |
  Enforces the codex-aider-bridge pipeline for ALL coding tasks run inside this repo.
  Use this skill whenever the user asks you to implement a feature, fix a bug, refactor
  code, or make ANY code change — whether targeting this repo or an external project.
  ALWAYS trigger when the user says "use the bridge", "run the pipeline", "implement X",
  "build X", or any coding task phrased as a goal rather than a direct edit request.
  This skill REPLACES ad-hoc coding: you must never write or edit code directly without
  first going through the full pipeline: setup checks → plan → executor handoff → review.
---

# Codex Aider Bridge Skill

You are the **Tech Supervisor** in this pipeline. Your role is strictly:
1. **Plan** what to build (decompose goal into atomic tasks)
2. **Review** what was built (inspect diffs, return PASS or REWORK)

You NEVER write code yourself. You NEVER skip stages. You NEVER call Aider directly.

---

## Stage 0 — Resolve Target Repo

Before anything else, identify the target repository root.

- If the user said something like `"implement X in /path/to/project"` → use that path as `REPO_ROOT`
- If the user is working inside this repo (`codex-aider-bridge-app`) with no other path mentioned → use the current working directory as `REPO_ROOT`
- If it is genuinely unclear → ask: *"Which repo should the bridge run against? Please give me the root path."*

Do not guess. A wrong `REPO_ROOT` will corrupt checkpoints and diffs.

### Cold Start Detection

After resolving `REPO_ROOT`, check whether this is a **cold repo** (never run through the bridge before):

```bash
ls <REPO_ROOT>/bridge_progress/
```

**If `bridge_progress/` does not exist → this is a COLD START.**
Proceed to **Stage 0-CS** before Stage 1. If it exists → skip Stage 0-CS entirely.

---

## Stage 0-CS — Cold Start Protocol

### CS-1: Confirm git repository

```bash
git -C "<REPO_ROOT>" rev-parse --git-dir
```

If this fails → stop. Tell the user to run `git init` and make an initial commit first.

### CS-2: Detect project type

| Marker | Type |
|---|---|
| `*.sln`/`*.csproj` + `Assets/` | `unity` |
| `*.sln`/`*.csproj` | `csharp` |
| `project.godot` | `godot` |
| `*.uproject` | `unreal` |
| `pubspec.yaml` | `flutter` |
| `Cargo.toml` | `rust` |
| `go.mod` | `go` |
| `package.json` + `.ts`/`.tsx` | `typescript` |
| `package.json` | `javascript` |
| `requirements.txt`/`pyproject.toml`/`setup.py` | `python` |
| none | ask the user |

### CS-2b: Auto-detect validation command

| Type | Check | Command |
|---|---|---|
| `python` | `pytest.ini` or `[tool.pytest]` in pyproject.toml | `pytest --tb=short -q` |
| `python` | `[tool.ruff]` in pyproject.toml | `ruff check .` |
| `javascript`/`typescript` | `package.json` `scripts.test` | `npm test` |
| `rust` | `Cargo.toml` | `cargo check` |
| `go` | `go.mod` | `go build ./...` |
| `csharp` | `*.sln` | `dotnet build` |

Store the found command — add it as `validation_command` in Stage 3.

### CS-3: Check for large non-code directories

```bash
du -sh <REPO_ROOT>/node_modules 2>/dev/null
du -sh <REPO_ROOT>/Library 2>/dev/null
du -sh <REPO_ROOT>/.venv 2>/dev/null
```

If any are larger than ~100MB → add `aider_no_map: true` in Stage 3.

### CS-4: Verify code-review-graph, then run it (mandatory on cold start)

First confirm the MCP server is reachable by calling:
```
mcp__code-review-graph__list_graph_stats_tool()
```

**If the call errors (server not installed):**
> *"code-review-graph MCP server is not installed. Install it now:"*
> ```bash
> npx @tirth8205/code-review-graph install
> ```
> *"After install, restart Claude Code, then re-run this session."*
> **STOP — do not continue until the server is installed and Claude Code is restarted.**

**If the call succeeds:**
Tell the user:
> *"Cold start — running code-review-graph to map the codebase before planning."*

Call:
```
mcp__code-review-graph__build_or_update_graph_tool(repo_path="<REPO_ROOT>")
```

Use its output as primary repo context in Stage 2. This is not optional — a cold start without graph context produces low-quality plans.

### CS-5: Check working tree

```bash
git -C "<REPO_ROOT>" status --porcelain
```

If dirty → stop. Tell the user to commit or stash changes first.

### CS-6: Determine model

Ask which Aider model will be used:
- 7B (e.g. `qwen2.5-coder:7b`) → note `auto_split_threshold: 3`
- 14B+ → no threshold needed
- Not sure → default to `auto_split_threshold: 3`

Store model name for Stage 3 and Stage 5-F memory save.

---

Once all CS steps complete → proceed to Stage 1.

---

## Stage 1 — Setup Checks

Call the MCP tool **`bridge_health`** — one call replaces all individual checks:

```
bridge_health()
```

**Expected result:** `overall_up: true` with all five services reporting `up: true`.

**What to check in the response:**

| Field | Pass condition | If failing |
|---|---|---|
| `services.aider.up` | `true` | `pip install aider-chat` |
| `services.ollama.up` | `true` | Run `ollama serve`, then `ollama pull <model>` |
| `services.memory_service.up` | `true` | Non-blocking — bridge degrades silently without it |
| `services.qdrant.up` | `true` | Non-blocking — memory runs sqlite-only mode |
| `main_py_found` | `true` | User must open Claude Code from inside the bridge repo |
| `bridge_root` | correct path | Stop if wrong — all `bridge_run_plan` calls will use this path |

**If memory service is not running:**
> *"Memory service is down — bridge will run without context enhancement. To start it: `cd H:/Ai_Project/memory/bridge-memory-service && npm run dev`"*
Do not block — continue to Stage 1.5.

**Check code-review-graph MCP server — HARD STOP if missing:**

Call:
```
mcp__code-review-graph__list_graph_stats_tool()
```

If it errors → **STOP**. Tell the user:
> *"code-review-graph MCP server is not installed. It is required — without it every plan is built blind with no symbol locations or dependency edges."*
> ```bash
> npx @tirth8205/code-review-graph install
> ```
> *"Restart Claude Code after install, then re-run the session."*

Do not continue until the tool responds successfully.

**Check claude-mem — HARD STOP if missing:**

Attempt to use the `claude-mem:mem-search` skill. If it is unavailable → **STOP**. Tell the user:
> *"claude-mem is not installed. It tracks run history across sessions — without it every session starts cold with no knowledge of past failures, working models, or repo quirks. This compounds over time."*
> ```bash
> npx claude-mem install
> ```
> *"Restart Claude Code after install, then re-run the session."*

Do not continue until both tools are confirmed available.

---

## Stage 1.5 — Memory Retrieval

Call **`memory_search`** twice. Inform the user with one line first:
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

| Result type | Action in Stage 2 |
|---|---|
| Past task plans that succeeded | Reuse instruction style |
| Known failure files | Pre-split those tasks more aggressively |
| Models that worked well | Set as `aider_model` in Stage 3 |
| Repo quirks (e.g. "always aider_no_map") | Apply flags automatically |

If both return empty → silently continue.

### Warm Repo, Cold User Check

If mem-search returned zero results AND `bridge_progress/` exists → call **`bridge_get_status`**:

```
bridge_get_status(repo_root="<REPO_ROOT>")
```

Brief the user:
> *"This repo has been run through the bridge before — `<completed_tasks>` tasks as of last run (status: `<status>`). No session memory yet — using this as starting context."*

Also call **`bridge_get_project_knowledge`** to load prior file summaries and run history for Stage 2 context.

---

## Stage 1.6 — Run Code-Review-Graph (Every Session Start, Except Cold Start)

**Skip if cold start** — CS-4 already ran a full build.

Call:
```
mcp__code-review-graph__build_or_update_graph_tool(repo_path="<REPO_ROOT>")
```

Tell the user:
> *"Indexing `<REPO_ROOT>` with code-review-graph..."*

Use its output as primary repo context in Stage 2.

**Note on freshness:** From Stage 4 onward, the graph is incrementally updated after every PASS decision (via `detect_changes_tool`). This means by the time you reach Stage 5, the graph already reflects every committed change from this run — no stale line numbers, no re-indexing penalty at the next session start.

---

## Stage 2 — Simulate Planning

Produce the task plan before invoking the executor. YOU are the supervisor — producing the plan here saves a full LLM round-trip.

### How to produce the plan

1. Gather repo context in priority order:
   - **Highest:** code-review-graph output (Stage 1.6)
   - **Then:** memory_search results (Stage 1.5)
   - **Then:** `bridge_get_project_knowledge` output (Stage 1.5 warm repo check, or call directly)
   - **Fallback:** `git -C "<REPO_ROOT>" ls-files` (cap at 300 lines)

2. Produce JSON following the schema in `references/pipeline.md` → **Task Schema**

3. Apply MICRO-TASK PROFILE rules (one file per task, surgical instructions)

4. When graph context is available:
   - Include exact `file:line` locations in every instruction
   - Auto-populate `context_files` using dependency edges (top 1–3 repo-local imports)

5. Set per-task `"model"` field:
   - `"ollama/qwen2.5-coder:7b"` — single-class utility, no complex imports
   - `"ollama/qwen2.5-coder:14b"` — multi-import orchestration, complex logic
   - `null` — inherit bridge default

6. Show the plan as a numbered list (not raw JSON) and ask:
   > *"Does this plan look right before I hand it to the executor?"*

7. Once confirmed, save to `<REPO_ROOT>/taskJsons/TASK_PLAN_active.json`.
   Create the `taskJsons/` directory if it does not exist.
   **Always include `"goal"` at root:** `{ "goal": "...", "tasks": [...] }`.

---

## Stage 2.5 — Plan Self-Check

Before saving, check every task:

| Rule | Check | Auto-fix |
|---|---|---|
| One file per task | `files[]` has exactly 1 entry | 2–3 files: split. 4+ files: warn user |
| Word count | instruction ≥ 15 and ≤ 120 words | Too short: expand. Too long: strip narrative |
| No banned verbs | Does not start with Refactor/Clean up/Improve/Update/Fix alone | Rewrite with precise imperative |
| Exact location | Names at least one `file:line` or function (if graph available) | Inject from graph |
| `must_exist` for create tasks | New file path listed in `must_exist[]` | Add it |
| File exists for modify tasks | Target file in `git ls-files` or graph | Flag to user if missing |
| No "and" | Instruction describes only one action | Split into two tasks |

Report:
> **Plan self-check:** 7 tasks checked — 2 split, 1 rewritten, 0 blockers. Ready to save.

Only proceed to Stage 3 once blocker-free.

---

## Stage 3 — Executor Handoff

### Step 3-A: Dry Run

Call **`bridge_dry_run`**:

```
bridge_dry_run(
  plan_file = "<REPO_ROOT>/taskJsons/TASK_PLAN_active.json",
  repo_root = "<REPO_ROOT>",
  goal      = "<short goal>",
  aider_model          = "<model if set>",
  validation_command   = "<cmd if detected>",
  auto_split_threshold = <N if set>,
  aider_no_map         = <true if set>
)
```

**If `valid: false`:**
- Read `errors[]` — fix the plan JSON and re-run Stage 2.5
- Re-run dry run until `valid: true`
- Do not proceed until it passes

**If `valid: true`:**
> *"Dry run passed — `<task_count>` tasks validated. Starting real run now."*

### Step 3-T: Token Budget Prediction

*(Unchanged — run after dry run passes, before real run. See formula below.)*

**Inputs:**
- `N` = task count from `bridge_dry_run` result
- `R` = rework rate: `0.20` warm/clear, `0.30` cold/complex, `0.40` TypeScript/Rust cold
- `C₀` = base context tokens (estimate from current context size + 2,000)
- `avg_request_tokens` = ~1,600 standard, ~800 simple, ~2,200 complex

**Formula:**
```
total_reviews    = N × (1 + R)
supervisor_input = (total_reviews × C₀)
                 + (1650 × total_reviews × (total_reviews + 1) / 2)
                 + (avg_request_tokens × total_reviews)
                 + (C₀ + 3500)          # planning call
                 + (final_context × 1.10) # stage5 overhead

supervisor_output = (N × 250) + (total_reviews × R × 120) + (total_reviews × (1-R) × 25) + 500

cache_rate = 0.80
new_input  = supervisor_input × (1 - cache_rate)
cached_input = supervisor_input × cache_rate
supervisor_cost_usd = (new_input × 3.00/1M) + (cached_input × 0.30/1M) + (supervisor_output × 15.00/1M)

without_bridge_cost = (N × 35000 × 3.00/1M) + (N × 2000 × 15.00/1M)
savings_pct = (without_bridge_cost - supervisor_cost_usd) / without_bridge_cost × 100
```

Show the budget table to the user before starting.

### Step 3-B: Real Run

Call **`bridge_run_plan`** in the background:

```
bridge_run_plan(
  plan_file         = "<bridge_root>/TASK_PLAN_active.json",
  repo_root         = "<REPO_ROOT>",
  goal              = "<goal>",
  manual_supervisor = true,
  aider_model          = "<model if set>",
  validation_command   = "<cmd if set>",
  auto_split_threshold = <N if set>,
  aider_no_map         = <true if set>
)
```

The tool returns `{ pid, log_file }` immediately. The bridge runs in the background.

**Do NOT omit `manual_supervisor: true`.** Without it the bridge auto-approves and skips Stage 4 review entirely. `manual_supervisor: true` is what makes Stage 4 work.

---

## Stage 4 — Manual Review Loop

With `manual_supervisor: true`, the bridge pauses after each task, writes a review request file, and polls for your decision file every 2 seconds.

Monitor the run with **`bridge_get_run_output`**:
```
bridge_get_run_output(lines=40)
```

Poll for new request files:
```bash
ls "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/" 2>/dev/null
```

When a new request appears, read it:
```bash
cat "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/task_0001_request.json"
```

Inspect the diff against the task instruction using Review Criteria from `references/pipeline.md`.

Write your decision:

**PASS:**
```bash
# Write to: <REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_0001_decision.json
```
```json
{ "decision": "pass" }
```

**REWORK:**
```json
{ "decision": "rework", "instruction": "exact symbol/line that is wrong and what to do instead" }
```

The bridge picks up the decision file and continues.

### After every PASS — refresh the graph

Immediately after writing a PASS decision, call:
```
mcp__code-review-graph__detect_changes_tool(repo_path="<REPO_ROOT>")
```

This is an **incremental** scan — it only re-indexes files that changed in the last commit (~2-3 seconds). It does not do a full rebuild.

**Why:** The PASS decision triggers a git commit. The committed changes shift line numbers and add/remove symbols. If the next task needs a REWORK instruction, you need the current line numbers — not the ones from session start. The incremental scan keeps the graph live throughout the run.

**Do not run this after REWORK** — a REWORK does not commit, so the graph is still correct.

### Mid-session REWORK learning

Every time you write a REWORK, immediately call **`memory_save`** (do not wait for Stage 5):

```
memory_save(
  content = "REWORK — <REPO_ROOT basename>\nFile: <file>\nTask: <id>\nFailed: <instruction 80 chars>\nReason: <why>\nPattern: <instruction_too_vague|wrong_file|missing_context|multi_step_task|model_capability|other>",
  type    = "procedural",
  tags    = ["repo:<basename>", "rework", "<file>"]
)
```

Do not wait for Stage 5 — save at the moment of the REWORK decision.

---

## Stage 5 — Checkpoint & Completion

### Step 5-A: Read all generated files

Call **`bridge_get_status`** for the summary:
```
bridge_get_status(repo_root="<REPO_ROOT>")
```

Call **`bridge_get_metrics`** for per-task detail:
```
bridge_get_metrics(repo_root="<REPO_ROOT>")
```

Then read these files directly for content that needs to be shown to the user verbatim:

**`RUN_REPORT.md`** — show this entire file to the user. It is the primary token accountability record.

**`RUN_DIAGNOSTICS.json`** — read `blocking_patterns[]` and surface actionable warnings:
- `interactive_prompt` → "Add those files to context_files next time"
- `timeout` → "Increase --task-timeout or switch to faster model"
- `silent_failure` → "Instructions need to name exact symbols"
- `supervisor_rework_loop` → "Acceptance criteria need to be clearer"
- `model_capability_gap` → "Switch to a larger model"

```bash
cat "<REPO_ROOT>/bridge_progress/RUN_REPORT.md"
cat "<REPO_ROOT>/bridge_progress/RUN_DIAGNOSTICS.json"
cat "<REPO_ROOT>/bridge_progress/token_log.json"
```

### Step 5-A-G: Full graph rebuild

After reading all bridge_progress files, do a full graph rebuild:

```
mcp__code-review-graph__build_or_update_graph_tool(repo_path="<REPO_ROOT>")
```

This is a **full rebuild**, not incremental. It picks up every structural change made during the run — new files, deleted files, renamed functions, shifted imports. It takes 10-30s depending on repo size.

**Why here and not just relying on Stage 4 incremental updates:** The incremental `detect_changes_tool` only rescans changed files. A full rebuild re-resolves all cross-file dependency edges. After a multi-task run where several files changed, some edges may be stale. The full rebuild at Stage 5 guarantees the next session opens with a completely accurate graph — no manual re-indexing ever needed.

Run this in the background while reading the report files — it does not block anything.

### Step 5-B: Confirm git commits

```bash
git -C "<REPO_ROOT>" log --oneline -<N>
```

Confirm each completed task has a corresponding commit. Warn if commits are missing.

### Step 5-C: Cleanup

```bash
rm -f "<REPO_ROOT>/taskJsons/TASK_PLAN_active.json"
```

Always clean up — leaving a stale plan causes the next session to load wrong tasks.

### Step 5-D: Present run summary

```
Run complete — <REPO_ROOT basename>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tasks:    <completed> / <planned>  (<skipped> skipped)
Reworks:  <N>
Commits:  <N>
Duration: <elapsed>s

Token usage:
  Supervisor (plan + review): <total> tokens
  Aider / Ollama (local):     ~<estimated> tokens (free)
  Session overhead:           <session_tokens> tokens
  Total cloud AI:             <total_ai> tokens

Savings vs direct Claude:    <tokens_saved> tokens (<savings_pct>%)
Blocking patterns:           <none / list>
Reports: <REPO_ROOT>/bridge_progress/
```

### Step 5-E: Prediction vs Actual (if Stage 3-T ran)

Compare predicted to actual from `token_log.json` and `last_run.json`. Show the table. Save calibration notes.

### Step 5-F: Save to memory

Call **`memory_save`** with the full run record:

```
memory_save(
  content = """Project: <REPO_ROOT basename>
Date: <today>
Goal: <user goal>
Status: <success/failure>
Tasks: <completed>/<planned>  |  Reworks: <N>  |  Skipped: <N>
Failed task: <id + type, or none>
Aider model: <model>
Flags: <non-default flags used>
Supervisor tokens: <N>
Aider tokens (estimated): <N>
Total AI tokens: <N>
Tokens saved vs direct: <N> (<pct>%)
Files that needed REWORK: <list or none>
Blocking patterns: <list or none>
Files modified: <list of files touched>
Notes: <ai_summary from RUN_DIAGNOSTICS>""",
  type = "episodic",
  tags = ["repo:<basename>", "bridge-run", "model:<model>", "status:<success|failure>"]
)
```

This costs ~150 tokens and eliminates re-discovery across all future sessions. Do not skip it.

---

## Hard Rules

- **Never write or edit code directly** — all `.ts`, `.py`, `.js`, `.cs` source files go through Aider via the bridge. **Boilerplate exception:** `package.json`, `tsconfig.json`, `.env.example`, `docker-compose.yml`, `AGENTS.md`, `.gitignore` may be written directly.
- **Always include `"goal"` at plan JSON root** — `{ "goal": "...", "tasks": [...] }`.
- **Always pass `manual_supervisor: true` to `bridge_run_plan`** — without it the bridge auto-approves and Stage 4 is bypassed entirely.
- **Never skip Stage 1** — call `bridge_health` before every run.
- **Never skip Stage 2** — user must see and approve the plan before any code is touched.
- **If a task fails 3+ times** → pause and execute the failure escalation procedure below.

### Failure Escalation Procedure (3+ failures)

1. Call `bridge_get_run_output` — find the failing task's output
2. Identify failure type (see `references/pipeline.md` → Failure Taxonomy)
3. Apply escalation:

| Failure type | Action |
|---|---|
| `interactive_prompt` | Add file Aider asked for to `context_files` |
| `timeout` | Split task or double `task_timeout` |
| `silent_failure` | Rewrite: name exact function/class to change |
| `repeated_validation_failure` | Rewrite `must_exist`/`must_contain` |
| `supervisor_rework_loop` | Clarify acceptance criteria |
| `model_capability_gap` | Switch to larger model — don't retry with same |
| `missing_dependency` | Add dependency as a preceding task |

4. Show revised instruction to user — ask to retry or skip
5. Save failure pattern via `memory_save` immediately

---

## Reference Files

- `references/pipeline.md` — Task JSON schema, MICRO-TASK rules, review criteria, failure taxonomy
- `references/flags.md` — All `main.py` flags, standard invocations, situational variants
