# Bridge — Product Roadmap

> **Bridge** connects a Supervisor Agent (Claude / Codex / any LLM) with Aider running on a local model,
> enabling autonomous multi-task code editing with scope enforcement, validation, and retry logic.

---

## Current State (v1.x)

| Feature | Status |
|---------|--------|
| Plan JSON → Aider task execution | ✅ Working |
| Scope enforcement — tracked files reverted after each task | ✅ Working |
| Scope enforcement — only removes NEW untracked files (pre-run snapshot) | ✅ Fixed |
| TimeoutExpired kill robustness (Windows Python 3.11) | ✅ Fixed |
| Unicode decode error in subprocess reader (`errors="replace"`) | ✅ Fixed |
| Supervisor: default changed from `codex.cmd` → `claude -p` | ✅ Fixed |
| Supervisor: RETRY verdict — unstructured review retries with original instruction | ✅ Fixed |
| Supervisor: empty-diff fast path — skips full review when aider made no changes | ✅ Fixed |
| Small-model auto-split regex (`≤8B`) | ✅ Working |
| Multi-project-type support (`unity`, `react`, etc.) | ✅ Working |
| Supervisor planning loop | ✅ Working |

---

## 🔴 Phase 1 — Stability & Quality
> **Goal:** Eliminate known failure modes seen in production use.

| # | Item | Problem it Solves | Effort |
|---|------|------------------|--------|
| 1.1 | **Untracked file cleanup** | Model creates empty junk files at repo root. Scope enforcement only reverts *tracked* files. Add `git clean -fd` for untracked files outside allowed paths after each task. | S |
| 1.2 | **Integer task ID coercion** | Parser rejects string IDs like `"qp_001"` — auto-coerce to int or accept both formats gracefully. | XS |
| 1.3 | **Binary file blocklist** | Model has corrupted `.asset` / font glyph tables. Add extension blocklist: never allow edits to `.asset`, `.png`, `.meta`, `.png`, `.jpg`, `.wav`, `.mp4`. | S |
| 1.4 | **Stale dotnet cache guard** | Unity validation fails silently due to stale incremental build cache. Always pass `--no-incremental` when project type is `unity`. | XS |
| 1.5 | **Timeout kill test coverage** | `ex.process` AttributeError fixed but untested on CI. Add unit tests for timeout path. | S |

---

## 🟡 Phase 2 — Developer Experience
> **Goal:** Reduce manual JSON writing and back-and-forth debugging.

| # | Item | Description | Effort |
|---|------|-------------|--------|
| 2.1 | **Auto-validation retry loop** | After each task run `dotnet build` / `npm test` / configured validator. Parse errors, feed back to aider as a follow-up prompt. Retry up to N times automatically. This alone eliminates ~80% of manual back-and-forth. | M |
| 2.2 | **Natural language → plan** | Accept a plain-English goal file (`--goal-file`). Supervisor LLM auto-generates the plan JSON — no manual JSON writing required. | M |
| 2.3 | **Task dependency graph (DAG)** | Add `"depends_on": [1, 2]` field to tasks. Task 3 waits for tasks 1 & 2 before starting. Enables multi-phase plans without manual ordering. | M |
| 2.4 | **Per-task git branch** | Each task runs on its own `bridge/task-{id}` branch. Merged into main branch if validation passes. Keeps Git history clean and each task reversible. | M |
| 2.5 | **Rich TUI progress display** | Replace raw logs with a `rich`-powered terminal UI showing task status, live aider output tail, retry counter, and elapsed time per task. | S |
| 2.6 | **Context file auto-detection** | Scan `using` / `import` statements in target files and auto-add related files as context. Reduces manual `context_files` specification in plan JSON. | M |

---

## 🟢 Phase 3 — Power Features
> **Goal:** Make Bridge capable of handling large, complex codebases autonomously.

| # | Item | Description | Effort |
|---|------|-------------|--------|
| 3.1 | **Multi-model routing** | Route tasks by complexity: simple refactors → 7B, architecture changes → 14B, planning → Claude/GPT-4. Configurable routing rules in `config.yaml`. | L |
| 3.2 | **Parallel task execution** | Run independent tasks (no overlapping files) concurrently across multiple aider processes. Significant speed gain for large plans. | L |
| 3.3 | **RAG context injection** | Use embeddings (e.g. `nomic-embed-text` via Ollama) to automatically find the most relevant files across the whole repo for each task. No more guessing `context_files`. | L |
| 3.4 | **Rollback on failure** | If validation fails after N retries, `git stash` the task changes and continue. Report all failed tasks in a summary at the end. | M |
| 3.5 | **Token budget tracking** | Track estimated tokens sent/received per task, per model. Display a cost/token summary table at the end of every bridge run. | S |
| 3.6 | **Unity MCP integration** | After a task, trigger Unity compilation and read console errors via MCP (HTTP transport) instead of `dotnet build`. Catches runtime errors `dotnet` misses. | L |

---

## 🔵 Phase 4 — Platform & Scale
> **Goal:** Make Bridge a first-class dev-tool platform, not just a script.

| # | Item | Description | Effort |
|---|------|-------------|--------|
| 4.1 | **Web dashboard** | React + FastAPI UI with drag-and-drop plan builder, live task log streaming via WebSocket, Git diff viewer per task, and one-click rollback. | XL |
| 4.2 | **GitHub Actions integration** | Run bridge as a CI step. On PR creation, execute plan automatically and open a follow-up PR with all task diffs. | L |
| 4.3 | **MCP server mode** | Expose Bridge itself as an MCP server. Claude Code can call `bridge.run_task(...)` directly as a tool — no plan JSON needed. | L |
| 4.4 | **Plugin system** | Validators, context injectors, model routers, and file blocklists as pluggable Python modules. Drop a `.py` file in `plugins/` to extend Bridge. | L |
| 4.5 | **Project templates** | `--project-type unity/react/fastapi/django` pre-configures validation command, binary file blocklist, context hints, and model routing. | M |
| 4.6 | **Multi-repo support** | One plan file targeting multiple repositories. E.g. update a Unity project and its backend API in a single bridge run. | XL |

---

## 📊 Priority Matrix

```
                    LOW EFFORT          HIGH EFFORT
                 ┌───────────────────┬───────────────────┐
  HIGH IMPACT    │ ✅ Untracked cleanup│ 🔶 Natural lang→plan│
                 │ ✅ Binary blocklist │ 🔶 RAG context      │
                 │ ✅ Auto-validation  │ 🔶 Parallel tasks   │
                 ├───────────────────┼───────────────────┤
  LOW IMPACT     │ 🔷 Rich TUI        │ 🔮 Web dashboard    │
                 │ 🔷 Token tracking  │ 🔮 MCP server mode  │
                 │ 🔷 ID coercion     │ 🔮 GitHub Actions   │
                 └───────────────────┴───────────────────┘
```

---

## 🎯 Immediate Next Step

The single highest ROI improvement right now:

> **Phase 2.1 — Auto-validation retry loop**
>
> After aider finishes a task, automatically run the validation command (e.g. `dotnet build`),
> parse compiler errors, feed them back to aider as a corrective prompt, and retry up to 3 times.
> This eliminates the most common manual loop in current usage.

---

## Effort Key

| Label | Estimate |
|-------|----------|
| XS | < 2 hours |
| S | 2–8 hours |
| M | 1–3 days |
| L | 1–2 weeks |
| XL | 2–4 weeks |

---

*Last updated: 2026-03-30*
