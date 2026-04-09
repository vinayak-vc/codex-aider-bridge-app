# Using the `codex-aider-bridge` Skill

The skill is committed to `.claude/skills/codex-aider-bridge/` in this repo.
Any teammate who opens this repo in **Claude Code** gets it automatically — no install needed.

---

## Prerequisites

Before using the skill for the first time, make sure you have:

| Requirement | Install command |
|---|---|
| Python 3.9+ | [python.org](https://python.org) |
| Aider | `pip install aider-chat` |
| Ollama | [ollama.com](https://ollama.com) — then `ollama serve` |
| At least one Ollama model | `ollama pull qwen2.5-coder:7b` |
| **claude-mem** (recommended) | `npx claude-mem install` — then restart Claude Code |

### About claude-mem

This skill uses **claude-mem** to keep token usage low across sessions. It runs as a background service and remembers:
- Which tasks failed and why, per project
- Which Aider models performed well
- Which files historically needed REWORK
- Flags and patterns that improved results

Without it, every session starts cold and re-discovers the same context. With it, the first run teaches all future runs.

Install once and forget it — it starts automatically when Claude Code opens.

---

## How to Trigger the Skill

The skill activates automatically in Claude Code when you phrase a request as a **goal** (not a direct edit).

### Trigger phrases that work

```
"use the bridge to implement X"
"implement feature X"
"build X"
"add X to the project"
"fix the bug where X"
"use the bridge to refactor X"
```

### What NOT to say (bypasses the skill)

```
"edit line 42 in aider_runner.py"       ← too direct, Claude edits inline
"open executor/task_ir.py and change X" ← same
```

If Claude starts editing files directly without going through the pipeline, remind it:
> "Use the bridge skill — don't edit directly."

---

## What Happens After You Trigger It

The skill walks Claude through these stages automatically:

### Stage 0 — Repo resolution
Claude asks which project to run the bridge against if it's not clear from context.

**If working on this repo itself:** just say the goal, no path needed.
**If targeting another project:** include the path in your message:
> "use the bridge to implement X in `/path/to/my-project`"

### Stage 1 — Setup checks
Claude verifies Python, Aider, and Ollama are working before touching anything.
If something is broken, it stops and tells you exactly what to fix.

### Stage 1.6 — Code-Review-Graph (automatic, every session start)

Claude runs this automatically at the start of every session. You'll see one line:

> *"Indexing `<repo>` with code-review-graph..."*

No prompt, no choice. The graph replaces `git ls-files` so every task instruction in Stage 2 references exact `file:line` locations instead of guesses.

**What it gives Claude:**
- Every function, class, and module with exact file:line locations
- What each symbol depends on (imports, calls)
- What depends on each symbol — changing X reveals everything that breaks

If the graph tool is unavailable, Claude falls back to `git ls-files` silently.

**Does it update automatically?**
No — and it doesn't need to. The graph is a **session-start snapshot**. It's fresh when your session opens. During the run, Aider makes commits and the graph goes stale — but that's fine because the plan is already locked before Aider touches anything. When you open a new session next time, the graph re-indexes and picks up everything Aider committed last session. You always start fresh, automatically.

---

### Stage 2 — Plan preview (you approve before any code runs)
Claude produces a numbered task plan — e.g.:

```
Task 1 [modify] executor/aider_runner.py
  → Add parameter `max_retries` (int, default=3) to AiderRunner.__init__

Task 2 [modify] executor/aider_runner.py
  → In AiderRunner.run(), add retry loop using self.max_retries

Task 3 [validate] executor/aider_runner.py
  → must_exist: executor/aider_runner.py
```

Claude asks: **"Does this plan look right before I hand it to the executor?"**
You can say "looks good", request changes, or ask it to split/merge tasks.
No code is touched until you approve.

### Stage 3 — Executor handoff
Claude runs:
```bash
python main.py \
  --repo-root "<your project>" \
  --plan-file TASK_PLAN_active.json \
  --manual-supervisor \
  --workflow-profile micro \
  --auto-approve
```

Aider (your local LLM) implements each task one at a time.

### Stage 4 — Diff review loop
After each task, Claude reads the git diff and decides:
- **PASS** → bridge moves to the next task
- **REWORK** → Claude gives Aider a specific correction and it retries

You watch this happen in real time. You can also intervene:
> "That diff looks wrong to me — rework it, the function signature is missing the default value."

### Stage 5 — Done
Claude reports: how many tasks completed, how many failed, how many git commits were made.

---

## Example Session

```
You:    use the bridge to add a --timeout flag to main.py that limits
        total run time and aborts with exit code 124 if exceeded

Claude: [Stage 1] Checks Python ✓, Aider ✓, Ollama ✓
        [Stage 2] Here's my plan (3 tasks)...
                  Task 1: add --timeout arg to argument parser in main.py
                  Task 2: pass timeout to AiderRunner in main.py
                  Task 3: enforce timeout in AiderRunner.run() loop
                  Does this look right?

You:    looks good

Claude: [Stage 3] Running executor...
        [Stage 4] Task 1 diff: ✓ PASS
                  Task 2 diff: ✓ PASS
                  Task 3 diff: ✗ REWORK — timeout not enforced in the loop,
                               only passed as a parameter. Adding enforcement.
                  Task 3 retry: ✓ PASS
        [Stage 5] 3/3 tasks complete, 3 commits made.
```

---

## Using the Bridge on a Fresh Repo (Cold Start)

If the target project has **never been run through the bridge** — no `bridge_progress/` folder, no prior history — the skill detects this automatically and runs a one-time cold start setup before doing anything else.

### What "cold" means

The bridge needs certain infrastructure to work safely. A fresh repo has none of it:

| What's missing | Why it matters |
|---|---|
| `bridge_progress/` | No checkpoints → a crash means starting over |
| `project_knowledge.json` | Supervisor has no context → plans will be vague |
| Project type | Wrong validator runs after each task |
| Git cleanliness check | Bridge commits after every task — dirty tree = corrupted diffs |
| claude-mem history | Everything starts from zero |

### What the skill does automatically

**Step 1 — Confirms git exists**
If the folder isn't a git repo, stops and tells you to run `git init` + initial commit.

**Step 2 — Detects project type**
Looks for markers (`package.json`, `Cargo.toml`, `*.sln`, `pyproject.toml`, etc.) and tells you what it found. You can correct it if wrong. This controls which syntax checker runs after each Aider task.

**Step 3 — Checks for large non-code directories**
If it finds `node_modules/`, `Library/` (Unity), `.venv/`, etc. it automatically adds `--aider-no-map` to prevent Aider from hanging during its initial scan. No action needed from you.

**Step 4 — Runs code-review-graph automatically (no prompt)**
On a cold repo Claude has zero prior knowledge. The graph runs automatically without asking — it's the only reliable way to produce accurate task instructions on an unfamiliar codebase. Claude tells you it's running and why, then uses the output as the primary repo map for planning.

**Step 5 — Checks your working tree is clean**
```bash
git status --porcelain
```
If you have uncommitted changes → stops. You need to commit or stash first. The bridge commits after every approved task and needs a clean baseline to produce meaningful diffs.

**Step 6 — Sets split threshold based on your model**
Asks which Aider model you're using. For 7B models it adds `--auto-split-threshold 3` so multi-file tasks are automatically split before Aider sees them. 14B+ models handle multi-file tasks fine.

### What the first run does that subsequent runs don't

- **Onboarding scan** (~20 seconds): reads up to 500 source files, extracts function names, class names, docstrings, detects frameworks → writes `project_knowledge.json`. This only happens once.
- **Creates `bridge_progress/`**: all checkpoint and state files live here from now on.
- All future sessions detect `bridge_progress/` exists → skip the cold start entirely → go straight to Stage 1.

### Cold start in practice — what you'll see

```
You:    use the bridge to add a dark mode toggle to /Users/me/my-react-app

Skill:  Cold start detected — bridge_progress/ not found in /Users/me/my-react-app

        Checking git... ✓
        Detected project type: typescript (found package.json + .tsx files)
        Found node_modules/ (340MB) → will add --aider-no-map automatically

        Cold start — running code-review-graph to map the codebase.
        This replaces the rough file tree with exact symbol locations
        and dependency edges, making the first plan significantly more accurate.
        [runs graph — ~40 seconds]
        Checking working tree... ✓ clean

        Which Aider model are you using?
          a) qwen2.5-coder:7b (faster, smaller)
          b) qwen2.5-coder:14b (slower, more capable)

You:    a

Skill:  Will add --auto-split-threshold 3 for safer multi-file handling.

        Setup complete. Proceeding to Stage 1...
        [first run will also scan 500 files to build project knowledge — ~20 seconds]
```

After that, it runs exactly like any normal session.

---

## Working on an External Project

Point the bridge at any repo on your machine:

```
"use the bridge to implement a user login page in /Users/vinayak/projects/my-app"
```

Claude will use that path as `--repo-root`. Bridge progress files are written to
`/Users/vinayak/projects/my-app/bridge_progress/` — never mixing with this repo's state.

---

## Resuming an Interrupted Run

If a run crashes or you close the terminal mid-way:

```
"resume the last bridge run for /path/to/my-project"
```

Claude will invoke `--resume` and skip any already-completed tasks using the checkpoint.

---

## Useful Flags You Can Request

| What you want | What to say |
|---|---|
| Use a specific Ollama model | "use qwen2.5-coder:14b for this run" |
| Run tests after each task | "validate with `pytest tests/ -x -q` after each task" |
| Unity / large asset repo | "it's a Unity project" — Claude adds `--aider-no-map` |
| Preview plan without running | "dry run only, don't invoke Aider" |
| Keep changes unstaged | "don't auto-commit after each task" |

---

## How the Two Context Tools Update

A common question: do `code-review-graph` and `claude-mem` keep themselves up to date as Aider makes changes?

| Tool | Updates how | Frequency | You do anything? |
|---|---|---|---|
| **claude-mem** | Automatic via lifecycle hooks (`PostToolUse`, `SessionEnd`) | Continuously, every tool call | Nothing — it just runs |
| **code-review-graph** | Re-indexes at session start | Once per session open | Nothing — skill runs it automatically |

**claude-mem timeline across sessions:**
```
Session 1  →  hooks capture everything  →  saved to DB
Session 2  →  mem-search loads session 1  →  hooks capture session 2  →  saved
Session 3  →  mem-search loads sessions 1+2  →  ...
```
Every session automatically builds on all previous ones. No manual action ever needed.

**code-review-graph timeline across sessions:**
```
Session 1 open  →  graph indexes repo (state A)
  Aider commits tasks 1-4 during session  →  graph goes stale (that's fine, plan is locked)
Session 1 close

Session 2 open  →  graph re-indexes repo (state A + tasks 1-4)  →  fresh again
  Aider commits tasks 5-8
Session 2 close

Session 3 open  →  graph re-indexes (state A + tasks 1-8)  →  fresh again
```

You always plan against the latest committed state of the repo, automatically, every session.

---

## Skill Files

```
.claude/skills/codex-aider-bridge/
├── SKILL.md                    ← main pipeline instructions Claude follows
└── references/
    ├── pipeline.md             ← task JSON schema, review criteria, failure types
    └── flags.md                ← all main.py flags and invocation patterns
```
