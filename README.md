# Codex Aider Bridge App

`codex-aider-bridge-app` is a CLI bridge that turns a high-level development goal into an execution loop:

`Codex -> Plan -> Aider -> Execute -> Validate -> Feedback`

The app is designed for local development and generic codebases, including Unity, WPF, and standard Python or mixed-language repositories. It keeps planning and execution decoupled so the planner can stay focused on small JSON tasks while Aider applies file-level changes.

## Features

- Codex-based planner that requests atomic JSON tasks
- Strict JSON task parsing and validation
- File selection layer for task-scoped execution
- Aider execution wrapper with retry support
- Validation layer with file existence checks, optional custom commands, and Python syntax compilation
- Persistent file and console logging
- Incrementally maintained project memory via `README.md`, `CHANGELOG.md`, and `AGENT_CONTEXT.md`

## Project Structure

```text
bridge-app/
|-- main.py
|-- planner/
|   |-- __init__.py
|   `-- codex_client.py
|-- executor/
|   |-- __init__.py
|   `-- aider_runner.py
|-- parser/
|   |-- __init__.py
|   `-- task_parser.py
|-- validator/
|   |-- __init__.py
|   `-- validator.py
|-- context/
|   |-- __init__.py
|   `-- file_selector.py
|-- models/
|   |-- __init__.py
|   `-- task.py
|-- bridge_logging/
|   |-- __init__.py
|   `-- logger.py
|-- logs/
|   `-- .gitkeep
|-- example plan.json
|-- requirements.txt
|-- README.md
|-- CHANGELOG.md
`-- AGENT_CONTEXT.md
```

## Requirements

- Python 3.10+
- `codex` CLI installed and authenticated
- `aider` CLI installed and authenticated

No external Python packages are required for the bridge itself.

## Setup

1. Ensure `python`, `codex`, and `aider` are available in your shell.
2. From the repository root, optionally create and activate a virtual environment.
3. Review configuration options:
   - `BRIDGE_CODEX_COMMAND`
   - `BRIDGE_AIDER_COMMAND`
   - `BRIDGE_DEFAULT_VALIDATION`
4. Run the bridge:

```bash
python main.py "Build a logging system feature"
```

## How It Works

1. `main.py` receives a high-level goal.
2. `planner/codex_client.py` asks Codex for a strict JSON plan using atomic tasks.
3. `parser/task_parser.py` validates and converts the JSON into typed task models.
4. `context/file_selector.py` resolves task file paths relative to the target repository.
5. `executor/aider_runner.py` sends each task to Aider with the task-specific files.
6. `validator/validator.py` checks the result:
   - task files exist when expected
   - optional validation command passes
   - Python files compile when present
7. If execution or validation fails, the failure is sent back to the planner for a refined retry.

## CLI Usage

```bash
python main.py "Build a logging system feature" --dry-run
python main.py "Add telemetry configuration" --max-plan-attempts 3 --max-task-retries 2
python main.py "Refactor settings loading" --repo-root "H:\\AnotherProject" --validation-command "python -m pytest"
```

### Common Options

- `goal`: High-level user request. Defaults to `Build a logging system feature` if omitted.
- `--repo-root`: Repository to operate on. Defaults to the current working directory.
- `--dry-run`: Generate and parse the plan without invoking Aider.
- `--plan-file`: Execute tasks from an existing JSON plan instead of calling Codex.
- `--max-plan-attempts`: Retry count for invalid planner output.
- `--max-task-retries`: Retry count for failed Aider or validation steps.
- `--validation-command`: Optional command run after each task.
- `--log-level`: Logging verbosity.

## Planner JSON Contract

Codex is instructed to return only this shape:

```json
{
  "tasks": [
    {
      "id": 1,
      "files": ["file1.cs"],
      "instruction": "Do specific change",
      "type": "modify"
    }
  ]
}
```

The parser accepts only non-empty task lists with integer `id`, string `instruction`, string `type`, and a non-empty `files` array of strings.

## Example Plan File

See [`example plan.json`](./example%20plan.json) for a ready-made plan that targets a logging feature.

## Notes

- The bridge keeps commands configurable because Codex CLI invocation patterns can vary by environment.
- If `codex` is unavailable, use `--plan-file` to run the executor loop against a hand-authored plan.
- The bridge does not depend on a specific project type and can target any repo reachable from `--repo-root`.
- Verified locally in this workspace:
  - `python main.py --help`
  - `python main.py --plan-file "example plan.json" --dry-run`
  - `python -m compileall .`
