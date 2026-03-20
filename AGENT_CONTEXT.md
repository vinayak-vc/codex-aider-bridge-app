# AGENT_CONTEXT

## Architecture Summary

The bridge app is a local CLI orchestrator that converts a high-level goal into a structured execution loop:

`Codex -> Plan -> Aider -> Execute -> Validate -> Feedback`

The planner is responsible for producing Aider-ready atomic tasks from a goal plus optional idea-file context. The executor is responsible only for applying those tasks against specific files. Validation happens after each task so failures can be fed back to the planner for a refined retry. If Codex planning is not actionable, the bridge falls back to a deterministic local planner.

## Module Responsibilities

- `main.py`
  Orchestrates planning, idea-file loading, parsing, execution, validation, retries, fallback planning, logging, and CLI argument handling.
- `planner/codex_client.py`
  Builds planner prompts, summarizes idea-file context, runs the Codex CLI, captures planner output through a file, and requests plan or retry refinements.
- `planner/fallback_planner.py`
  Builds deterministic Aider-ready plans from the goal and idea context when Codex planning retries are exhausted.
- `parser/task_parser.py`
  Extracts JSON from raw model output, validates schema, and can normalize numbered technical plans into typed tasks when file targets are recoverable.
- `executor/aider_runner.py`
  Runs Aider against task-scoped files and captures stdout, stderr, exit code, and executed command.
- `context/file_selector.py`
  Resolves task file paths relative to the repository root and reports existing vs missing files.
- `context/idea_loader.py`
  Loads product or architecture briefs such as `GAME_IDEA.md` for planning.
- `validator/validator.py`
  Ensures target files exist, optionally runs a project-specific validation command, and compiles Python sources when relevant.
- `models/task.py`
  Holds dataclasses for tasks, execution results, validation results, selected file sets, and runtime config.
- `bridge_logging/logger.py`
  Configures console and file logging under `logs/bridge-app.log`.

## File Structure

```text
main.py
planner/
parser/
executor/
validator/
context/
models/
bridge_logging/
logs/
README.md
CHANGELOG.md
AGENT_CONTEXT.md
requirements.txt
example plan.json
```

## Current Capabilities

- Generates plans from Codex with strict JSON instructions
- Loads project idea files and injects them into planning prompts
- Parses and validates plan JSON safely
- Converts recoverable numbered Codex plans into typed tasks
- Executes atomic tasks with Aider
- Retries failed tasks with planner feedback
- Supports dry-run and plan-file execution modes
- Supports writing generated plans to disk
- Falls back to deterministic local planning when Codex output is not actionable
- Writes structured logs to console and `logs/bridge-app.log`
- Supports optional custom validation commands per task
- Works against generic repositories via `--repo-root`
- Verified locally for CLI help, dry-run plan execution from `example plan.json`, dry-run planning from `GAME_IDEA.md` into a Unity `bridge-plan.json`, and project-wide Python compilation

## Pending Improvements

- Add unit tests for planner parsing, retry logic, and validator behavior
- Add richer diff-based validation to verify intended file changes occurred
- Support alternative planner backends beyond Codex CLI
- Add resumable run state for long multi-task sessions
- Add configurable allowlists or denylists for editable paths
