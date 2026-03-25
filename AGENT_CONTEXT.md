# AGENT_CONTEXT

## Architecture Summary

The bridge is a local CLI orchestrator with a strict two-role separation:

```
Supervisor Agent (Codex / Claude / any coding agent)
  Role: Tech Supervisor
  - Reads the live repo tree
  - Produces an atomic sequential JSON plan
  - Reviews each completed task's git diff
  - Returns PASS or REWORK (with replacement instruction)
  - NEVER writes code. NEVER executes tasks.

Bridge (this app)
  Role: Communication layer
  - Sends planning and review prompts to the supervisor
  - Runs Aider per task with task-scoped file list
  - Collects git diffs after each Aider run
  - Runs mechanical checks (no supervisor tokens)
  - Routes REWORK instructions back to Aider
  - NEVER makes coding or implementation decisions

Aider (local LLM)
  Role: Developer
  - Receives one atomic instruction + specific file paths
  - Applies code changes using a local model (Ollama, LM Studio, etc.)
  - Reports back via exit code + stdout/stderr
```

The loop is strictly sequential and acknowledgement-gated:
the supervisor must approve each task before the next one starts.

---

## Module Responsibilities

- `main.py`
  Orchestrates repo scanning, plan acquisition, the sequential task-review loop,
  logging, and CLI argument handling. No coding decisions.

- `supervisor/agent.py` (`SupervisorAgent`)
  Builds planning and review prompts, runs the supervisor CLI subprocess,
  parses PASS/REWORK responses. Replaces the old `CodexClient`.
  Hardcoded game-specific logic removed entirely — supervisor works from
  a live repo tree injected at runtime.

- `executor/aider_runner.py` (`AiderRunner`)
  Resolves the Aider executable, builds the Aider CLI command with local LLM
  `--model` flag, and captures stdout/stderr/exit code.

- `executor/diff_collector.py` (`DiffCollector`)
  Runs `git diff HEAD` after Aider completes. Returns a compact diff string
  (capped at 4000 chars) sent to the supervisor for review.

- `context/repo_scanner.py` (`RepoScanner`)
  Walks the target repo and produces a compact tree string (capped at 100 entries,
  4 levels deep, ignoring generated directories). Injected into supervisor planning
  prompts so the supervisor can reference real file paths.

- `context/file_selector.py` (`FileSelector`)
  Resolves task file paths relative to `repo_root` into absolute `Path` objects.
  Splits into existing and missing sets.

- `context/idea_loader.py` (`IdeaLoader`)
  Loads an optional product or architecture brief file. First 2000 chars injected
  into the supervisor planning prompt.

- `parser/task_parser.py` (`TaskParser`)
  Extracts and validates the supervisor's JSON plan. Strips code fences. Rejects
  numbered plans, absolute paths, and clarification-request tasks. No fallback
  file path inference or hardcoded lookups.

- `validator/validator.py` (`MechanicalValidator`)
  Fast token-free structural checks: file existence (create/modify tasks), Python
  syntax compilation, optional CI gate command. Quality review is the supervisor's job.

- `utils/command_resolution.py`
  Resolves executables from PATH, `.venv\Scripts`, `venv\Scripts`, `aider-env\Scripts`,
  and the current Python interpreter's directory.

- `models/task.py`
  Dataclasses: `Task`, `SelectedFiles`, `ExecutionResult`, `ValidationResult`,
  `TaskReport` (task + result + diff), `ReviewResult` (PASS/REWORK + new instruction),
  `BridgeConfig`.

- `bridge_logging/logger.py`
  Configures console and file logging to `logs/bridge-app.log`.

- `planner/codex_client.py`
  Backwards-compat shim. Re-exports `SupervisorAgent` as `CodexClient`.
  Original game-specific prompt logic removed.

- `planner/fallback_planner.py`
  Removed. Raises `NotImplementedError` with a clear message directing the user
  to `--plan-file`.

---

## File Structure

```text
main.py
supervisor/         SupervisorAgent — plan and review
executor/           AiderRunner + DiffCollector
parser/             TaskParser
validator/          MechanicalValidator
context/            FileSelector + IdeaLoader + RepoScanner
utils/              Command resolution
models/             Dataclasses
bridge_logging/     Logger
planner/            Compat shims only (no active logic)
logs/
AI_SUPERVISOR_PROMPT.md
README.md
CHANGELOG.md
AGENT_CONTEXT.md
requirements.txt
example plan.json
```

---

## Execution Flow

```
main()
  1. Parse CLI args
  2. Load idea file (optional, injected into planning)
  3. Scan repo tree → compact tree string
  4. If --plan-file: load tasks from file
     Else: supervisor.generate_plan(goal, tree, idea) → tasks
           Retry with parse error as feedback on failure
           Raise if all attempts exhausted (no fallback)
  5. For each task (sequential):
     a. FileSelector resolves file paths
     b. AiderRunner executes instruction on local LLM
     c. DiffCollector captures git diff
     d. MechanicalValidator: file existence + Python syntax + CI gate
        → on failure: retry same instruction (no supervisor tokens)
     e. SupervisorAgent.review_task(TaskReport) → ReviewResult
        PASS   → move to next task
        REWORK → retry with supervisor's new instruction
        → on retries exhausted: raise RuntimeError
  6. Print JSON summary {"status": "success", "tasks": N}
```

---

## Token Economy

| Event | Supervisor called |
|---|---|
| Plan generation | Yes — once per run (+ retries on parse failure) |
| Mechanical check failure | No — same instruction, no tokens spent |
| Aider exit code != 0 | No — diff still sent to supervisor in next step |
| Task review | Yes — one call per task |
| REWORK retry | Yes — one call per rework cycle |

---

## Key Design Rules

1. **Supervisor never codes.** Planning instructions say what to build, never how.
2. **No fallback planner.** If the supervisor fails, the operator uses `--plan-file`.
3. **Sequential + acknowledgement-gated.** Each task is reviewed and approved before the next starts.
4. **Diff-driven review.** The supervisor sees the actual git diff, not just exit codes.
5. **Mechanical failures are free.** File existence and syntax checks don't call the supervisor.
6. **Aider uses local LLM.** Configured via `--aider-model` (e.g. `ollama/mistral`).
7. **No hardcoded paths.** The supervisor receives the live repo tree every run.

---

## Configuration Reference

| CLI flag | Env var | Purpose |
|---|---|---|
| `--supervisor-command` | `BRIDGE_SUPERVISOR_COMMAND` | Supervisor agent CLI |
| `--aider-command` | `BRIDGE_AIDER_COMMAND` | Aider executable |
| `--aider-model` | `BRIDGE_AIDER_MODEL` | Local LLM model for Aider |
| `--validation-command` | `BRIDGE_DEFAULT_VALIDATION` | Optional CI gate command |

---

## Pending Improvements

- Add unit tests for SupervisorAgent prompt building, review parsing, and MechanicalValidator
- Add resumable run state so interrupted multi-task sessions can be resumed
- Support streaming supervisor output for long planning responses
- Add configurable allowlists or denylists for editable paths
- Support multiple supervisor backends via a plugin interface
