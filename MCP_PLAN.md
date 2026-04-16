# Bridge MCP Server — Development Plan

> **Purpose:** Expose the bridge's core services as MCP tools so Claude can
> orchestrate runs, query memory, and read project state through typed tool
> calls instead of raw bash commands + file parsing.
>
> **Document status:** Living document — updated after each milestone lands.

---

## Problem Being Solved

Today Claude reaches the bridge through:

| What Claude needs | How it does it now | Pain |
|---|---|---|
| Run a task plan | `python main.py --repo-root X --plan-file Y` bash call | Parses unstructured stdout |
| Check run status | `cat bridge_progress/task_metrics.json` | Must know exact file paths |
| Search memory | `curl http://localhost:3000/bridge/enhance` | Raw JSON, no schema |
| Read checkpoint | `cat bridge_progress/checkpoint.json` | Silently fails if path wrong |
| Service health | `curl http://localhost:6333/health` × 3 | Three separate bash calls |

An MCP server wraps all of this into typed tool calls with structured returns
and proper error objects. The `codex-aider-bridge` skill shrinks significantly —
fewer bash commands, less stdout parsing, fewer errors.

---

## Architecture Overview

```
Claude Code (MCP client)
    │
    └── bridge-mcp-server  (new — Node.js or Python, stdio transport)
            ├── Bridge Tools       → wraps python main.py subprocess
            ├── Memory Tools       → HTTP client to bridge-memory-service
            └── State Tools        → reads bridge_progress/ + project files
```

The MCP server lives **inside the bridge repo** at `mcp/`.
It is started automatically via `ensure_services.py` alongside Qdrant and
the memory service.

---

## Milestones

---

### M1 — Scaffold & Transport  `[ ]`

**Goal:** Bare MCP server that Claude can connect to and call one ping tool.

**Deliverables:**
- `mcp/` directory with `package.json`, `tsconfig.json`, `src/index.ts`
- MCP SDK wired with stdio transport
- One tool: `bridge_ping` → returns `{ ok: true, version: "0.1.0" }`
- Entry in `ensure_services.py` to start the server on session open
- Entry in `.claude/settings.json` `mcpServers` config so Claude auto-connects

**Acceptance:** Claude can call `bridge_ping` from a tool call and get a
structured response — no bash required.

**Status:** `DONE ✅`

**Notes:**
- MCP SDK 1.29.0 uses newline-delimited JSON (NDJSON), not Content-Length framing
- `"type": "module"` + `"module": "NodeNext"` tsconfig required (SDK is ESM-only)
- `mcp/dist/index.js` built and tested; `bridge_ping` returns `{ ok, version, server }`
- `.claude/settings.json` updated with `mcpServers.bridge` entry (absolute path to dist)
- `ensure_services.py` extended with `ensure_mcp_server()` — auto-builds dist on session open if missing

---

### M2 — State Tools  `[ ]`

**Goal:** Claude can read all bridge_progress/ files and project state through
typed tool calls.

**Tools:**

| Tool | Input | Returns |
|---|---|---|
| `bridge_get_status` | `repo_root` | `{ status, planned, completed, failed_task_id, last_commit }` |
| `bridge_get_checkpoint` | `repo_root` | `{ completed_ids[], plan_hash }` |
| `bridge_get_metrics` | `repo_root` | full `task_metrics.json` typed |
| `bridge_get_project_knowledge` | `repo_root` | `project_knowledge.json` contents |
| `bridge_list_repos` | — | all repos that have a `bridge_progress/` dir |

**Why before execution tools:** State reads are read-only and safe to build
first. Claude immediately benefits — no more `cat` file reads in the skill.

**Status:** `DONE ✅`

**Notes:**
- All five tools verified against real `bridge_progress/` data
- `bridge_list_repos` scans a directory and returns all repos with `bridge_progress/` present
- `zod` added as direct dependency (was transitive via SDK — now explicit)
- Large-file tools (`bridge_get_project_knowledge`, `bridge_list_repos`) need ~10s timeout in combined tests — individual calls respond in <1s

---

### M3 — Memory Tools  `[ ]`

**Goal:** Claude can search, save, and enhance via the bridge-memory-service
through MCP tools — no more curl calls or raw HTTP.

**Tools:**

| Tool | Input | Returns |
|---|---|---|
| `memory_search` | `query, limit?` | `SearchResult[]` with scores |
| `memory_enhance` | `prompt` | `{ original, enhanced, context_used[] }` |
| `memory_save` | `content, type, tags[]` | `{ id, saved: true }` |
| `memory_health` | — | `{ status, mode: "vector" \| "sqlite-only" }` |

**Replaces:** `claude-mem:mem-search` calls in Stage 1.5 of the skill, and
all `curl /bridge/enhance` + `/bridge/ingest` calls.

**Status:** `DONE ✅`

**Notes:**
- 5 tools: `memory_health`, `memory_search`, `memory_save`, `memory_enhance`, `memory_ingest`
- `memory_ingest` added beyond original plan — mirrors the bridge's `POST /bridge/ingest` call
- SQLite serialises `MemoryType` REAL column as string `"2.0"` — fixed with `Math.round(Number(...))`
- `memory_health` infers mode (vector+sqlite vs sqlite-only) by probing Qdrant port 6333 directly
- All tools degrade gracefully if memory service is unreachable (return `isError` response)

---

### M4 — Service Health Tool  `[ ]`

**Goal:** One tool call tells Claude whether all dependencies are up.

**Tools:**

| Tool | Returns |
|---|---|
| `bridge_health` | `{ qdrant, ollama, memory_service, mcp_server }` each with `{ up: bool, latency_ms }` |

**Replaces:** The three separate health curl calls in Stage 1 setup checks.
Claude calls one tool, gets a full dependency map.

**Status:** `NOT STARTED`

---

### M5 — Execution Tools (Dry Run + Plan)  `[ ]`

**Goal:** Claude can validate and run task plans through tool calls, receiving
structured progress events instead of raw stdout.

**Tools:**

| Tool | Input | Returns |
|---|---|---|
| `bridge_dry_run` | `plan (TaskPlan object), repo_root` | `{ valid: bool, errors[], task_count }` |
| `bridge_run_plan` | `plan_file_path, repo_root, flags?` | streaming `ProgressEvent` objects |
| `bridge_cancel` | — | `{ cancelled: bool }` |

**Notes:**
- `bridge_run_plan` uses MCP streaming (progress notifications) so Claude sees
  per-task updates without polling files
- `bridge_dry_run` calls `python main.py --dry-run` and parses its exit code +
  stderr into a structured error list
- Flags object covers: `auto_split_threshold`, `aider_model`,
  `validation_command`, `manual_supervisor`

**This is the biggest milestone** — replaces the core bash orchestration loop
in the skill.

**Status:** `NOT STARTED`

---

### M6 — Skill Rewrite  `[ ]`

**Goal:** Update the `codex-aider-bridge` skill to use MCP tools everywhere
bash commands were used. Skill becomes shorter and more reliable.

**Changes:**
- Stage 1 setup checks → `bridge_health` (single tool call)
- Stage 1.5 memory retrieval → `memory_search` (two calls)
- Stage 2 state context → `bridge_get_status` + `bridge_get_project_knowledge`
- Stage 3 dry run → `bridge_dry_run`
- Stage 3 real run → `bridge_run_plan` with streaming
- Stage 5-E ingest → `memory_save`

**Target:** Skill word count drops by ~40%. All bash blocks removed except
git commands (those stay as bash — git is already good at structured output
via `--porcelain`).

**Status:** `NOT STARTED`

---

### M7 — ensure_services Integration  `[ ]`

**Goal:** MCP server starts automatically with the session, alongside Qdrant
and memory service.

**Changes to `scripts/ensure_services.py`:**
- `ensure_mcp_server()` function: checks if port is listening, runs
  `npm start` in `mcp/` if not
- Added to `main()` call sequence after `ensure_memory_service()`

**Changes to `.claude/settings.json`:**
```json
"mcpServers": {
  "bridge": {
    "command": "node",
    "args": ["mcp/dist/index.js"],
    "cwd": "<bridge_root>"
  }
}
```

**Status:** `NOT STARTED`

---

## Milestone Summary

| # | Milestone | Status |
|---|---|---|
| M1 | Scaffold & Transport | `DONE ✅` |
| M2 | State Tools | `DONE ✅` |
| M3 | Memory Tools | `DONE ✅` |
| M4 | Service Health Tool | `NOT STARTED` |
| M5 | Execution Tools | `NOT STARTED` |
| M6 | Skill Rewrite | `NOT STARTED` |
| M7 | ensure_services Integration | `NOT STARTED` |

---

## Tech Stack Decision

| Choice | Reason |
|---|---|
| **TypeScript** | MCP SDK is best supported in TS; memory-service is already TS |
| **stdio transport** | Simplest for Claude Code MCP integration; no port to manage |
| **`@modelcontextprotocol/sdk`** | Official SDK, handles framing/schema automatically |
| **Single process** | All tools in one server — no need to split by concern at this scale |

---

## File Layout (Target)

```
mcp/
  package.json
  tsconfig.json
  src/
    index.ts          ← server entry, tool registration
    tools/
      ping.ts
      state.ts        ← M2
      memory.ts       ← M3
      health.ts       ← M4
      execution.ts    ← M5
    bridge/
      runner.ts       ← spawns python main.py, parses output
      progress.ts     ← reads bridge_progress/ files
    memory/
      client.ts       ← HTTP client to bridge-memory-service
  dist/               ← compiled output
```

---

## Change Log

| Date | Milestone | What happened |
|---|---|---|
| 2026-04-16 | — | Plan created |
| 2026-04-16 | M1 | Scaffold complete — `bridge_ping` tool verified end-to-end; SDK framing issue (NDJSON not LSP) diagnosed and fixed |
| 2026-04-16 | M2 | All 5 state tools verified against real `bridge_progress/` data — `bridge_get_status`, `bridge_get_checkpoint`, `bridge_get_metrics`, `bridge_get_project_knowledge`, `bridge_list_repos` |
| 2026-04-16 | M3 | All 5 memory tools verified against live service — `memory_health`, `memory_search`, `memory_save`, `memory_enhance`, `memory_ingest`; fixed SQLite string-float type serialisation bug |
