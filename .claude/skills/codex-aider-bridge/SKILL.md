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

On a cold repo with an unknown codebase, Aider is more likely to receive multi-file tasks it cannot handle well. Ask:

> *"Which Aider model will you be using?"*
> - 7B model (e.g. `qwen2.5-coder:7b`) → add `--auto-split-threshold 3` to Stage 3 (safer for small models)
> - 14B+ model → no split threshold needed
> - Not sure → default to `--auto-split-threshold 3` (safer)

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
| main.py exists | check `<bridge_root>/main.py` | file present |
| claude-mem running | `curl -s http://localhost:37777/health` | returns 200 or any response |

`bridge_root` is always the root of THIS repo (`codex-aider-bridge-app`), regardless of `REPO_ROOT`.

If Aider is missing: `pip install aider-chat`
If Ollama is not running: tell user to start Ollama (`ollama serve`) and select a model.

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

---

## Stage 1.6 — Run Code-Review-Graph (Always, Every Session Start)

Run the `code-review-graph` skill on `REPO_ROOT` automatically at the start of every session. Do not ask. Inform the user with one line:

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
   - **Highest:** `code-review-graph` output if user said YES in Stage 1.6
   - **Then:** `claude-mem` results from Stage 1.5 (past run patterns, known failures, model preferences)
   - **Then:** `bridge_progress/project_knowledge.json` in `REPO_ROOT` if present
   - **Fallback:** `git ls-files --others --cached --exclude-standard` in `REPO_ROOT` (cap at 300 lines)
2. Produce a JSON plan following the **exact schema** in `references/pipeline.md` → **Task Schema**
3. Apply the MICRO-TASK PROFILE rules (one file per task, surgical instructions, explicit assertions)
4. Apply any patterns learned from claude-mem (e.g. pre-split files that historically needed REWORK, apply known-good model preferences)
5. When `code-review-graph` context is available, include exact file paths and line numbers in instructions
6. Show the plan to the user as a numbered list — **not** raw JSON — and ask: *"Does this plan look right before I hand it to the executor?"*
7. If the user requests changes, revise. Once confirmed, save the plan to `<bridge_root>/TASK_PLAN_active.json`

---

## Stage 3 — Executor Handoff

Once the plan is confirmed, invoke the real bridge. Use the exact invocation pattern from `references/flags.md` → **Standard Invocation**.

The core command structure is:

```bash
python <bridge_root>/main.py \
  --repo-root "<REPO_ROOT>" \
  --plan-file "<bridge_root>/TASK_PLAN_active.json" \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve \
  --session-tokens <N>
```

Where `<N>` is your current token usage (use `/cost` or estimate from context size).

See `references/flags.md` for the full flag reference and situational variants.

**Do not add flags not in this reference without explicit user approval.** Wrong flags bypass validation or corrupt checkpoints.

---

## Stage 4 — Manual Review Loop

With `--manual-supervisor`, the bridge pauses after each task and writes a review request to:
```
<REPO_ROOT>/bridge_progress/manual_supervisor/<session_id>/review_request_<task_id>.json
```

For each task that completes:

1. Read the review request JSON (it contains the diff and task details)
2. Inspect the diff against the task instruction
3. Apply the review criteria from `references/pipeline.md` → **Review Criteria**
4. Write a decision JSON to the response path specified in the review request

**Decision: PASS**
```json
{ "decision": "approved", "notes": "brief note" }
```

**Decision: REWORK**
```json
{ "decision": "rework", "reason": "specific reason — name the exact issue" }
```

Be specific in rework reasons. Vague reasons cause the LLM to make random changes.

---

## Stage 5 — Checkpoint & Completion

After all tasks complete:

1. Check `<REPO_ROOT>/bridge_progress/task_metrics.json` — verify all task IDs show `"approved"` or `"success"`
2. If any task shows `"failed"` after max retries — report it to the user with the failure reason from `bridge_progress/`
3. The bridge auto-commits each approved task. Confirm with `git log --oneline -10` in `REPO_ROOT`
4. Delete `<bridge_root>/TASK_PLAN_active.json` (cleanup)
5. Report to the user: tasks completed, tasks failed, total git commits made

### Save run summary to claude-mem

If claude-mem is running, save a compact run note so future sessions start with context. Use the `claude-mem:mem-search` skill's save mechanism or write a structured note with this content:

```
Project: <REPO_ROOT basename>
Goal: <the original user goal>
Tasks: <N completed> / <N total>
Failed tasks: <list task IDs + failure type, or "none">
Models used: <aider model(s) that ran>
Flags that helped: <any non-default flags that improved results>
Files that needed REWORK: <list, or "none">
Notes: <anything unusual — quirks, workarounds, patterns observed>
```

This note costs ~100 tokens to save and saves hundreds per future session by eliminating re-discovery. Do not skip it if claude-mem is running.

---

## Hard Rules

These are non-negotiable. If you find yourself about to break one, stop and re-read this skill.

- **Never write or edit code directly** — all code changes go through Aider via the bridge
- **Never skip Stage 1 (setup checks)** — a broken Aider install will corrupt bridge_progress state silently
- **Never run main.py without `--manual-supervisor`** — without it, the bridge calls an external supervisor CLI and ignores you as reviewer
- **Never skip Stage 2 (simulate plan)** — the user must see and approve the task plan before any code is touched
- **Never use `--auto-split-threshold` unless the user asks** — it changes task granularity unpredictably on unknown repos
- **If a task fails 3+ times** → pause, read the failure log from `bridge_progress/`, diagnose, and propose a revised instruction to the user before retrying

---

## Reference Files

- `references/pipeline.md` — Task JSON schema, MICRO-TASK rules, review criteria, failure taxonomy
- `references/flags.md` — All `main.py` flags, standard invocations, situational variants
