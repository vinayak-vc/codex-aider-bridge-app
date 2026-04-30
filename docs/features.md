# Codex Aider Bridge App Features

This document lists the currently implemented features of `codex-aider-bridge-app` based on the code in this repository.

## 1. Core Product Purpose

- Bridges AI planning and review with local AI code execution.
- Separates responsibilities into:
  - Supervisor: creates plans and reviews output.
  - Bridge: orchestrates execution, persistence, validation, and recovery.
  - Aider: performs code edits in the target repository using a local model.
- Designed for local-first workflows where the target repository receives all progress artifacts.

## 2. CLI Run Modes

### Standard workflow

- Accepts a high-level goal and can request a task plan from a supervisor command.
- Executes tasks sequentially.
- Runs validation after each task.
- Can send each completed task to a supervisor for PASS or REWORK review.

### Manual-supervisor workflow

- Uses filesystem-based review instead of calling an external supervisor CLI.
- Writes review requests into:
  - `bridge_progress/manual_supervisor/requests/`
- Waits for matching decision JSON files in:
  - `bridge_progress/manual_supervisor/decisions/`
- Supports three review outcomes:
  - `pass`
  - `rework`
  - `subplan`
- Archives consumed request and decision files after processing.

### Auto-approve workflow

- Skips supervisor review and relies on mechanical validation only.
- Still records diffs, token usage, and progress artifacts.
- Emits structured review summaries so another tool or agent can inspect the run externally.

### Dry-run workflow

- Generates and parses the task plan without invoking Aider or the task review loop.
- Useful for plan inspection and debugging.

### Resume workflow

- Can resume a prior run from saved checkpoint data.
- Skips tasks already marked completed in `bridge_progress/checkpoint.json`.

## 3. Planning Features

### Plan generation

- Can ask a supervisor to generate a JSON task plan from:
  - goal
  - compact repo tree
  - optional brief or idea file
  - optional project knowledge cache
- Retries plan generation when the supervisor returns invalid output.
- Can write the generated plan to a file for reuse.

### Plan schema and parsing

- Expects a strict JSON root object with a `tasks` array.
- Supports task fields:
  - `id`
  - `files`
  - `instruction`
  - `type`
  - `context_files`
  - `must_exist`
  - `must_not_exist`
- Accepts task types:
  - `create`
  - `modify`
  - `delete`
  - `validate`
- Strips markdown code fences from supervisor output.
- Extracts JSON by brace matching if the supervisor wraps it in extra text.
- Coerces string task IDs like `task-3` into integers when possible.
- Rejects:
  - absolute paths
  - path traversal
  - malformed drive-relative paths
  - root-level non-specific file targets
  - clarification requests instead of implementation work

### Workflow profiles

- Supports `standard` and `micro` workflow profiles.
- `micro` profile enforces:
  - one file per task
  - one concern per task
  - required `must_exist` for create tasks
  - required `must_not_exist` for delete tasks
  - stricter, surgical task decomposition for small local models

### Plan preview

- Can show a pre-execution task preview and ask for confirmation before any task runs.

### Auto-splitting large tasks

- Can automatically split multi-file tasks into single-file sub-tasks.
- Preserves the original instruction and prepends a file-specific focus directive.
- Uses a synthetic sub-task ID scheme:
  - `parent_id * 1000 + index`
- Intended to help smaller local models stay focused on one file at a time.

## 4. Supervisor Features

### External supervisor integration

- Can run an external supervisor command to:
  - generate plans
  - review completed work
  - generate corrective subplans
- Supports command templating for output files and prompt handling.
- Distinguishes between:
  - exec-style commands that accept prompt text as an argument
  - commands that should receive the prompt on stdin
- Handles command resolution and subprocess timeouts.

### Review contract

- Task review returns one of:
  - `PASS`
  - `REWORK: <replacement instruction>`
- Converts the response into a typed review result for the execution loop.

### Corrective subplans

- Can request 1 to 3 corrective sub-tasks when a task fails mechanical validation.
- Restricts sub-tasks to the parent task’s file set.
- Parses supervisor subplan JSON into typed `SubTask` objects.

## 5. Aider Execution Features

### Structured task execution

- Runs Aider as a subprocess for each task.
- Builds a structured message that includes:
  - overall goal
  - current task number
  - total task count
  - already completed task summaries
  - exact target file paths
  - execution rules

### Explicit file scoping

- Passes target files using `--file`.
- Repeats the exact absolute paths in the prompt so smaller models do not drift to wrong files.
- Supports tasks with read-only context files via `--read`.

### Standards injection

- Automatically discovers and injects repo-level standards files when present:
  - `CODE_FORMAT_STANDARDS.md`
  - `CODING_STANDARDS.md`
  - `STYLE_GUIDE.md`
  - `.editorconfig`
  - `CONTRIBUTING.md`

### Reference-file injection

- Detects file paths mentioned inside a task instruction.
- Adds referenced existing files as read-only context if they are not task targets.

### Non-interactive operation

- Passes flags to suppress interactive Aider behavior:
  - `--yes-always`
  - `--no-pretty`
  - `--no-stream`
  - `--no-auto-lint`
  - `--no-auto-commits`
  - `--no-gitignore`
  - `--no-show-model-warnings`
- Can disable repo-map scanning with `--map-tokens 0`.

### Silent failure detection

- Detects cases where Aider exits successfully but:
  - does not create or modify the required files
  - only changes whitespace or comments
- Converts those cases into failed execution results so they do not pass as successful edits.

### Interactive prompt detection

- Scans Aider stdout and stderr for signs that it asked an interactive question during automation.
- Treats detected interactive prompts as failures.

### Timeout and encoding handling

- Applies per-task subprocess timeouts.
- Forces UTF-8-related environment settings for the Aider subprocess on Windows to reduce console encoding failures.

## 6. File Selection and Repo Context

### Repo tree scanning

- Generates a compact file tree for supervisor planning.
- Ignores heavy or irrelevant directories such as:
  - `.git`
  - `node_modules`
  - `Library`
  - `Temp`
  - `build`
  - `dist`

### Read-only context support

- Supports `context_files` per task for read-only reference injection.
- Keeps reference and target files separate.

## 7. Validation Features

### Mechanical validation pipeline

- Runs fast, token-free checks after each Aider task.
- Validation order:
  - file existence
  - task assertions
  - language-aware syntax checks
  - optional CI gate command

### File existence and assertions

- Verifies create and modify tasks produced the expected files.
- Verifies delete tasks removed the expected files.
- Enforces `must_exist` and `must_not_exist` assertions.

### Project type detection

- Detects project type automatically from repo markers.
- Supports explicit project-type override via CLI and saved knowledge.
- Recognizes at least:
  - Unity
  - C#
  - Python
  - TypeScript
  - JavaScript

### C# and Unity validation

- Scans C# files for LLM artifact tokens.
- Performs brace-balance checks.
- Runs `dotnet build` for plain C# repositories when available.
- For Unity projects, can query a Unity MCP HTTP server for compiler errors.
- Gracefully skips Unity live compilation checks if the MCP server is unavailable.

### Python validation

- Uses `python -m compileall -q` against changed Python files.

### JavaScript validation

- Uses `node --check` when Node.js is available.

### TypeScript validation

- Prefers `tsc --noEmit --skipLibCheck` for full checks.
- Falls back to `node --check` when `tsc` is unavailable.

### Optional CI gate

- Can run a configured validation command after each task.
- Parses the command safely and runs it without shell mode.
- Treats non-zero exit or timeout as validation failure.

## 8. Diff and Review Artifacts

### Git diff collection

- Collects a compact diff after each task using Git.
- Includes changed-file stats plus unified diff text.
- Falls back for repositories with no commits yet.
- Truncates large diffs to a bounded size for prompt safety.

### Task reports

- Combines:
  - task definition
  - execution result
  - diff
- Uses this report for supervisor review and manual review requests.

## 9. Progress Persistence and Recovery

### Project-scoped progress storage

- Stores run artifacts inside the target repo under `bridge_progress/`.
- Keeps different target projects isolated from each other.

### Stored artifacts

- `bridge_progress/project_knowledge.json`
- `bridge_progress/project_snapshot.json`
- `bridge_progress/task_metrics.json`
- `bridge_progress/token_log.json`
- `bridge_progress/LATEST_REPORT.md`
- `bridge_progress/last_run.json`
- `bridge_progress/checkpoint.json`
- `bridge_progress/manual_supervisor/...`

### Checkpointing

- Saves completed task IDs after every successful task.
- Clears the checkpoint after a fully successful run.
- Migrates legacy checkpoint files from the repo root if needed.

### Pause and resume between tasks

- Uses a `.bridge_pause` file in the target repo to pause execution between tasks.
- Can resume when the file is removed.

### Rollback reference

- Records a rollback point at run start and logs how to reset to that commit if the run fails.

## 10. Project Knowledge Features

### Knowledge cache

- Maintains `project_knowledge.json` as a reusable project memory file.
- Stores:
  - project metadata
  - file roles
  - detected patterns
  - completed feature labels
  - run history

### Knowledge updates after runs

- Registers every touched file with a role summary.
- Updates the project summary if missing.
- Adds completed feature labels.
- Records run history with status and task counts.

### Prompt-ready project summary

- Converts the knowledge cache into compact text for supervisor prompts.
- Includes:
  - project summary
  - file registry
  - code patterns
  - already implemented features
  - suggested next steps
  - last run information

## 11. Onboarding Scanner

### First-run project scan

- Runs automatically when project knowledge is empty unless explicitly skipped.
- Scans up to 500 text-like source files.
- Ignores heavy, generated, and tool-specific directories.

### File role inference

- Extracts file role summaries using language-aware heuristics:
  - Python via AST
  - JavaScript and TypeScript via regex
  - C# via regex
  - Go via regex
  - generic fallback for other text files

### Pattern detection

- Detects recurring inheritance patterns.
- Detects framework usage from imports.
- Detects test suites from filenames.
- Detects Unity MonoBehaviour-heavy architectures.

### Project metadata inference

- Infers dominant language.
- Infers project type from repo markers and language distribution.
- Updates the knowledge cache with scan results and a generated summary.

## 12. Token Tracking

### Session accounting

- Tracks token usage for:
  - plan generation
  - review cycles
  - subplans
  - session totals

### Exact and estimated modes

- Can accept exact session token counts from the caller.
- Can estimate tokens from file sizes when exact counts are not provided.

### Reporting

- Builds per-session token reports.
- Persists token history to `bridge_progress/token_log.json`.
- Emits structured token-report events for the UI.

## 13. Web UI Features

### Flask-based local UI

- Provides a browser UI under `ui/`.
- Supports both source execution and bundled execution via PyInstaller.

### Setup and environment checks

- Checks availability of:
  - Python
  - Aider
  - Ollama
  - Codex CLI
  - Claude CLI
- Can list available Ollama models.
- Can stream installation of `aider-chat`.
- Can stream `ollama pull <model>`.

### Native file and folder browsing

- Opens OS-native dialogs for:
  - selecting a project folder
  - selecting an idea or brief file

### Run management

- Starts and stops bridge runs from the UI.
- Returns current run status, task list, and logs.
- Stores the current settings for reuse.

### Real-time event streaming

- Uses server-sent events to stream live bridge updates to the browser.
- Broadcasts:
  - run start
  - log lines
  - task updates
  - diffs
  - progress
  - review-required state
  - pause and resume events
  - token reports
  - completion and error states

### Manual review from UI

- Reads the current pending manual-supervisor request.
- Lets the user submit review decision JSON through an API endpoint.

### Run history

- Persists run history entries.
- Supports:
  - listing history
  - filtering by status
  - text search by goal
  - entry deletion
  - full history clear

### Report viewing

- Can read project-scoped reports from `bridge_progress/`:
  - token log
  - project knowledge
  - last run summary

## 14. Packaging and Distribution Support

- Includes support files for Windows packaging and launch:
  - `bridge.spec`
  - `build.bat`
  - `installer.iss`
  - `launch_ui.py`
  - `launch_ui.bat`
- UI code supports PyInstaller bundled template resolution.

## 15. Current Functional Limits Visible in Code

- Manual-supervisor mode requires an explicit `--plan-file`.
- Subplan generation exists in the supervisor and manual-review model, but the exact execution path should still be verified end-to-end against current orchestration behavior for every branch of failure handling.
- Diff review is compact and truncated, not a full semantic analysis layer.
- Validation coverage is strongest for Unity, C#, Python, JavaScript, and TypeScript. Other project types mostly fall back to generic behavior.
- The UI manages one local run at a time through a singleton run manager.

---

## 16. Web UI — Multi-Page Application

The UI was fully rebuilt from a single `index.html` into a multi-page Flask application.

### Pages
- `/dashboard` — live progress ring, task feed, pause/resume, review panel, project status cards
- `/run` — split-panel layout: settings/log tabs (left) + task progress panel (right) with drag-to-resize. Parsed/Raw log views with tag filters. Pre-flight checklist, goal templates, cost estimator.
- `/knowledge` — Overview, AI Understanding viewer, File Registry with tree view + file type icons + click-to-open in VS Code. Refresh/auto-refresh knowledge.
- `/history` — searchable run table, log modal, re-run, delete
- `/tokens` — 6 stat chips (including Aider tokens), savings comparison card, per-task breakdown, diagnostics panel
- `/git` — branch selector, commit history with bridge badges, changed files tree view, diff viewer, gitignore management
- `/setup` — dependency checks, Aider install, Ollama model manager, GPU status + process manager + benchmark + Free VRAM

### Frontend Infrastructure
- CSS custom properties design system (`tokens.css`) — dark default, `[data-theme="light"]` override, persisted in `localStorage`
- Vanilla ES modules only — no npm, no bundler, no framework
- `SSEClient` class: auto-reconnects every 3 s, dispatches typed events (`start`, `task_update`, `progress`, `complete`, `paused`, `resumed`, `review_required`, etc.)
- Reactive `store.js`: key-based subscription pattern, `get/set/subscribe/snapshot`
- `api.js`: `apiFetch`, `apiPost`, `apiDelete` with normalised error handling
- `toast.js`: non-blocking notifications (success / error / info / warning)
- `shortcuts.js`: `g+d/r/k/h/t/s/g/c` navigation chords with 1.5 s timeout, `?` help overlay, ignored inside inputs/textareas
- `action-log.js`: records all UI clicks, API calls, SSE events for debugging
- All icons: heroicons-style inline SVG — no emoji, no icon font

### Supervisor & Model Compatibility Warnings (Run page)
- Live banner per supervisor tile: Codex warns API key required; Claude confirms Pro works via OAuth; Cursor/Windsurf confirm subscription required; Manual confirms no account needed
- Live banner per model input: `gpt-*` and `claude-*` models warn that web subscriptions (Plus/Pro) do not include API access; `ollama/*` models show no warning

---

## 17. Chat Feature

Current behavior update:
- Chat now opens from a persistent right-side drawer instead of behaving like a page-local conversation
- Chat history is stored per project and restored after app restart when the same project is selected
- Active assistant responses can continue while the user changes pages and reconnect when the drawer is reopened
- `New Chat` clears the selected project's thread
- `Stop` interrupts the active response

Local conversational AI integrated directly into the web UI at `/chat` (shortcut `g+c`).

- Powered by the configured Ollama model — fully local, no API key
- Project-aware system prompt: file roles, patterns, language, type injected from `project_knowledge.json`
- Token-by-token streaming via `fetch` ReadableStream parsing SSE chunks
- Inline markdown renderer (no library): h1–h3, bold/italic, inline code, fenced code blocks, unordered/ordered lists, blank-line spacing
- Conversation history maintained in JS memory (cleared on page refresh — intentional, disclosed)
- Welcome screen with 4 suggestion chips; Enter to send / Shift+Enter for newline; auto-resizing textarea
- Dismissible limitations banner always shown on first load
- Gracefully blocks and explains when a non-Ollama model is configured (API keys not managed)
- `/api/chat` POST endpoint: validates Ollama prefix, builds system prompt with knowledge context, proxies to `http://localhost:11434/api/chat` via `urllib.request`, streams tokens as SSE

### What Chat can do
- Answer questions about project architecture and file structure
- Help plan a feature before committing to a Run
- Debug issues by discussing codebase patterns
- Suggest what goal text to enter in the Run tab

### What Chat cannot do
- Edit files (use the Run tab)
- Browse the internet
- Persist conversation history across page refreshes
- Use `gpt-*` or `claude-*` models without an external API key

---

## 18. Universal Pipeline

All supervisor types now use `--manual-supervisor` under the hood. A `SupervisorProxyThread` in the UI backend polls for review requests and dispatches to the correct supervisor (CLI for Claude/Codex, SSE for Chatbot/Manual).

- Mid-run supervisor switching without restart
- Chatbot inline relay wizard on the Run page (copy-paste review)
- Plan generation uses the selected supervisor CLI, not Ollama
- Role indicator strip: Planner/Reviewer status during runs

## 19. Escalating Retry Strategy

10-attempt retry system with progressive intelligence:

- Attempts 1-3: Standard retries with original instruction
- Attempts 4-6: Simplified instruction with accumulated failure context
- Attempt 7: Supervisor diagnostic — analyzes all failures, rewrites instruction
- Attempts 8-9: Diagnostic-informed retries
- Attempt 10: Supervisor takeover prompt — user chooses to let supervisor write code

Failure reasons accumulated and stored in `RUN_DIAGNOSTICS.json` for product improvement.

## 20. Read/Investigate Task Types

- `read` type: reads files, sends content to supervisor for analysis. No Aider invoked.
- `investigate` type: reads files + discovers related imports, deep analysis by supervisor.
- Smart goal routing classifies goals before plan generation (read/investigate/code).

## 21. Git Page

Dedicated `/git` page with:
- Branch selector with create/switch
- Commit history with bridge task badges
- Changed files list with tree view
- Inline diff viewer
- Add-to-gitignore action

## 22. Run Diagnostics

`RUN_DIAGNOSTICS.json` written after every run with:
- Per-task attempt timeline with failure reasons
- Blocking pattern detection (interactive prompts, timeouts, validation loops)
- AI-readable summary with actionable suggestions
- Escalation log for product improvement data

## 23. Product Telemetry

Local-only usage analytics in `telemetry.json`:
- Run lifecycle events (start, complete, fail, resume)
- Task events (pass, fail, timeout, rework)
- Page views and feature usage
- AI analysis prompt included for automated improvement recommendations

## 24. GPU Management

- GPU status detection via nvidia-smi (VRAM, utilization, Ollama backend)
- GPU process manager on Setup page with Kill button
- Model speed benchmark (tok/s measurement)
- "Free VRAM" button to unload Ollama model
- Status bar GPU indicator on all pages
- Model advisor recommends best model for system specs
