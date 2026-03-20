# AGENT_CONTEXT

## Architecture Summary

The bridge app is a local CLI orchestrator that converts a high-level goal into a structured execution loop:

`Codex -> Plan -> Aider -> Execute -> Validate -> Feedback`

The planner is responsible only for producing strict JSON tasks. The executor is responsible only for applying those tasks against specific files. Validation happens after each task so failures can be fed back to the planner for a refined retry.

## Module Responsibilities

- `main.py`
  Orchestrates planning, parsing, execution, validation, retries, logging, and CLI argument handling.
- `planner/codex_client.py`
  Builds planner prompts, runs the Codex CLI, and requests plan or retry refinements.
- `parser/task_parser.py`
  Extracts JSON from raw model output, validates schema, and returns typed task objects.
- `executor/aider_runner.py`
  Runs Aider against task-scoped files and captures stdout, stderr, exit code, and executed command.
- `context/file_selector.py`
  Resolves task file paths relative to the repository root and reports existing vs missing files.
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
- Parses and validates plan JSON safely
- Executes atomic tasks with Aider
- Retries failed tasks with planner feedback
- Supports dry-run and plan-file execution modes
- Writes structured logs to console and `logs/bridge-app.log`
- Supports optional custom validation commands per task
- Works against generic repositories via `--repo-root`
- Verified locally for CLI help, dry-run plan execution from `example plan.json`, and project-wide Python compilation

## Pending Improvements

- Add unit tests for planner parsing, retry logic, and validator behavior
- Add richer diff-based validation to verify intended file changes occurred
- Support alternative planner backends beyond Codex CLI
- Add resumable run state for long multi-task sessions
- Add configurable allowlists or denylists for editable paths
