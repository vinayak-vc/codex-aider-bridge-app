# Changelog

---

## [2026-03-25] — Web UI, Architecture Redesign, HOW_TO

### Added
- **Web UI** (`ui/`) — full single-page application accessible at `http://127.0.0.1:7823`
  - Setup tab: detects Python, Aider, Ollama, Codex CLI, Claude CLI with one-click install for Aider and Ollama model pull
  - Run tab: goal, repo, model, supervisor settings; live per-task progress cards; streaming log
  - History tab: persisted run history with re-run, view-log, and delete actions
  - Dark/light theme, auto-saved settings, native OS browse dialogs, toast notifications
- `launch_ui.py` — auto-installs Flask, opens browser, starts server on port 7823
- `launch_ui.bat` — Windows double-click launcher
- `requirements_ui.txt` — `flask>=3.0` UI dependency
- `ui/app.py` — Flask server with 18 API routes (SSE streams for live install + run output)
- `ui/bridge_runner.py` — subprocess manager that parses bridge log lines into SSE events
- `ui/setup_checker.py` — tool detection for Python, Aider, Ollama, Codex, Claude
- `ui/state_store.py` — JSON persistence for settings and run history
- `HOW_TO.md` — plain-English installation and usage guide for non-technical users
- `supervisor/agent.py` (`SupervisorAgent`) — new core class for planning + diff-driven review
- `executor/diff_collector.py` — captures `git diff HEAD` after each Aider run (capped 4000 chars)
- `context/repo_scanner.py` — builds live compact repo tree injected into supervisor prompts
- `AI_SUPERVISOR_PROMPT.md` — full reference for planning/review prompt specs and backends

### Changed
- **Architecture redesign**: strict Supervisor (plan + review only) / Aider (execute only) separation
- `main.py` rewritten — sequential acknowledgement-gated loop; no fallback on supervisor failure
- `supervisor/agent.py` replaces `planner/codex_client.py` as the active planning/review engine
- `executor/aider_runner.py` — added `--model` flag passthrough for local LLM selection
- `validator/validator.py` renamed to `MechanicalValidator`; quality assessment removed; kept file existence, Python syntax, and optional CI gate checks
- `models/task.py` — added `TaskReport`, `ReviewResult`; `BridgeConfig` gains `supervisor_command` and `aider_model`
- `parser/task_parser.py` — strict JSON-only; removed numbered-plan normalisation and Unity fallback lookups
- `README.md`, `AGENT_CONTEXT.md` — fully updated to reflect new architecture and UI

### Removed
- `planner/fallback_planner.py` active logic — now raises `NotImplementedError` directing to `--plan-file`
- Hardcoded Unity/game file lists from `codex_client.py` and `fallback_planner.py`
- Quality-assessment role from `validator/validator.py`

---

## [2026-03-20] — Initial Production Architecture

### Added
- Initial production-ready bridge app architecture: Codex planning, JSON task parsing, Aider execution, validation, retry feedback orchestration
- Modular package structure: planner, parser, executor, validator, context, models, logging, example plan, runnable CLI entry point
- Persistent project memory files: README, AGENT_CONTEXT, CHANGELOG
- Idea-driven planning via `--idea-file`, generated plan export via `--plan-output-file`
- Windows-friendly Codex CLI defaults (`codex.cmd exec`)
- Numbered-plan normalisation and Unity fallback planner for project briefs
- Hardened Codex and Aider command resolution (PATH + local Windows script directories)
- Improved execution retry handling so Aider startup failures surface clearly

### Verified
- CLI help, dry-run plan execution, and Python compilation in local workspace
- Dry-run planning against `GAME_IDEA.md` for a Unity project produces valid `bridge-plan.json`
