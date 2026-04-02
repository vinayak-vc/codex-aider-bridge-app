# Changelog

---

## [2026-04-02] - Chat Drawer Persistence and AI Relay Restore

### Added
- Global right-side chat drawer available across the UI instead of a page-only chat flow
- Per-project chat persistence so old conversations restore after app restart when the same project is selected
- Chat controls for `New Chat` and `Stop`
- Server-backed AI Relay UI state persistence for generated or imported task plans
- Relay task status badges for `Not started`, `Running`, `Waiting review`, `Done`, `Failed`, `Rework`, `Retrying`, and `Stopped`

### Changed
- Chat now continues while the user navigates to other pages and reconnects when the drawer is reopened
- AI Relay now restores imported tasks and task statuses after restart
- AI Relay reopens on **Confirm Tasks** after restart unless there is a live run that can resume into **Run & Review**
- Left sidebar Chat navigation removed in favor of the persistent drawer

### Fixed
- Chat no longer resets when the user changes panels
- Relay restore no longer traps the UI on stale **Run & Review** state after app restart
- AI Relay manual-supervisor artefacts are now isolated by per-plan session id, preventing task-id collisions across different imported plans

## [2026-04-01] — Multi-page Web UI, Chat Feature, AI Relay Spec

### Added — Web UI Rebuild (M1–M8, complete)
- **Multi-page Flask app** replacing the single-file `index.html`:
  - `/dashboard` — live SVG progress ring, task feed, pause/resume, review panel with diff viewer
  - `/run` — full config form, supervisor selector, live log terminal, command preview
  - `/knowledge` — AI_UNDERSTANDING.md viewer, file registry with sort/filter/pagination
  - `/history` — searchable/filterable run table, log modal, re-run
  - `/tokens` — token analytics, savings bar, session chart and detail panel
  - `/setup` — dependency check cards, Aider install terminal, Ollama model manager
- **Design system** — CSS custom properties in `tokens.css`, dark default, light mode toggle persisted in `localStorage`
- **Sidebar navigation** — fixed 220px sidebar, active state, run status chip, issue badge on Setup
- **SSE infrastructure** — `SSEClient` class with auto-reconnect, reactive `store.js`, `api.js` fetch wrapper
- **Inline markdown renderer** — ~80-line vanilla JS renderer in `knowledge.js` (no external library)
- **Keyboard shortcuts** — `g+d/r/k/h/t/s/c` navigation chords, `?` help overlay, `Ctrl+Enter` to launch run
- `/legacy` route removed; `/` redirects to `/dashboard`

### Added — Chat Page (`/chat`, shortcut `g+c`)
- Conversational AI interface powered by local Ollama model
- Token-by-token streaming via `fetch` ReadableStream + SSE
- Project-aware system prompt: file roles, patterns, language, type injected from last bridge scan
- Inline markdown renderer for assistant replies (headings, bold/italic, code blocks, lists)
- Welcome screen with 4 suggestion chips; auto-resizing textarea
- Enter to send / Shift+Enter for newline
- Limitations banner always visible: no file editing, no internet, history not persisted on refresh
- Blocks gracefully when a non-Ollama model is configured (API keys not managed by UI)

### Added — Run Page Compatibility Warnings
- **Supervisor warnings** — live banner per selected supervisor tile:
  - Codex: warns `OPENAI_API_KEY` required — ChatGPT Plus/Pro does NOT include API access
  - Claude: confirms Claude Pro works via `claude login` (OAuth, no separate API key)
  - Cursor / Windsurf: confirms IDE subscription required, IDE must be installed
  - Manual: no account or API key needed
- **Aider model warnings** — live banner per model input:
  - `gpt-*` models: warns OpenAI API key required (separate from ChatGPT Plus)
  - `claude-*` models: warns Anthropic API key required (separate from Claude Pro)
  - `ollama/*` models: no warning — fully local and free

### Added — Onboarding Scanner
- `utils/onboarding_scanner.py` — one-time source scan on first run against an existing project
- Extracts file roles from docstrings / class names / function names (Python via `ast`, JS/TS/C#/Go via regex)
- Detects dominant language, project type (unity/node/python/go/dotnet), architectural patterns
- 500-file cap with fair depth-based sampling
- `--skip-onboarding-scan` flag; `BridgeConfig.skip_onboarding_scan` field

### Added — Project Understanding Bootstrap
- `context/project_understanding.py` — discovers markdown docs, writes `bridge_progress/AI_UNDERSTANDING.md`
- `understanding_confirmed` field in project knowledge schema
- `docs[]` and `clarifications[]` fields in project knowledge

### Fixed — UnicodeEncodeError on Windows cp1252 consoles
- `main.py` — `_fix_windows_encoding()` reconfigures stdout/stderr to UTF-8 with `errors='replace'` at startup
- `utils/project_type_prompt.py` — replaced `─`, emoji chars with ASCII fallbacks
- `launch_ui.py` — replaced `…` with `...`

### Documented — AI Relay Supervisor
- `AI_RELAY_SPEC.md` — full implementation spec for the upcoming AI Relay supervisor mode
  (use any web AI as supervisor via copy-paste — no API key required)

---

## [2026-04-01] — Git Readiness and Auto-Commit

### Added
- Git-readiness preview in pre-flight:
  - whether the target repo is git-backed
  - whether `HEAD` exists
  - current branch
  - clean vs dirty worktree
  - staged / unstaged / untracked counts
  - next bridge action
- Interactive recovery for non-git target repos:
  - stop and let the user create the repo
  - or initialize the local git repo and baseline commit automatically
- Per-task auto-commit after approval so each completed task lands in a small local git commit
- `task_metrics.json` now stores per-task `commit_sha` values when auto-commit succeeds

### Changed
- The bridge now refuses to execute tasks against non-git target projects
- Pre-flight now guarantees the target repo has a valid `HEAD` before diff collection starts

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
