# AI Supervisor Prompt Reference

This document describes how the bridge communicates with the supervisor agent
(Codex, Claude CLI, or any coding agent), what prompts are sent, and how to
configure different backends.

---

## Supervisor Role

The supervisor is a **Tech Supervisor**, not a developer. It has two and only two jobs:

1. **Plan** — decompose a high-level goal into an atomic sequential JSON task list for Aider.
2. **Review** — inspect each completed task's git diff and return `PASS` or `REWORK`.

The supervisor **never writes code**, never executes commands, and never makes runtime decisions. All coding is done by Aider using a local LLM.

---

## Planning Prompt

Sent once at the start of a bridge run (or once per retry if the plan is rejected).

```
You are a Tech Supervisor. Your only job is to decompose a development goal into
an atomic sequential plan for a developer tool called Aider.

STRICT RULES:
- Return ONLY the JSON plan. No code. No prose. No questions.
- Each task targets exactly one concern and one or more specific files.
- Use only relative file paths that are visible in the repo structure below.
  If a file does not yet exist, use the path it should be created at.
- Task type must be one of: create, modify, validate
- Tasks execute sequentially. Later tasks may depend on earlier ones.
- Instructions must be concrete but code-free: say WHAT to build, never HOW.
- Do not ask questions. Do not explain. Return the plan only.

Repo structure:
<live tree of the target repository>

[Project brief: <content of --idea-file if provided>]

Goal: <user-supplied goal>

[Previous plan was rejected for: <parse/validation error> — fix and return only the plan]
```

### Expected response shape

```json
{
  "tasks": [
    {
      "id": 1,
      "files": ["relative/path/to/file.ext"],
      "instruction": "Create X that does Y with Z behaviour.",
      "type": "create"
    },
    {
      "id": 2,
      "files": ["relative/path/to/other.ext"],
      "instruction": "Add Y to existing X so that Z works.",
      "type": "modify"
    }
  ]
}
```

### Field rules

| Field | Type | Constraint |
|---|---|---|
| `id` | integer | Sequential, unique |
| `files` | string[] | Non-empty; relative paths only; never the repo root |
| `instruction` | string | Non-empty; describes what to build — no code |
| `type` | string | One of `create`, `modify`, `validate` |

---

## Review Prompt

Sent after each task completes (after Aider runs and mechanical checks pass).
The supervisor sees the task definition and the actual git diff — nothing else.

```
You are a Tech Supervisor reviewing completed developer work.
Reply with exactly one of these two forms (nothing else):
  PASS
  REWORK: <one-sentence atomic replacement instruction — no code>

Task <id> (<type>)
Files: <comma-separated file list>
Instruction: <original instruction>
Aider execution: succeeded | failed (exit code N)

Changes made:
--- changed files ---
<git diff --stat output>

--- diff ---
<git diff output, truncated at 4000 chars>
```

### Expected response

| Response | Meaning |
|---|---|
| `PASS` | Supervisor approves — bridge moves to the next task |
| `REWORK: <instruction>` | Supervisor rejects — bridge re-runs Aider with the new instruction |

The replacement instruction in `REWORK:` must follow the same rules as a planning
instruction: concrete, code-free, one concern, same files.

---

## Supervisor Backends

### Codex CLI (default)

```bash
python main.py "Build a logging feature" \
  --supervisor-command "codex.cmd exec --skip-git-repo-check --color never"
```

Or set the environment variable:
```bash
set BRIDGE_SUPERVISOR_COMMAND=codex.cmd exec --skip-git-repo-check --color never
```

### Claude CLI

```bash
python main.py "Build a logging feature" \
  --supervisor-command "claude --print"
```

### Anthropic API / custom agent

You can wire any agent that reads stdin or a prompt argument and writes JSON to stdout:

```bash
python main.py "Build a logging feature" \
  --supervisor-command "python my_supervisor.py {prompt}"
```

The `{prompt}` placeholder is replaced with the full prompt text before execution.
The `{output_file}` placeholder (if present) is replaced with a temp file path that
the supervisor should write its response to.

---

## Aider Local LLM Configuration

Aider is the developer and runs on a local LLM. Pass the model via `--aider-model`:

```bash
# Ollama — Mistral (general purpose)
python main.py "Add error handling" --aider-model ollama/mistral

# Ollama — DeepSeek Coder (code-focused)
python main.py "Add error handling" --aider-model ollama/deepseek-coder

# Ollama — CodeLlama
python main.py "Add error handling" --aider-model ollama/codellama

# LM Studio (OpenAI-compatible local server)
python main.py "Add error handling" \
  --aider-command "aider --openai-api-base http://localhost:1234/v1" \
  --aider-model openai/local-model
```

Or set persistently:
```bash
set BRIDGE_AIDER_MODEL=ollama/mistral
```

When `--aider-model` is not set, Aider uses whatever model is configured in
its own `.aider.conf.yml` or environment.

---

## Token Usage Pattern

The design minimises supervisor token usage:

| Event | Supervisor called? |
|---|---|
| Plan generation | Yes (once, or once per retry) |
| Mechanical check failure (file missing, syntax error) | **No** — retries with same instruction |
| Aider exit code != 0 | No — diff still sent to supervisor |
| After mechanical checks pass | Yes — one review call per task |
| REWORK issued | Yes — one plan refinement per retry |

Aider handles all code generation locally. The supervisor only sees compact
prompts (repo tree + goal) and compact review payloads (task + diff).

---

## Prompt Injection Guidance

Keep the idea file (`--idea-file`) concise. The bridge injects up to 2000
characters of the idea file into the planning prompt. Put the most important
architectural constraints at the top of the file.

The repo tree is capped at 100 entries and 4 directory levels. Deeply nested
or generated directories (node_modules, Library, obj, bin) are automatically
excluded.
