# Using the `codex-aider-bridge` Skill (Antigravity Edition)

The skill is committed to `.gemini/skills/codex-aider-bridge/` in this repo.
Any teammate who opens this repo in **Antigravity** gets it automatically — no install needed.

---

## Prerequisites

Before using the skill for the first time, make sure you have:

| Requirement | Install command |
|---|---|
| Python 3.9+ | [python.org](https://python.org) |
| Aider | `pip install aider-chat` |
| Ollama | [ollama.com](https://ollama.com) — then `ollama serve` |
| At least one Ollama model | `ollama pull qwen2.5-coder:7b` |

### About Knowledge Items (KIs)

Antigravity uses **Knowledge Items** to keep token usage low across sessions. KIs are stored automatically and remember:
- Which tasks failed and why, per project
- Which Aider models performed well
- Which files historically needed REWORK
- Flags and patterns that improved results

Unlike Claude's `claude-mem`, KIs are built-in — no separate service to install or run. They just work.

---

## How to Trigger the Skill

The skill activates automatically when you phrase a request as a **goal** (not a direct edit).

### Trigger phrases that work

```
"use the bridge to implement X"
"implement feature X"
"build X"
"/build add dark mode toggle"
"add X to the project"
"fix the bug where X"
"use the bridge to refactor X"
```

### What NOT to say (bypasses the skill)

```
"edit line 42 in aider_runner.py"       ← too direct, Antigravity edits inline
"open executor/task_ir.py and change X" ← same
```

If Antigravity starts editing files directly without going through the pipeline, remind it:
> "Use the bridge skill — don't edit directly."

---

## What Happens After You Trigger It

The skill walks Antigravity through these stages automatically:

### Stage 0 — Repo resolution
Antigravity asks which project to run the bridge against if it's not clear from context.

**If working on this repo itself:** just say the goal, no path needed.
**If targeting another project:** include the path in your message:
> "use the bridge to implement X in `H:\path\to\my-project`"

### Stage 1 — Setup checks
Antigravity verifies Python, Aider, and Ollama are working before touching anything.
If something is broken, it stops and tells you exactly what to fix.

### Stage 1.6 — Code-Review-Graph (automatic, every session start)

Antigravity uses its built-in MCP `code-review-graph` tools automatically. You'll see one line:

> *"Indexing `<repo>` with code-review-graph..."*

No prompt, no choice. The graph replaces `git ls-files` so every task instruction in Stage 2 references exact `file:line` locations instead of guesses.

**What it gives Antigravity:**
- Every function, class, and module with exact file:line locations
- What each symbol depends on (imports, calls)
- What depends on each symbol — changing X reveals everything that breaks

**Does it update automatically?**
Yes — incrementally at each session start. During the run, Aider makes commits and the graph goes stale — but that's fine because the plan is already locked. Next session, the graph re-indexes and picks up everything.

---

### Stage 2 — Plan preview (you approve before any code runs)
Antigravity produces a numbered task plan — e.g.:

```
Task 1 [modify] executor/aider_runner.py
  → Add parameter `max_retries` (int, default=3) to AiderRunner.__init__

Task 2 [modify] executor/aider_runner.py
  → In AiderRunner.run(), add retry loop using self.max_retries

Task 3 [validate] executor/aider_runner.py
  → must_exist: executor/aider_runner.py
```

Antigravity asks: **"Does this plan look right before I hand it to the executor?"**
You can say "looks good", request changes, or ask it to split/merge tasks.
No code is touched until you approve.

### Stage 3 — Executor handoff
Antigravity runs:
```powershell
python main.py `
  --repo-root "<your project>" `
  --plan-file TASK_PLAN_active.json `
  --manual-supervisor `
  --workflow-profile micro
```

Aider (your local LLM) implements each task one at a time.

### Stage 4 — Diff review loop
After each task, Antigravity reads the git diff and decides:
- **PASS** → bridge moves to the next task
- **REWORK** → Antigravity gives Aider a specific correction and it retries

You watch this happen in real time. You can also intervene:
> "That diff looks wrong to me — rework it, the function signature is missing the default value."

### Stage 5 — Done
Antigravity reports: how many tasks completed, how many failed, how many git commits were made, token savings vs direct editing.

---

## Example Session

```
You:    use the bridge to add a --timeout flag to main.py that limits
        total run time and aborts with exit code 124 if exceeded

Antigravity: [Stage 1] Checks Python ✓, Aider ✓, Ollama ✓
             [Stage 1.6] Indexing with code-review-graph... done
             [Stage 2] Here's my plan (3 tasks)...
                       Task 1: add --timeout arg to argument parser in main.py
                       Task 2: pass timeout to AiderRunner in main.py
                       Task 3: enforce timeout in AiderRunner.run() loop
                       Does this look right?

You:    looks good

Antigravity: [Stage 3] Running executor...
             [Stage 4] Task 1 diff: ✓ PASS
                       Task 2 diff: ✓ PASS
                       Task 3 diff: ✗ REWORK — timeout not enforced in the loop,
                                    only passed as a parameter. Adding enforcement.
                       Task 3 retry: ✓ PASS
             [Stage 5] 3/3 tasks complete, 3 commits made.
                       Saved ~66% tokens vs doing this without the bridge.
```

---

## The `/build` Shortcut

For quick tasks, use the `/build` command. It classifies each change as tiny/medium/large:

```
You:    /build add maxVideos control to upload options --project D:\MyProject
```

Antigravity will:
1. **TINY tasks** (< 50 lines, simple config) → edit directly, no bridge needed
2. **MEDIUM tasks** (50-500 lines) → delegate to bridge + Ollama 7b
3. **LARGE tasks** (500+ lines) → delegate to bridge + Ollama 14b

This is the fastest path — tiny tasks complete in seconds, not minutes.

---

## Using the Bridge on a Fresh Repo (Cold Start)

If the target project has **never been run through the bridge** — no `bridge_progress/` folder — the skill detects this automatically and runs a one-time cold start setup.

### What "cold" means

| What's missing | Why it matters |
|---|---|
| `bridge_progress/` | No checkpoints → a crash means starting over |
| `project_knowledge.json` | Supervisor has no context → plans will be vague |
| Project type | Wrong validator runs after each task |
| Git cleanliness check | Bridge commits after every task — dirty tree = corrupted diffs |

### What the skill does automatically

**Step 1 — Confirms git exists**
If the folder isn't a git repo, stops and tells you to run `git init` + initial commit.

**Step 2 — Detects project type**
Looks for markers (`package.json`, `Cargo.toml`, `*.sln`, `pyproject.toml`, etc.) and tells you what it found.

**Step 3 — Checks for large non-code directories**
If it finds `node_modules/`, `Library/` (Unity), `.venv/`, etc. it automatically adds `--aider-no-map`.

**Step 4 — Runs code-review-graph automatically (no prompt)**
Uses the MCP `code-review-graph` tools to build a full knowledge graph of the codebase.

**Step 5 — Checks your working tree is clean**
```powershell
git status --porcelain
```
If you have uncommitted changes → stops. You need to commit or stash first.

**Step 6 — Sets split threshold based on your model**
Asks which Aider model you're using. For 7B models it adds `--auto-split-threshold 3`.

### Cold start in practice

```
You:    use the bridge to add a dark mode toggle to H:\Projects\my-react-app

Skill:  Cold start detected — bridge_progress/ not found

        Checking git... ✓
        Detected project type: typescript (found package.json + .tsx files)
        Found node_modules/ (340MB) → will add --aider-no-map automatically

        Cold start — running code-review-graph to map the codebase...
        [runs graph — ~40 seconds]
        Checking working tree... ✓ clean

        Which Aider model are you using?
          a) qwen2.5-coder:7b (faster, smaller)
          b) qwen2.5-coder:14b (slower, more capable)

You:    a

Skill:  Will add --auto-split-threshold 3 for safer multi-file handling.
        Setup complete. Proceeding to Stage 1...
```

After the first run, `bridge_progress/` exists and all future sessions skip cold start entirely.

---

## Working on an External Project

Point the bridge at any repo on your machine:

```
"use the bridge to implement a user login page in H:\Projects\my-app"
```

Antigravity will use that path as `--repo-root`. Bridge progress files are written to
`H:\Projects\my-app\bridge_progress\` — never mixing with this repo's state.

---

## Resuming an Interrupted Run

If a run crashes or you close the terminal mid-way:

```
"resume the last bridge run for H:\path\to\my-project"
```

Antigravity will invoke `--resume` and skip any already-completed tasks using the checkpoint.

---

## Useful Flags You Can Request

| What you want | What to say |
|---|---|
| Use a specific Ollama model | "use qwen2.5-coder:14b for this run" |
| Run tests after each task | "validate with `pytest tests/ -x -q` after each task" |
| Unity / large asset repo | "it's a Unity project" — adds `--aider-no-map` |
| Preview plan without running | "dry run only, don't invoke Aider" |
| Keep changes unstaged | "don't auto-commit after each task" |

---

## Token Usage & Statistics — What Gets Logged Automatically

Token usage is logged automatically. You don't do anything. Here's exactly what the bridge tracks:

### What's tracked per run

| Stat | Where it's stored | Auto or manual? |
|---|---|---|
| Supervisor tokens (plan in/out, review in/out) | `bridge_progress/token_log.json` | Auto |
| Aider / Ollama estimated tokens (per task) | `bridge_progress/token_log.json` | Auto |
| Your session overhead tokens | `bridge_progress/token_log.json` | Via `--session-tokens N` flag |
| Total AI cloud tokens | `bridge_progress/token_log.json` | Auto (calculated) |
| Estimated cost (Opus, Sonnet pricing) | `bridge_progress/token_log.json` | Auto |
| Tokens saved vs doing it without the bridge | `bridge_progress/token_log.json` | Auto (calculated) |
| Per-task: attempts, exit codes, stdout/stderr tail | `bridge_progress/RUN_DIAGNOSTICS.json` | Auto |
| Blocking patterns detected | `bridge_progress/RUN_DIAGNOSTICS.json` | Auto |
| Task completion status, commit SHAs, diffs | `bridge_progress/task_metrics.json` | Auto |
| Project file roles, run history | `bridge_progress/project_knowledge.json` | Auto |

### Files generated after every run

```
bridge_progress/
├── RUN_REPORT.md         ← token breakdown + savings table
├── RUN_DIAGNOSTICS.json  ← per-task attempts, failure patterns
├── token_log.json        ← full session token log + cumulative totals
├── task_metrics.json     ← task completion status + commit SHAs
├── LATEST_REPORT.md      ← quick status overview
├── last_run.json         ← compact run summary
├── project_knowledge.json ← updated project context
└── telemetry.json        ← anonymized usage events
```

### What you see at the end of every run

```
Run complete — my-project
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tasks:    4 / 4 completed  (0 skipped)
Reworks:  1 task needed rework
Commits:  4 git commits made
Duration: 187s

Token usage:
  Supervisor (plan + review):  3,240 tokens
  Aider / Ollama (local):      ~18,500 tokens (free)
  Total cloud AI:              8,061 tokens

Savings vs doing this without the bridge:
  Estimated direct cost:  24,000 tokens
  Actual cost:             8,061 tokens
  Saved:                  15,939 tokens (66.4%)

Blocking patterns: none
Reports written to: H:\Projects\my-project\bridge_progress\
```

---

## How the Context Tools Update

| Tool | Updates how | Frequency | You do anything? |
|---|---|---|---|
| **Knowledge Items (KIs)** | Saved automatically by Antigravity | End of session / on demand | Nothing |
| **code-review-graph** | MCP tool re-indexes at session start | Once per session | Nothing |

**Knowledge Items timeline across sessions:**
```
Session 1  →  Antigravity learns patterns  →  saved as KIs
Session 2  →  KIs loaded at start          →  new patterns saved
Session 3  →  All prior KIs available      →  ...
```
Every session automatically builds on all previous ones. No manual action needed.

**code-review-graph timeline across sessions:**
```
Session 1 open  →  graph indexes repo (state A)
  Aider commits tasks 1-4 during session  →  graph goes stale (fine, plan is locked)
Session 1 close

Session 2 open  →  graph re-indexes repo (state A + tasks 1-4)  →  fresh again
  Aider commits tasks 5-8
Session 2 close
```

You always plan against the latest committed state of the repo, automatically.

---

## Comparison: Antigravity vs Claude Code Skills

Both editions enforce the exact same pipeline. The differences are purely in tooling:

| Feature | Claude Code Edition | Antigravity Edition |
|---|---|---|
| Skill location | `.claude/skills/` | `.gemini/skills/` |
| Memory system | `claude-mem` (external service) | Knowledge Items (built-in) |
| Codebase indexing | External `code-review-graph` call | Built-in MCP tools (no install) |
| Shell | Bash | PowerShell |
| File operations | Read/Edit Claude tools | `view_file` / `replace_file_content` / `write_to_file` |
| Auto-review polling | `CronCreate` (60s timer) | `run_command` polling loop |
| Config file | `.claude/settings.local.json` | `GEMINI.md` (loaded as user rule) |

The pipeline itself — stages, task schema, review criteria, failure handling — is identical. A plan JSON created by Claude works in Antigravity and vice versa.

---

## Skill Files

```
.gemini/skills/
├── build.md                          ← /build command (tiny/medium/large classifier)
└── codex-aider-bridge/
    ├── SKILL.md                      ← main pipeline instructions Antigravity follows
    └── references/
        ├── pipeline.md               ← task JSON schema, review criteria, failure types
        └── flags.md                  ← all main.py flags and invocation patterns
```

---

## Quick Start Cheatsheet

```
┌─────────────────────────────────────────────────────────┐
│  "use the bridge to add feature X"                      │
│   → Full pipeline: plan → dry-run → execute → review    │
│                                                         │
│  "/build add feature X"                                 │
│   → Smart: tiny edits done instantly, rest via bridge   │
│                                                         │
│  "use the bridge to implement X in H:\path\to\project"  │
│   → Targets external project                            │
│                                                         │
│  "resume the last bridge run"                           │
│   → Picks up where a crashed run left off               │
│                                                         │
│  "dry run only"                                         │
│   → Validates plan JSON without touching any code       │
└─────────────────────────────────────────────────────────┘
```
