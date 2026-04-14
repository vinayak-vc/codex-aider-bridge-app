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

After resolving `REPO_ROOT`, immediately check whether this is a **cold repo** (never run through the bridge before):

```bash
# Check for bridge state directory
ls <REPO_ROOT>/bridge_progress/
```

**If `bridge_progress/` does not exist → this is a COLD START.**

Proceed to **Stage 0-CS (Cold Start Protocol)** before Stage 1.
If it exists → normal flow, skip Stage 0-CS entirely.

**Note:** The **warm repo, cold user** check (new user on an existing bridged repo) is deferred to after Stage 1.5, because it depends on mem-search results. See the note at the end of Stage 1.5.

---

## Stage 0-CS — Cold Start Protocol

A cold repo has none of the bridge's infrastructure. Everything must be built from scratch before the first run. Work through this checklist in order — do not skip steps.

### CS-1: Confirm the repo is a git repository

```bash
git -C "<REPO_ROOT>" rev-parse --git-dir
```

If this fails → stop. Tell the user:
> *"The bridge requires a git repository. Please run `git init` in `<REPO_ROOT>` and make an initial commit before continuing."*

The bridge commits after every approved task. Without git, nothing is safe.

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

If unclear, ask. Getting this wrong means the wrong syntax checker runs after each task.

### CS-2b: Auto-detect validation command

After detecting the project type, scan for a validation command to pass as `--validation-command`. Check in this order:

| Project type | Files to check | Command to use |
|---|---|---|
| `python` | `pytest.ini`, `pyproject.toml` (`[tool.pytest]`), `setup.cfg` (`[tool:pytest]`) | `pytest --tb=short -q` |
| `python` | `pyproject.toml` (`[tool.ruff]`) | `ruff check .` |
| `javascript` / `typescript` | `package.json` → `scripts.test` key | `npm test` |
| `javascript` / `typescript` | `package.json` → `scripts.lint` key | `npm run lint` |
| `rust` | `Cargo.toml` present | `cargo check` |
| `go` | `go.mod` present | `go build ./...` |
| `csharp` | `*.sln` present | `dotnet build` |
| any | `.github/workflows/*.yml` → first `run:` step under a `test` job | extract that command |

Run the detection non-interactively:
```bash
# Example for python
cat <REPO_ROOT>/pytest.ini 2>/dev/null | head -5
cat <REPO_ROOT>/pyproject.toml 2>/dev/null | grep -A2 "tool.pytest"
# Example for node
node -e "const p=require('./<REPO_ROOT>/package.json'); console.log(p.scripts?.test||'')"
```

Once found, tell the user:
> *"Found validation command: `pytest --tb=short -q`. This will run after each task. Add `--validation-command 'pytest --tb=short -q'` to the Stage 3 invocation."*

If no validation command is found → tell the user:
> *"No test command detected. Tasks will run without post-task validation. You can add `--validation-command '<cmd>'` manually if you have a test suite."*

Store the detected command — it will be appended automatically to the Stage 3 invocation in this session.

### CS-3: Check for large non-code directories

```bash
du -sh <REPO_ROOT>/node_modules 2>/dev/null
du -sh <REPO_ROOT>/Library 2>/dev/null       # Unity
du -sh <REPO_ROOT>/.venv 2>/dev/null
du -sh <REPO_ROOT>/venv 2>/dev/null
```

If any of these exist and are larger than ~100MB → **add `--aider-no-map` to Stage 3 invocation automatically** and tell the user why:
> *"Found `node_modules/` (large). Adding `--aider-no-map` to prevent Aider from hanging during its initial repo scan."*

### CS-4: Run code-review-graph (mandatory on cold start)

On a cold repo you have zero prior knowledge — no `project_knowledge.json`, no claude-mem history, nothing. `code-review-graph` is the only way to get a precise map of the codebase before planning. **Run it automatically. Do not ask.**

Tell the user what you're doing:
> *"Cold start detected — running `code-review-graph` to map the codebase. This replaces the rough file tree with exact symbol locations and dependency edges, making the first plan significantly more accurate."*

Then invoke the `code-review-graph` skill on `REPO_ROOT`.

Use its output as the primary repo context in Stage 2 — it replaces `git ls-files` entirely for this first run. Reference exact `file:line` locations in every task instruction where the graph reveals them.

**Do not skip this step on cold start, even if the user says to hurry.** A bad first plan wastes far more time than 45 seconds of indexing. If the graph fails for any reason (unsupported language, tool unavailable), fall back to `git ls-files` and note it.

### CS-5: Tell the user what the first run will do automatically

Before Stage 1, brief the user so there are no surprises:

> **First-run behaviour (automatic):**
> - The bridge will scan up to 500 source files to build `project_knowledge.json` — this is a one-time cost (~20 seconds)
> - It will create `<REPO_ROOT>/bridge_progress/` to store checkpoints and run state
> - All changes will be committed to git after each approved task — your working tree must be clean before we start
>
> **Checking working tree...**

```bash
git -C "<REPO_ROOT>" status --porcelain
```

If the working tree is dirty → stop and tell the user:
> *"Your working tree has uncommitted changes. Please commit or stash them first — the bridge commits after each task and needs a clean baseline to produce meaningful diffs."*

Only continue once the tree is clean.

### CS-6: Determine `--auto-split-threshold`

On a cold repo with an unknown codebase, Aider is more likely to receive multi-file tasks it cannot handle well. Ask the user now (Stage 1.5 hasn't run yet so there is no memory to draw from):

> *"Which Aider model will you be using for this repo?"*
> - 7B model (e.g. `qwen2.5-coder:7b`) → note `--auto-split-threshold 3` for Stage 3 (safer for small models)
> - 14B+ model → no split threshold needed
> - Not sure → default to `--auto-split-threshold 3` (safer)

Store the model name — it will be used as `--aider-model` in Stage 3 and saved to claude-mem in Stage 5-E so future cold starts on this repo won't need to ask.

**Note:** If Stage 1.5 mem-search later finds a stronger model preference, it will override this choice. Stage 1.5 always wins.

---

Once all CS steps are complete → proceed to Stage 1 (setup checks) as normal.
The cold start protocol runs **once per repo**. After the first successful run, `bridge_progress/` exists and all future sessions skip straight to Stage 1.

---

## Stage 1 — Setup Checks

Run these checks before any planning. If any fail, stop and help the user fix them before continuing.

See `references/flags.md` → **Setup Checks** section for the exact commands to run.

| Check | Command | Pass condition |
|---|---|---|
| Python available | `python --version` | Python 3.9+ |
| Aider installed | `aider --version` | exits 0 |
| Ollama running | `ollama list` | exits 0, shows at least one model |
| main.py exists | `ls main.py` | file present in cwd |
| bridge-memory-service running | `curl -s http://localhost:3000/health` | returns `{"status":"ok"}` |
| claude-mem running | `curl -s http://localhost:37777/health` | returns 200 or any response |

`bridge_root` = **current working directory (`cwd`)**. The skill always runs from inside the bridge repo. Never navigate to a parent directory. Never search for `main.py` with `find`. All bridge commands use `python main.py` (not `python <full_path>/main.py`).

If Aider is missing: `pip install aider-chat`
If Ollama is not running: tell user to start Ollama (`ollama serve`) and select a model.

**If bridge-memory-service is not running:**
The bridge automatically enhances every task instruction with memory context and ingests results after each task — but silently degrades if the service is unavailable (original instruction is used, ingest is skipped with a warning). To start the service:
```bash
cd H:/Ai_Project/memory/bridge-memory-service && npm run dev
```
To use a non-default URL, set `MEMORY_SERVICE_URL` before running the bridge:
```bash
set MEMORY_SERVICE_URL=http://localhost:3000   # Windows
export MEMORY_SERVICE_URL=http://localhost:3000  # Unix
```
Do not block on this — if the service is down the bridge still runs normally.

**If claude-mem is not running:**
Tell the user:

> This skill uses **claude-mem** to track run history, surface past failures, and reduce repeated context-loading across sessions — keeping token usage low over time.
>
> To set it up (one-time):
> ```bash
> npx claude-mem install
> ```
> Then restart Claude Code. claude-mem starts automatically on session open.
>
> If you prefer not to install it now, the skill will still work — but each session starts cold with no memory of past runs.

Do not block on claude-mem being absent. If it's not installed, note it and continue. If it IS running, proceed to Stage 1.5.

---

## Stage 1.5 — Memory Retrieval (claude-mem)

This stage runs automatically — no user decision needed. Inform the user with a single line:

> *"Checking session memory for past runs on this project..."*

Then use the `claude-mem:mem-search` skill to query for relevant history. Run these two queries:

**Query 1 — Past bridge runs on this project:**
```
bridge run <REPO_ROOT basename> task failures rework
```

**Query 2 — Known patterns for this project:**
```
aider model failure pattern <REPO_ROOT basename>
```

**What to do with the results:**

| What mem-search returns | How to use it in Stage 2 |
|---|---|
| Previous task plans that succeeded | Reuse their instruction style and task structure |
| Known failure types for specific files | Pre-split those tasks more aggressively |
| Models that performed well on this repo | Set as preferred `--aider-model` in Stage 3 |
| Files that repeatedly needed REWORK | Flag them in the plan with a note |
| Repo-specific quirks (e.g. "always use --aider-no-map") | Apply those flags automatically in Stage 3 |

If mem-search returns nothing (first run, or claude-mem not installed), silently continue — Stage 2 proceeds on `git ls-files` + `project_knowledge.json` alone.

**After the run completes (end of Stage 5):** use `claude-mem` to save a brief run summary — what worked, what failed, which model was used, any flags that helped. This feeds future sessions automatically.

### Warm Repo, Cold User Check (run after mem-search)

Now that mem-search results are available, check if this is a **warm repo with a new user** (prior bridge runs exist but no session memory yet):

**Conditions:**
- `bridge_progress/last_run.json` exists (bridge has run before), AND
- mem-search returned zero results for this project

If both conditions are met → read prior run context and brief the user:

```bash
# Read last run summary
cat <REPO_ROOT>/bridge_progress/last_run.json
```

> *"This repo has been run through the bridge before — `<N>` tasks completed as of `<last_run_date>`. I have no session memory for it yet. Here's what I found:*
> *— Status: `<status>`, Tasks: `<completed>/<planned>`, Model: `<aider_model>`*
> *I'll use this as my starting context for planning."*

Also read `bridge_progress/task_metrics.json` to understand the prior run's scope. Use this data as additional Stage 2 context alongside code-review-graph output.

At end of session (Stage 5-E): save the full run record to claude-mem so this session won't be cold next time.

---

## Stage 1.6 — Run Code-Review-Graph (Every Session Start, Except Cold Start)

**Skip this stage if this is a cold start** — CS-4 already ran the graph. Proceed directly to Stage 2.

For all other sessions, run the `code-review-graph` skill on `REPO_ROOT` automatically. Do not ask. Inform the user with one line:

> *"Indexing `<REPO_ROOT>` with code-review-graph..."*

Use the graph output as the primary repo context in Stage 2 — it replaces `git ls-files` entirely. Reference exact `file:line` locations in task instructions wherever the graph reveals them.

**Important — graph is a session-start snapshot, not live:**
The graph reflects the repo at the moment it runs. Once Aider begins committing changes in Stage 3-4, the graph becomes stale for the current session — this is fine because the plan is already locked by then. At the start of the **next** session, the graph runs again and picks up everything Aider committed.

Do not re-run the graph between tasks mid-session. It is always fresh at session open, always stale by session end.

If the graph fails (tool unavailable, unsupported language) → fall back to `git ls-files` silently and note it.

---

## Stage 2 — Simulate Planning (Supervisor Role)

Before invoking the real executor, YOU produce the task plan. This is the simulation phase.

**Why simulate first?** The real executor calls the supervisor LLM via subprocess. When running inside Claude Code, YOU are the supervisor — producing the plan here saves a full LLM round-trip and gives the user a chance to review and adjust before any code is touched.

### How to produce the plan

1. Get repo context, layered in priority order:
   - **Highest:** `code-review-graph` output from Stage 1.6 (always runs — use it as primary source)
   - **Then:** `claude-mem` results from Stage 1.5 (past run patterns, known failures, model preferences)
   - **Then:** `bridge_progress/project_knowledge.json` in `REPO_ROOT` if present
   - **Fallback:** `git -C "<REPO_ROOT>" ls-files` (all tracked files, cap at 300 lines)
2. Produce a JSON plan following the **exact schema** in `references/pipeline.md` → **Task Schema**
3. Apply the MICRO-TASK PROFILE rules (one file per task, surgical instructions, explicit assertions)
4. Apply any patterns learned from claude-mem (e.g. pre-split files that historically needed REWORK, apply known-good model preferences)
5. When `code-review-graph` context is available:
   - Include exact `file:line` locations in every instruction
   - **Auto-populate `context_files` using dependency edges:** for each task's target file, look up its import/dependency edges in the graph. Any file it directly imports or that directly imports it is a candidate for `context_files`. Add the top 1–3 most relevant ones (ignore third-party packages, only include repo-local files). This prevents Aider from asking for file confirmation mid-run.
6. Set per-task `"model"` field based on complexity:
   - `"model": "ollama/qwen2.5-coder:7b"` — single-class utility file with no complex imports
   - `"model": "ollama/qwen2.5-coder:14b"` — multi-import orchestration, complex logic, or files that wire multiple components
   - `"model": null` — inherit bridge default
7. Show the plan to the user as a numbered list — **not** raw JSON — and ask: *"Does this plan look right before I hand it to the executor?"*
8. If the user requests changes, revise. Once confirmed, save the plan to `TASK_PLAN_active.json` (in cwd = bridge_root). **Always include `"goal"` at root level** — `{ "goal": "...", "tasks": [...] }`.

---

## Stage 2.5 — Plan Self-Check (MICRO-TASK Validation)

Before saving `TASK_PLAN_active.json` and handing off to the executor, run this checklist against **every task** in the plan. Fix violations automatically — do not ask the user for each one unless the fix requires domain knowledge you don't have.

### Checklist (run per task)

| Rule | Check | Auto-fix |
|---|---|---|
| **One file per task** | `files[]` must have exactly 1 entry | If 2–3 files: split into separate tasks. If 4+ files: warn user before proceeding |
| **Word count** | `instruction` must be ≥ 15 words and ≤ 120 words | Too short: expand with exact symbol names and expected outcome. Too long: strip narrative, keep only the imperative. |
| **No banned verbs** | `instruction` must not start with: "Refactor", "Clean up", "Improve", "Update", "Fix" (alone, without specifics) | Rewrite with a precise imperative: what to add/remove/change and where |
| **Exact location** | If graph context is available: every `instruction` must name at least one `file:line` or function name | Inject the exact location from graph output |
| **`must_exist` for create tasks** | If `instruction` says "create" or "add a new file": `must_exist[]` must list the new file path | Add the expected output path to `must_exist` |
| **File exists for modify tasks** | If task modifies an existing file: confirm that file appears in `git ls-files` or graph output | If file not found: flag to user — task may target a non-existent file |
| **No "and" in instructions** | `instruction` must not describe two independent actions joined by "and" | Split into two tasks |

### Self-check output format

After checking, report a single status line before saving the plan:

> **Plan self-check:** 7 tasks checked — 2 split, 1 instruction rewritten, 0 blockers. Ready to save.

If any blockers were found (e.g. file not found, split would create 5+ tasks):

> **Plan self-check:** BLOCKED — Task 3 targets `src/foo.rs` which does not exist in the repo. Please confirm the correct path before proceeding.

Only proceed to Stage 3 once the plan is blocker-free.

---

## Stage 3 — Executor Handoff

**`bridge_root` = cwd.** All `python main.py` commands run from the current working directory — never change directory, never use absolute paths to `main.py`. If cwd doesn't contain `main.py`, stop and tell the user to open Claude Code from inside the bridge repo.

Once the plan is confirmed, **always run a dry-run first** before the real executor. This validates the plan JSON against the bridge's schema and catches flag errors before any code is touched.

### Step 3-A: Dry run

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --dry-run
```

The `--dry-run` flag makes the bridge parse and validate the plan without executing any tasks or calling Aider.

**If dry run fails:**
- Read the error output — it will identify the specific task or field that is invalid
- Fix the plan JSON (or the task instruction) and re-run Stage 2.5 self-check
- Re-run the dry run until it passes
- Do not proceed to the real run until the dry run exits 0

**If dry run passes:**
> *"Dry run passed — plan JSON is valid. Starting real run now."*

Then proceed to Stage 3-T (token budget), then Stage 3-B.

---

## Stage 3-T — Token Budget Prediction

Run this immediately after the dry run passes, before the real run. It takes 30 seconds and gives the user a cost estimate upfront.

### How to calculate

**Inputs you need:**
1. `N` = number of tasks in the plan (after removing boilerplate tasks you wrote directly)
2. `R` = expected rework rate:
   - `0.20` — warm repo, familiar codebase, clear instructions
   - `0.30` — cold repo, new language stack, or complex multi-import tasks
   - `0.40` — TypeScript/Rust on a cold repo with no prior graph context
3. `C₀` = base context at Stage 4 start = run `/cost` after dry run and take that token count + ~2,000 (for the real run startup overhead)
4. `avg_request_tokens` = ~1,600 (standard diff-bearing task). Use ~800 for simple create tasks, ~2,200 for complex multi-file tasks.

**Formula:**

```
total_reviews    = N × (1 + R)            # round up to nearest integer
context_growth   = 1,650                   # tokens added to context per review
supervisor_input = (total_reviews × C₀)
                 + (context_growth × total_reviews × (total_reviews + 1) / 2)
                 + (avg_request_tokens × total_reviews)
                 + planning_call_tokens    # ≈ C₀ + 3,500 (scaffold + plan prompt)
                 + stage5_overhead_tokens  # ≈ final_context × 1.10

supervisor_output = (N × 250)             # plan JSON output
                  + (total_reviews × R × 120)   # rework instructions
                  + (total_reviews × (1-R) × 25) # pass decisions
                  + 500                   # Stage 5 wrap-up

cache_rate = 0.80  # SKILL.md stays in prompt cache; adjust down for short sessions
new_input  = supervisor_input × (1 - cache_rate)
cached_input = supervisor_input × cache_rate

supervisor_cost_usd = (new_input    × 3.00 / 1_000_000)
                    + (cached_input × 0.30 / 1_000_000)
                    + (supervisor_output × 15.00 / 1_000_000)

aider_input  = N × avg_aider_prompt_tokens   # ~4,000 simple, ~7,500 complex, ~6,000 avg
aider_output = N × avg_aider_output_tokens   # ~800 simple, ~2,500 complex, ~1,800 avg
aider_retries_tokens = (N × R) × (aider_input / N + aider_output / N)
aider_total  = aider_input + aider_output + aider_retries_tokens
aider_cost   = $0  (local Ollama)

without_bridge_cost = (N × 35_000 × 3.00 / 1_000_000)   # Claude writing directly
                    + (N × 2_000  × 15.00 / 1_000_000)   # output per file
savings_pct  = (without_bridge_cost - supervisor_cost_usd) / without_bridge_cost × 100
```

### Output format (show this to the user before starting)

```
Token Budget Estimate — <REPO_ROOT basename>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Plan:        <N> tasks  (expected <total_reviews> reviews at <R×100>% rework rate)
Base context at Stage 4: ~<C₀> tokens

Supervisor (Claude):
  Total input:   ~<supervisor_input> tokens
    cached:      ~<cached_input>  (~80%)
    new:         ~<new_input>
  Total output:  ~<supervisor_output> tokens
  Estimated cost: ~$<supervisor_cost_usd>

Aider / Ollama (local):
  Estimated tokens: ~<aider_total> (free — local GPU)
  Est. GPU time:    ~<N × avg_minutes_per_task> min

Without bridge (Claude direct):  ~$<without_bridge_cost>
Bridge savings estimate:          ~<savings_pct>%

[These are pre-run estimates. Actual numbers in bridge_progress/token_log.json after run.]
```

Save the predicted values — you will compare them to actuals in Stage 5-E.

### Step 3-B: Real run

Once the plan is confirmed and the dry run passes, invoke the real bridge. Use the exact invocation pattern from `references/flags.md` → **Standard Invocation**.

The core command structure is:

```bash
python main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --session-tokens <N>
```

Where `<N>` is your current token usage (use `/cost` or estimate from context size).

**Do NOT add `--auto-approve`.** Without it, the bridge writes a review request file after each task and waits for your decision file before continuing. This is Stage 4's review loop. `--auto-approve` bypasses all review and must never be used.

**Per-task model override:** Use the `"model"` field in the plan JSON instead of the global `--aider-model` flag:
- `"model": "ollama/qwen2.5-coder:7b"` — simple single-class utility files
- `"model": "ollama/qwen2.5-coder:14b"` — complex multi-import orchestration files
- `"model": null` — inherit the bridge default

See `references/flags.md` for the full flag reference and situational variants.

**Do not add flags not in this reference without explicit user approval.** Wrong flags bypass validation or corrupt checkpoints.

---

## Stage 4 — Manual Review Loop

With `--manual-supervisor` (no `--auto-approve`), the bridge pauses after each task and writes a review request file. It then polls every 2 seconds waiting for your decision file. The bridge is **blocking** — run it in the background so you can write decision files in parallel.

### How to run and review simultaneously

**Step 1 — Start the bridge in background:**
Use `run_in_background: true` on the Bash tool call so you can continue acting while it runs.

**Step 2 — After each task, bridge writes a request file:**
```
<REPO_ROOT>/bridge_progress/manual_supervisor/requests/task_XXXX_request.json
```
Where `XXXX` is the zero-padded task ID (e.g. `task_0001_request.json`). If `--relay-session-id` is set, the filename includes it: `task_0001_<session_id>_request.json`.

Poll for new request files:
```bash
ls "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/" 2>/dev/null
```

**Step 3 — Read the review request:**
```bash
cat "<REPO_ROOT>/bridge_progress/manual_supervisor/requests/task_0001_request.json"
```
The JSON contains: `task_id`, `instruction`, `diff`, `execution`, `validation`, `files`, and `response_schema`.

**Step 4 — Inspect the diff** against the task instruction using Review Criteria from `references/pipeline.md`.

**Step 5 — Write your decision file:**

Decision path (derived by convention — same task ID, different directory):
```
<REPO_ROOT>/bridge_progress/manual_supervisor/decisions/task_XXXX_decision.json
```

PASS:
```json
{ "decision": "pass" }
```

REWORK:
```json
{ "decision": "rework", "instruction": "specific instruction — name the exact symbol/line/value that is wrong and what to do instead" }
```

The bridge polls the decisions directory every 2 seconds and picks up the file automatically.

Be specific in rework reasons. Vague reasons cause the LLM to make random changes.

### Mid-session REWORK learning

**Every time you write a REWORK decision**, immediately save the pattern to claude-mem (do not wait for Stage 5). This ensures the pattern is captured even if the session ends early or the run is aborted.

Save this record to claude-mem right after writing the REWORK JSON:

```
REWORK — <REPO_ROOT basename>
File: <task target file>
Task: <task_id>
Instruction that failed: <original instruction, first 80 chars>
Why it failed: <your rework reason>
Pattern: <one of: instruction_too_vague | wrong_file | missing_context | multi_step_task | model_capability | other>
```

Do not save this at the end of the session — save it **at the moment of the REWORK decision**. Future sessions on this repo will find this immediately via Stage 1.5 mem-search and can pre-split or rewrite similar tasks before they fail.

---

## Stage 5 — Checkpoint & Completion

The bridge auto-generates rich output files after every run. Read them all — do not skip any. They contain everything the user needs to see and everything claude-mem needs to remember.

### Step 5-A: Read all generated files

Read these files from `<REPO_ROOT>/bridge_progress/` in this order:

**1. `task_metrics.json`**
- Verify `status` is `"success"` or `"failure"`
- Extract: `completed_task_ids`, `failed_task_id`, `planned_tasks`, `skipped_tasks`, `diffs_recorded`
- If any task shows `"failed"` → extract the failure reason and report it

**2. `RUN_REPORT.md`** ← the main token report, surface this directly to the user
- Contains: supervisor token breakdown (plan/review/subplan in+out), Aider estimated tokens, total AI tokens, session overhead
- Contains: savings comparison — tokens used WITH bridge vs estimated WITHOUT bridge
- Contains: per-task token usage table
- **Show this entire file to the user verbatim.** It is the primary accountability record for every token spent.

**3. `RUN_DIAGNOSTICS.json`**
- Read `blocking_patterns[]` — if any patterns were detected, surface them as actionable warnings:
  - `interactive_prompt` → "Aider asked for file confirmation — add those files to context_files next time"
  - `timeout` → "Tasks timed out — consider increasing --task-timeout or switching to a faster model"
  - `silent_failure` → "Tasks exited 0 with no changes — instructions need to be more specific"
  - Empty-file guardrail: do not treat `**/__init__.py`, `**/.gitkeep`, or `**/.keep` as silent-failure evidence when they are intentionally empty.
  - `supervisor_rework_loop` → "Multiple reworks on same task — acceptance criteria need to be clearer"
  - `model_capability_gap` → "Syntax errors in output — consider using a larger model"
- Read `ai_summary` — include this in the claude-mem note
- Read per-task `attempts[]` counts — any task with 3+ attempts is worth flagging

**4. `last_run.json`**
- Quick confirmation of status, tasks executed, elapsed seconds, token totals

**5. `token_log.json`** → read the latest entry in `sessions[]`
- Extract: `savings.savings_percent`, `savings.tokens_saved`, `cost.bridge_cost_sonnet`, `cost.savings_dollar_sonnet`
- Extract: `aider.reworks`, `aider.estimated_tokens`, `session.total_ai_tokens`
- Surface the savings % to the user — this is the headline metric

**6. `LATEST_REPORT.md`**
- Quick status overview, confirm it matches task_metrics.json

### Step 5-B: Confirm git commits

```bash
git -C "<REPO_ROOT>" log --oneline -<N>   # N = number of completed tasks
```

Confirm each completed task has a corresponding commit. If commits are missing → warn the user.

### Step 5-C: Cleanup

Remove the active plan file regardless of run outcome (success, failure, or abort):

```bash
rm -f "<bridge_root>/TASK_PLAN_active.json"
```

If the run failed mid-way and `TASK_PLAN_active.json` already contains a partial/broken plan, leaving it would cause the next session to load stale tasks. Always clean it up before ending the session.

### Step 5-D: Present the run summary to user

Surface a clean summary. Use actual numbers from the files above — no estimates:

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
  Your session overhead:       <session_tokens> tokens
  Total cloud AI:              <total_ai> tokens

Savings vs doing this without the bridge:
  Estimated direct cost:  <estimated_direct> tokens
  Actual cost:            <total_ai> tokens
  Saved:                  <tokens_saved> tokens (<savings_pct>%)

Blocking patterns detected: <none / list>
Reports written to: <REPO_ROOT>/bridge_progress/
```

### Step 5-E: Prediction vs Actual Comparison

If you ran Stage 3-T before the run, compare predicted to actual now.

Read actuals from `bridge_progress/token_log.json` (latest entry in `sessions[]`) and `bridge_progress/last_run.json`.

**Comparison table — show this to the user:**

```
Token Budget: Predicted vs Actual
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        Predicted       Actual      Delta
Supervisor input:       ~<P>            <A>         <sign><diff> (<pct>%)
Supervisor output:      ~<P>            <A>         <sign><diff>
Supervisor cost:        ~$<P>           $<A>        <sign>$<diff>
Aider tokens (local):   ~<P>            <A>         <sign><diff>
Actual rework rate:     <P_R>%          <A_R>%      ──
Bridge savings:         ~<P_S>%         <A_S>%      ──
```

**Calibration notes to save for next run:**
- If actual input > predicted by >20%: rework rate was underestimated. Raise `R` for next plan.
- If actual input < predicted by >20%: tasks were simpler than expected. Lower `R`.
- If actual Aider tokens >> predicted: tasks were too large (multi-file), split more aggressively next time.
- If bridge savings < 50%: supervisor context is too large. Consider splitting the run into shorter sessions.

Save these calibration notes to claude-mem alongside the run record (Step 5-F below).

### Step 5-F: Save to claude-mem

If claude-mem is running, save the full run record. Include actual numbers — not vague summaries. This is what future sessions will search:

```
Project: <REPO_ROOT basename>
Date: <today>
Goal: <the original user goal>
Status: <success / failure>
Tasks: <completed> / <planned>  |  Reworks: <N>  |  Skipped: <N>
Failed task: <task_id + failure_type, or "none">
Aider model: <model used>
Flags: <non-default flags that ran>
Supervisor tokens: <N> (plan: <N> in/<N> out, review: <N> in/<N> out)
Aider tokens (estimated): <N>
Total AI tokens: <N>
Tokens saved vs direct: <N> (<pct>%)
Files that needed REWORK: <list or "none">
Blocking patterns: <list from RUN_DIAGNOSTICS or "none">
Files modified: <list of files touched by completed tasks>
Notes: <ai_summary from RUN_DIAGNOSTICS.json>
```

This note costs ~150 tokens to save and eliminates re-discovery across every future session on this project. Do not skip it.

---

## Hard Rules

These are non-negotiable. If you find yourself about to break one, stop and re-read this skill.

- **Never write or edit code directly** — all code changes go through Aider via the bridge. **Boilerplate exception:** you may write `package.json`, `tsconfig.json`, `.env.example`, `docker-compose.yml`, `AGENTS.md`, `.gitignore` directly — these are pure config with no imports. Every `.ts`, `.py`, `.js`, `.cs` source file must go through bridge even if trivial, because other files depend on its API.
- **Always include `"goal"` at plan JSON root** — `{ "goal": "...", "tasks": [...] }`. The bridge onboarding scanner uses it.
- **Never use `--auto-approve`** — it bypasses all review. Stage 4 file-based review IS the review loop.
- **Never run main.py without `--manual-supervisor`** — without it, the bridge calls an external supervisor CLI and ignores you as reviewer
- **Never skip Stage 1 (setup checks)** — a broken Aider install will corrupt bridge_progress state silently
- **Never skip Stage 2 (simulate plan)** — the user must see and approve the task plan before any code is touched
- **Never navigate away from cwd to run main.py** — bridge_root = cwd always
- **Never use `--auto-split-threshold` unless the user asks, or CS-6 set it automatically** — it changes task granularity unpredictably on unknown repos; the only automatic exception is CS-6 on cold start where the model size is known
- **If a task fails 3+ times** → pause the run and execute the failure escalation procedure below

### Failure Escalation Procedure (3+ failures on same task)

1. Read `bridge_progress/RUN_DIAGNOSTICS.json` → find the failing task's `attempts[]` array
2. Identify the failure type using the taxonomy in `references/pipeline.md` → **Failure Taxonomy**
3. Apply the escalation action for that type:

| Failure type | Escalation action |
|---|---|
| `interactive_prompt` | Add the file Aider asked for to `context_files` in the task instruction |
| `timeout` | Split the task into 2 smaller tasks. If already 1 file, add `--task-timeout <2x current>` |
| `silent_failure` | Rewrite instruction: name the exact function/class/symbol to add or change |
| `repeated_validation_failure` | Rewrite assertion in `must_exist` / `must_contain`. If syntax error: add `--validation-command` to catch it earlier |
| `supervisor_rework_loop` | Clarify the acceptance criteria — what does "done" look like exactly? Add a `must_contain` check |
| `model_capability_gap` | Tell user: switch to a larger model (`--aider-model`). Do not retry with the same model |
| `missing_dependency` | Add the dependency as a preceding task in the plan. Do not assume the file exists |

4. Show the revised instruction to the user:
   > *"Task `<id>` has failed 3 times. Failure type: `<type>`. Here is a revised instruction:*
   > `<new instruction>`
   > *Shall I update the plan and retry, or abort this task?"*

5. If user approves → update `TASK_PLAN_active.json` with the revised instruction and continue the run
6. If user declines → mark the task as skipped and continue with remaining tasks
7. Save the failure pattern to claude-mem immediately (same format as Stage 4 REWORK learning)

---

## Reference Files

- `references/pipeline.md` — Task JSON schema, MICRO-TASK rules, review criteria, failure taxonomy
- `references/flags.md` — All `main.py` flags, standard invocations, situational variants
