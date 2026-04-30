# Codex Aider Bridge App

`codex-aider-bridge-app` is a local orchestrator that separates **planning and review** from **code execution**.

It supports both:
- an external supervisor CLI flow (`codex`, `claude`, etc.)
- a manual supervisor flow where the active agent session reviews each task directly and the bridge waits for decision JSON files instead of calling another AI process

For the most accurate low-token workflow, use:
- `--workflow-profile micro`
- `--manual-supervisor`
- a pre-written `--plan-file`

That profile is designed for: Codex supervises, Aider implements, bridge validates.

It ships with a **web UI** you can launch by double-clicking `launch_ui.bat`, and a CLI you can drive directly from the terminal.

The bridge is also a **project-memory and analytics layer**. Every run writes structured state into the target repo's `bridge_progress/` folder so the supervising agent can resume with context instead of re-reading source files.

---

## Installation — one command

```bash
npm install -g bridge-mcp-server
```

That single command automatically:
- Clones the bridge Python runtime to `~/.bridge/`
- Installs the Claude Code skill to `~/.claude/skills/codex-aider-bridge/`
- Registers the MCP server in `~/.claude/settings.json` with the correct `BRIDGE_ROOT` path

Then **restart Claude Code**, open any project, and type:

```
/codex-aider-bridge
```

No cloning this repo manually. No path config. No `python main.py`.

---

## Architecture

```
Supervisor Agent (Codex / Claude / any)
  = Tech Supervisor
  │  - Reads the repo tree
  │  - Produces atomic sequential JSON tasks
  │  - Reviews each completed task's diff
  │  - Returns PASS or REWORK
  │  - Never writes code
  │
Bridge (this app)
  │  - Routes messages between supervisor and Aider
  │  - Collects git diffs after execution
  │  - Requires a git-backed target repo before execution
  │  - Runs mechanical checks (file existence, syntax, CI gate)
  │  - Auto-commits approved task changes into small local commits
  │  - Sends compact review payloads to supervisor
  │  - Persists project knowledge, task metrics, token log, and latest run report
  │  - Never makes coding decisions
  │
Aider (local LLM)
  = Developer
     - Receives one atomic instruction + file list
     - Applies code changes using a local model
     - Reports back via exit code + stdout/stderr
```

### Execution loop

```
Supervisor → atomic plan
  For each task (sequentially):
    Aider executes instruction on target files
    Bridge collects git diff
    Bridge runs mechanical checks (no supervisor tokens)
    Supervisor reviews diff → PASS or REWORK
      PASS   → next task
      REWORK → Aider retries with supervisor's new instruction
```

### Manual Supervisor Mode

```text
Agent session creates plan JSON
  -> Bridge executes one task through Aider
  -> Bridge writes a review request JSON under bridge_progress/manual_supervisor/requests/
  -> Agent session writes a decision JSON under bridge_progress/manual_supervisor/decisions/
  -> Bridge resumes:
       pass    -> next task
       rework  -> retry with a new instruction
       subplan -> execute corrective micro-tasks, then continue
```

Manual-supervisor reruns are crash-tolerant:
- structured stdout events are best-effort only and no longer define task success
- a passed review writes a completed receipt before UI emission
- rerunning the same command can consume matching request/decision files or resume an unchanged approved task without invoking Aider again

---

## Recommended Workflow

For the most reliable and token-efficient setup:

1. The **agentic AI session** reads the target repo context and writes a task JSON plan.
2. The plan is saved in the target repo under `taskJsons/`.
3. The bridge runs in:
   - `--workflow-profile micro`
   - `--manual-supervisor`
4. Aider executes exactly one atomic task at a time.
5. The bridge writes a review request JSON after each task.
6. The active AI session reviews the diff and writes a decision JSON:
   - `pass`
   - `rework`
   - `subplan`
7. The bridge auto-commits each approved task into the target repo.
8. The bridge resumes and continues until all tasks are complete.

This keeps the bridge dumb, Aider productive, and the expensive AI focused only on planning and review.

---

## `bridge_progress/` Artifacts

Every run writes project state into the **target repo**, not the bridge repo.

Important files:

- `bridge_progress/project_knowledge.json`
  - rolling file-responsibility map
  - what each known file does
  - which features are already done
  - run history
- `bridge_progress/project_snapshot.json`
  - current file tree snapshot
  - completed vs pending tasks
  - failed task id when a run stops
- `bridge_progress/task_metrics.json`
  - machine-readable task completion state for the current run
  - resumed checkpoint ids are tracked separately
  - records per-task `commit_sha` values when auto-commit succeeds
- `bridge_progress/token_log.json`
  - token and savings history across runs
- `bridge_progress/LATEST_REPORT.md`
  - short human-readable run summary
- `bridge_progress/last_run.json`
  - most recent success/failure payload
- `bridge_progress/manual_supervisor/requests/`
  - review request JSON files
- `bridge_progress/manual_supervisor/decisions/`
  - supervisor decision JSON files
- `bridge_progress/manual_supervisor/completed/`
  - persisted approval receipts used to recover a passed task after a crash

The bridge now updates the project knowledge and snapshot files **during the run**, not only after a perfect success. That means partial progress is still captured if the local model fails midway.

---

## Quick Start — Web UI

The easiest way to use the app. No terminal required after setup.

```
python launch_ui.py
```

Or on Windows, just double-click **`launch_ui.bat`**.

A browser window opens at `http://127.0.0.1:7823`:

| Page | What it does |
|---|---|
| **Dashboard** | Live progress ring, task feed with status badges, pause/resume, review panel |
| **Run** | Configure and launch a run — goal, repo, model, supervisor, advanced options |
| **Chat Drawer** | Persistent right-side Ollama chat for project Q&A, new chat, and stop controls |
| **Knowledge** | View AI_UNDERSTANDING.md and the scanned file registry |
| **History** | Browse past runs, re-run with same settings, view full logs |
| **Tokens** | Token usage analytics with savings bar and session detail |
| **Setup** | Install/check Aider, Ollama, and supervisor tools |

### Supervisor compatibility

| Supervisor | Requirement | Works with subscription? |
|---|---|---|
| **Claude Code** | Run `claude login` once | Yes — Claude Pro |
| **Cursor** | Cursor IDE installed | Yes — Cursor subscription |
| **Windsurf** | Windsurf IDE installed | Yes — Windsurf subscription |
| **Manual** | Nothing | Always — fully offline |
| **AI Relay** | Nothing | Yes — any web AI (ChatGPT Plus, Claude.ai Pro, Gemini…) |
| **Codex CLI** | `OPENAI_API_KEY` env var | No — separate API account needed |

Flask is installed automatically if it is not present.

---

## Quick Start — CLI

```bash
# Basic run — supervisor plans, Aider executes on local LLM
python main.py "Build a logging system feature" --aider-model ollama/mistral

# With a product brief and explicit repo target
python main.py "Build the first playable vertical slice" \
  --repo-root "H:\\MyProject\\GameRepo" \
  --idea-file "H:\\MyProject\\GAME_IDEA.md" \
  --aider-model ollama/deepseek-coder

# Dry-run — generate plan only, no Aider invocation
python main.py "Refactor settings loading" --dry-run

# Execute from an existing plan file (recommended for manual-supervisor mode)
python main.py "Implement feature X" \
  --repo-root "H:\\MyRepo" \
  --plan-file "H:\\MyRepo\\taskJsons\\plan_001_feature_x.json" \
  --workflow-profile micro \
  --manual-supervisor \
  --aider-model ollama/qwen2.5-coder:14b

# Save the generated plan for inspection
python main.py "Add telemetry" --plan-output-file "plan.json" --dry-run

# Use Claude CLI as the supervisor instead of Codex
python main.py "Add error handling" \
  --supervisor-command "claude --print" \
  --aider-model ollama/mistral

# With a CI gate command run after each task
python main.py "Add unit tests" \
  --aider-model ollama/mistral \
  --validation-command "python -m pytest"
```

Before task execution starts, the bridge now logs a git-readiness preview for the target repo:
- whether the target is a git repository
- whether `HEAD` exists
- current branch
- clean vs dirty worktree
- staged, unstaged, and untracked counts
- what the bridge will do next

---

## Features

### Web UI
- Multi-page Flask app — Dashboard, Run, Knowledge, History, Tokens, Git, Setup, plus a persistent **Chat Drawer**
- **Split-panel Run page** — settings/log tabs on left, persistent task progress panel on right with drag-to-resize
- **Parsed log view** — structured event cards with tag filters (Task, Review, Error, Warning, Bridge, Proxy), toggleable with Raw view
- Live SVG progress ring, task feed, pause/resume, review panel with diff viewer
- **Task progress panel** — shows all tasks with done/running/failed/pending status, click for diff viewer, undo button, resume from checkpoint
- **Smart pre-flight check** — validates Ollama, GPU, model VRAM, disk, git, Aider before launch; blocks on critical failures
- **Goal templates** — 8 pre-built templates (Add Feature, Fix Bug, Refactor, Tests, API, Security, Performance, Read/Analyze)
- **Chat drawer** — conversational AI using a local Ollama model, auto-unloads from VRAM on close
- **Git page** — branch management, commit history with bridge task badges, changed files with tree view, inline diff viewer, add-to-gitignore
- **Knowledge page** — AI Understanding viewer, file registry with tree view, file type icons, click-to-open in VS Code, refresh/auto-refresh
- **Model speed benchmark** — test Ollama tok/s on Setup page with estimated task time
- **GPU process manager** — list GPU processes, kill non-essential apps to free VRAM, "Free VRAM" button
- **Cost estimator** — shows estimated time, supervisor tokens, aider tokens before launch
- **Desktop notifications** — browser notification when run completes/fails while tab is in background
- **Project dashboard cards** — per-project status cards with progress bars on Dashboard
- **Favorite plans** — save and reuse plan templates across projects
- **Run queue** — queue multiple goals, auto-starts next on completion
- **VS Code integration** — open project/files in VS Code from status bar and file registry
- **VS Code-style status bar** — branch, git status, auto-commit toggle, GPU status, run status, task count on every page
- **UI action logger** — records all clicks, API calls, SSE events for debugging; downloadable as JSON
- **Product telemetry** — local-only usage analytics with AI analysis prompt
- Supervisor and model API-key compatibility warnings on the Run page
- Dark theme, keyboard shortcuts (`g+d/r/k/h/t/s/g/c`, `?` help, `Ctrl+Enter`)
- Onboarding scanner: one-time static scan pre-populates `project_knowledge.json` on first run

### CLI & Orchestration
- **Universal Pipeline** — all supervisor types use `--manual-supervisor`; UI proxy thread dispatches to correct backend (CLI or copy-paste)
- **Mid-run supervisor switching** — change supervisor during an active run without restarting
- **Escalating retry strategy** — 10 attempts: standard (1-3), simplified (4-6), supervisor diagnostic (7), diagnostic-informed (8-9), supervisor takeover prompt (10)
- **Read/Investigate task types** — analysis-only goals skip Aider entirely; supervisor reads files and answers questions
- **Smart goal routing** — classifies goals as read/investigate/code before plan generation
- Supervisor agent produces atomic sequential plans from the live repo tree — no hardcoded file lists
- Supervisor reviews each task's git diff before the next task is allowed to start
- Plan generation uses the selected supervisor (Claude/Codex CLI), not Ollama
- Git-readiness pre-flight preview before Aider runs
- Approved tasks are auto-committed as small local git commits (toggleable)
- Mechanical validation (file existence, Python syntax, optional CI gate) — zero supervisor tokens
- Scope enforcement: unexpected file creation outside task scope is detected and reverted
- **Run diagnostics** — `RUN_DIAGNOSTICS.json` with per-task failure analysis and blocking pattern detection
- **Token tracking** — supervisor + Aider token estimation, per-task breakdown, savings comparison report (`RUN_REPORT.md`)
- Persistent project intelligence: `project_knowledge.json`, `project_snapshot.json`, `token_log.json`, `LATEST_REPORT.md`
- Persistent file and console logging; `stdin=DEVNULL` for Aider prevents headless hangs

---

## Requirements

- Python 3.10+
- Optional external supervisor agent CLI: `codex`, `claude`, or any agent that reads a prompt and writes JSON
- `aider` CLI (`pip install aider-chat`)
- A local LLM accessible via Aider (Ollama, LM Studio, etc.) — or Aider's own cloud model
- `flask>=3.0` for the web UI (auto-installed by `launch_ui.py`)

No external Python packages are required for the CLI bridge itself.

### Chat Drawer behavior

- Open chat from the floating tab on the right side of the UI
- Chat stays available while you move between pages
- Assistant responses can continue while the drawer is collapsed or another page is open
- History is stored per project and restored after app restart when that project is selected again
- `New Chat` clears the current project's thread
- `Stop` interrupts the active assistant response

### AI Relay behavior

- Importing or generating tasks saves the relay state for the selected project
- After app restart, AI Relay restores the task list and task status badges such as `Not started`, `Done`, and `Failed`
- If there is no live bridge session, AI Relay reopens on **Confirm Tasks** instead of **Run & Review**
- **Run & Review** only restores automatically for an active live session
- Each imported AI Relay plan now gets its own session id, so old manual-supervisor files from a previous plan cannot mark new tasks as completed just because the task ids match

---

## Using This Bridge On External Projects

When an agentic AI uses the bridge on another repo, the preparation should look like this:

1. Read only the minimum context needed:
   - the user brief / goal file
   - the target repo file tree
   - any project summary or knowledge cache if available
2. Do **not** let the agent write code directly in the target repo.
3. The agent writes a plan JSON into:
   - `<target_repo>/taskJsons/plan_001_<feature>.json`
4. The plan should use `micro` profile rules:
   - exactly one file per task
   - exactly one concern per task
   - `must_exist` for create tasks
   - `must_not_exist` for delete tasks
   - observable assertions for modify tasks
5. Run the bridge from this repo, pointing it at the external project:

```bash
python main.py "Short goal headline" \
  --repo-root "D:\\ExternalProject" \
  --plan-file "D:\\ExternalProject\\taskJsons\\plan_001_feature.json" \
  --workflow-profile micro \
  --manual-supervisor \
  --aider-model ollama/qwen2.5-coder:14b
```

The target repo must be backed by git before the bridge will execute tasks.
If the folder is not a git repository and you are running interactively, the bridge can:
- stop and let you create the repo yourself
- or initialize a local git repo and create the baseline commit for you

6. After each task:
   - read the request file in `D:\ExternalProject\bridge_progress\manual_supervisor\requests\`
   - review the diff and validation result
   - write a decision file into `...manual_supervisor\decisions\`
   - once approved, let the bridge auto-commit that task's file changes
7. Use the analytics files in `bridge_progress/` instead of re-reading the repo blindly on every follow-up:
   - `project_knowledge.json`
   - `project_snapshot.json`
   - `LATEST_REPORT.md`

This is the intended external-project workflow for Codex-style supervision.

---

## Project Structure

```text
bridge-app/
├── main.py                       CLI entry point
├── launch_ui.py                  Web UI launcher (auto-installs Flask, opens browser)
├── launch_ui.bat                 Windows double-click launcher
├── requirements.txt              Bridge CLI dependencies
├── requirements_ui.txt           Web UI dependency (flask)
│
├── ui/                           Web UI package
│   ├── __init__.py
│   ├── app.py                    Flask server (routes, SSE, chat + relay endpoints)
│   ├── bridge_runner.py          Subprocess manager + SSE event broadcaster
│   ├── setup_checker.py          Detects Python, Aider, Ollama, Codex, Claude
│   ├── state_store.py            JSON persistence for settings and run history
│   ├── data/                     Runtime data (settings.json, history.json)
│   ├── templates/
│   │   ├── base.html             Layout shell (sidebar nav, theme, shortcuts, toasts)
│   │   ├── dashboard.html        Live progress ring, task feed, review panel
│   │   ├── run.html              Config form, supervisor selector, live log
│   │   ├── chat.html             Conversational AI (Ollama-powered)
│   │   ├── knowledge.html        AI_UNDERSTANDING.md viewer, file registry
│   │   ├── history.html          Searchable run table, log modal
│   │   ├── tokens.html           Token analytics and session detail
│   │   └── setup.html            Dependency checks, Aider install, Ollama manager
│   └── static/
│       ├── css/
│       │   ├── tokens.css        CSS custom properties (design tokens)
│       │   ├── base.css          Reset, typography, layout utilities
│       │   ├── components.css    btn, card, badge, input, modal, toast
│       │   ├── nav.css           Fixed sidebar navigation
│       │   ├── progress-ring.css SVG ring animations
│       │   └── pages/            Per-page stylesheets
│       └── js/
│           ├── core/             api.js, sse.js, store.js, toast.js, theme.js, shortcuts.js
│           └── pages/            Per-page controllers (dashboard, run, chat, …)
│
├── supervisor/
│   ├── __init__.py
│   └── agent.py                  SupervisorAgent — plan + review
├── planner/
│   ├── __init__.py
│   ├── codex_client.py           Backwards-compat shim → supervisor.agent
│   └── fallback_planner.py       Removed (raises NotImplementedError)
├── executor/
│   ├── __init__.py
│   ├── aider_runner.py           Runs Aider with local LLM model support
│   └── diff_collector.py         Collects git diff after each Aider run
├── parser/
│   ├── __init__.py
│   └── task_parser.py            Validates supervisor JSON plan
├── validator/
│   ├── __init__.py
│   └── validator.py              Mechanical checks only (MechanicalValidator)
├── context/
│   ├── __init__.py
│   ├── file_selector.py          Resolves task file paths
│   ├── idea_loader.py            Loads optional idea/brief file
│   └── repo_scanner.py           Produces compact repo tree for supervisor
├── models/
│   ├── __init__.py
│   └── task.py                   Task, TaskReport, ReviewResult, BridgeConfig, …
├── utils/
│   ├── __init__.py
│   └── command_resolution.py     Resolves executables across PATH and venv Scripts
├── bridge_logging/
│   ├── __init__.py
│   └── logger.py
├── logs/
│   └── .gitkeep
├── example plan.json
├── AI_SUPERVISOR_PROMPT.md       Prompt reference and backend configuration guide
├── HOW_TO.md                     Plain-English guide for non-Python users
├── README.md
├── CHANGELOG.md
└── AGENT_CONTEXT.md
```

---

## CLI Options

| Option | Default | Description |
|---|---|---|
| `goal` | *(required)* | High-level goal |
| `--repo-root` | Current directory | Target repository |
| `--idea-file` | — | Architecture/product brief for the supervisor |
| `--plan-file` | — | Execute a pre-made plan instead of asking the supervisor |
| `--plan-output-file` | — | Save the generated plan JSON |
| `--dry-run` | false | Plan only, skip Aider execution |
| `--max-plan-attempts` | 3 | Retries on invalid supervisor JSON |
| `--max-task-retries` | 2 | Max REWORK cycles per task |
| `--supervisor-command` | env / fallback | Command used only when you intentionally want an external supervisor subprocess |
| `--manual-supervisor` | false | Disable external supervisor CLI calls and wait for local review decision JSON files instead |
| `--manual-review-poll-seconds` | 2 | Polling interval while waiting for a manual supervisor decision |
| `--workflow-profile` | `standard` | `micro` enforces one-file atomic tasks and required assertions |
| `--aider-command` | `aider` | Aider command prefix |
| `--aider-model` | — | Local LLM model for Aider (e.g. `ollama/mistral`) |
| `--validation-command` | — | Optional CI gate run after each task |
| `--log-level` | `INFO` | Logging verbosity |

### Environment variables

| Variable | Maps to |
|---|---|
| `BRIDGE_SUPERVISOR_COMMAND` | `--supervisor-command` |
| `BRIDGE_AIDER_COMMAND` | `--aider-command` |
| `BRIDGE_AIDER_MODEL` | `--aider-model` |
| `BRIDGE_DEFAULT_VALIDATION` | `--validation-command` |

---

## Supervisor JSON Contract

The supervisor must return exactly this shape:

```json
{
  "tasks": [
    {
      "id": 1,
      "files": ["relative/path/file.ext"],
      "instruction": "Create X that does Y.",
      "type": "create",
      "must_exist": ["relative/path/file.ext"],
      "must_not_exist": []
    }
  ]
}
```

In `--workflow-profile micro`:
- every task must target exactly one file
- every `create` task must include `must_exist`
- every `delete` task must include `must_not_exist`
- every `modify` task must include at least one observable assertion
- manual supervision is the intended review mode

### Manual decision file format

When the bridge pauses for review in manual-supervisor mode, write a decision JSON like one of these:

```json
{
  "task_id": 7,
  "decision": "pass"
}
```

```json
{
  "task_id": 7,
  "decision": "rework",
  "instruction": "In main.py, fix argparse so --help works without requiring the optional runtime inputs."
}
```

```json
{
  "task_id": 7,
  "decision": "subplan",
  "sub_tasks": [
    {
      "instruction": "In main.py, repair the syntax error at the top of the file.",
      "files": ["main.py"],
      "type": "modify"
    }
  ]
}
```

See `AI_SUPERVISOR_PROMPT.md` for full prompt specifications, review format,
and backend configuration (Codex, Claude, custom agents, local LLMs).

---

## Token Usage

| Event | Supervisor tokens |
|---|---|
| Planning | One call (+ retries on parse failure) |
| Mechanical check failure | **Zero** — retries with same instruction |
| Task review (after mechanical pass) | One call per task |
| REWORK retry | One call per retry |

Aider handles all code generation locally. In manual-supervisor mode the bridge
does not invoke any external supervisor CLI at all; it only writes compact
review request and decision JSON files for the active agent session.

`token_log.json` records:
- bridge subprocess supervisor tokens when an external supervisor is used
- session token estimates when manual-supervisor mode is used
- estimated direct-coding baseline vs total AI tokens
- weighted savings across runs
- successful-session savings averages
- wasted token totals for zero-progress sessions
- likely waste reasons such as `bridge_stdout_crash`, `model_missing`, and `manual_review_rerun`

### How to read the token totals

- `savings_percent_weighted` is the most accurate all-runs headline metric.
- `savings_percent_successful_avg` excludes zero-progress sessions.
- `wasted_tokens_total` counts sessions that spent tokens but completed no tasks.
- `waste_reason_counts` groups those zero-progress sessions by likely cause.

If a run completes no tasks, its session note now marks it as overhead rather than productive savings.

---

## Known Failure Modes

### Stdout emit crash

Symptoms:
- `OSError: [Errno 22] Invalid argument`
- the bridge stops right after a review handoff or task completion event

Recovery:
- rerun the exact same bridge command
- keep the same `--plan-file`
- let checkpointing and `bridge_progress/manual_supervisor/completed/` recover the last approved task

### Missing model

If Aider reports that the configured model is missing, install it and rerun the same command. Those zero-progress runs are now classified as `model_missing` in `token_log.json`.

### Stale request or decision files

If the saved request/decision pair does not match the current task instruction or file list, the bridge archives it as stale automatically. Matching pairs are consumed automatically on rerun.

### Interactive Aider prompts

The bridge already uses non-interactive flags, but some Aider/model combinations still emit confirmation prompts. That output is now treated as an explicit task failure instead of a silent success. Narrow the task and rerun.

### Zero-progress token spikes

Use `wasted_tokens_total` and `waste_reason_counts` in `token_log.json` to separate crash overhead from productive savings.

---

## Notes

- The bridge resolves `codex`, `aider`, and other executables from PATH, `.venv\Scripts`, `venv\Scripts`, and `aider-env\Scripts` automatically on Windows.
- The supervisor receives the live repo tree at runtime — no hardcoded file paths anywhere.
- `bridge_progress/task_metrics.json` now includes per-task commit SHAs when auto-commit succeeds.
- If the supervisor fails to produce a valid plan after all retries, use `--plan-file` to supply one manually.
- The bridge does not depend on a specific project type and works against any repo reachable via `--repo-root`.
- `bridge_progress/project_knowledge.json` is the main handoff file for future sessions; the supervising agent should prefer reading it before opening source files.
- For a non-technical user guide, see `HOW_TO.md`.
