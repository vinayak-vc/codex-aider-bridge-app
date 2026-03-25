# Codex Aider Bridge App

`codex-aider-bridge-app` is a local orchestrator that separates **planning and review** (done by a supervisor AI agent) from **code execution** (done by Aider running on a local LLM).

It ships with a **web UI** you can launch by double-clicking `launch_ui.bat`, and a CLI you can drive directly from the terminal.

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
  │  - Runs mechanical checks (file existence, syntax, CI gate)
  │  - Sends compact review payloads to supervisor
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

---

## Quick Start — Web UI

The easiest way to use the app. No terminal required after setup.

```
python launch_ui.py
```

Or on Windows, just double-click **`launch_ui.bat`**.

A browser window opens at `http://127.0.0.1:7823` with:

- **Setup tab** — detects Python, Aider, Ollama, Codex CLI, Claude CLI. Shows install hints and one-click install buttons for missing tools.
- **Run tab** — fill in your goal, repo path, model, and supervisor settings. Click **Start Run** to watch task-by-task progress in real time with live log streaming.
- **History tab** — every run is saved. Re-run, view logs, or delete entries.

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

# Execute from an existing plan file (skip supervisor planning)
python main.py --plan-file "my-plan.json" --aider-model ollama/codellama

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

---

## Features

- **Web UI** with setup wizard, live task progress, run history, and persisted settings
- Supervisor agent produces atomic sequential plans from the live repo tree — no hardcoded file lists
- Supervisor reviews each task's git diff before the next task is allowed to start
- Aider runs on a local LLM (`--aider-model ollama/mistral`, `ollama/codellama`, etc.)
- Mechanical validation (file existence, Python syntax, optional CI gate) runs without supervisor tokens
- Supervisor tokens are only spent on planning and quality review — never on mechanical retries
- No fallback planner — if the supervisor fails, use `--plan-file` to supply a plan manually
- Dry-run mode generates and parses the plan without invoking Aider
- Persistent file and console logging

---

## Requirements

- Python 3.10+
- A supervisor agent CLI: `codex`, `claude`, or any agent that reads a prompt and writes JSON
- `aider` CLI (`pip install aider-chat`)
- A local LLM accessible via Aider (Ollama, LM Studio, etc.) — or Aider's own cloud model
- `flask>=3.0` for the web UI (auto-installed by `launch_ui.py`)

No external Python packages are required for the CLI bridge itself.

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
│   ├── app.py                    Flask server (18 API routes)
│   ├── bridge_runner.py          Subprocess manager + SSE event broadcaster
│   ├── setup_checker.py          Detects Python, Aider, Ollama, Codex, Claude
│   ├── state_store.py            JSON persistence for settings and run history
│   ├── data/                     Runtime data (settings.json, history.json)
│   └── templates/
│       └── index.html            Single-page app (Setup / Run / History tabs)
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
| `--supervisor-command` | `codex.cmd exec …` | Command used to invoke the supervisor agent. Can be set to `interactive` to provide supervisor inputs manually via the terminal. |
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
      "type": "create"
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

Aider handles all code generation locally. The supervisor only receives compact
prompts and compact diff payloads.

---

## Notes

- The bridge resolves `codex`, `aider`, and other executables from PATH, `.venv\Scripts`, `venv\Scripts`, and `aider-env\Scripts` automatically on Windows.
- The supervisor receives the live repo tree at runtime — no hardcoded file paths anywhere.
- If the supervisor fails to produce a valid plan after all retries, use `--plan-file` to supply one manually.
- The bridge does not depend on a specific project type and works against any repo reachable via `--repo-root`.
- For a non-technical user guide, see `HOW_TO.md`.
