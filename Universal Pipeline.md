# Universal Pipeline — Architecture & Milestone Plan

## Mental Model

The bridge has two roles, played by the same entity (the supervisor):

| Role | When | What it does |
|---|---|---|
| **Planner** | Before the run starts | Reads the user's natural language goal, generates a structured task list |
| **Reviewer** | After each Aider task | Reviews the diff, decides pass / fail / replan |

The same supervisor (Chatbot, Codex, Claude, etc.) fills **both** roles. The user can switch supervisor mid-run — changing only which backend the UI proxy calls next; the bridge process keeps running.

---

## Universal Pipeline Diagram

```
User types NL goal
        │
        ▼
  [Planner role]
  Supervisor reads goal
  → generates task list
        │
        ▼
   Task List confirmed
        │
   ┌────┴────────────────────────────────────┐
   │        BRIDGE LOOP (--manual-supervisor) │
   │                                          │
   │   Aider executes task N                  │
   │         │                                │
   │         ▼                                │
   │   writes request file                    │
   │   bridge_progress/manual_supervisor/     │
   │   requests/<uuid>.json                   │
   │         │                                │
   │         ▼                                │
   │   [UI Supervisor Proxy Thread]           │
   │   reads request file                     │
   │   checks current supervisor setting      │
   │         │                                │
   │    ┌────┴──────────────────────────┐     │
   │    │                               │     │
   │    ▼                               ▼     │
   │  Chatbot path           Other supervisor │
   │  (Reviewer role)        path             │
   │  show copy-paste        auto-call CLI    │
   │  card in UI             (codex/claude)   │
   │  wait for user          wait for stdout  │
   │  to paste response      parse response   │
   │    │                               │     │
   │    └────────────┬──────────────────┘     │
   │                 │                        │
   │                 ▼                        │
   │   writes decision file                   │
   │   bridge_progress/manual_supervisor/     │
   │   decisions/<uuid>.json                  │
   │                 │                        │
   │                 ▼                        │
   │   bridge reads decision → next task      │
   │                                          │
   └──────────────────────────────────────────┘
        │
        ▼
   All tasks done → run complete
```

---

## Supervisor Path Comparison

### Chatbot Path (inline AI Relay wizard)

```
User selects "Chatbot" supervisor
        │
        ▼
Planner step:
  UI sends goal to Ollama → gets task list
  UI renders task list (editable)
  User clicks "Confirm Plan"
        │
        ▼
Run starts (--manual-supervisor)
        │
   At each review point:
        │
        ▼
  UI shows copy-paste card:
    ┌─────────────────────────────┐
    │ REVIEWER PROMPT             │
    │ [prompt text for user to    │
    │  copy into their chatbot]   │
    │                             │
    │ [Copy] button               │
    ├─────────────────────────────┤
    │ PASTE RESPONSE HERE         │
    │ [textarea]                  │
    │ [Submit]                    │
    └─────────────────────────────┘
  User copies prompt → pastes into ChatGPT/Claude/etc
  User pastes response back → UI submits decision
```

### Other Supervisor Path (Codex, Claude CLI, etc.)

```
User selects "Codex" (or Claude/Cursor/Custom) supervisor
        │
        ▼
Planner step:
  UI sends goal to Ollama → gets task list
  User confirms plan
        │
        ▼
Run starts (--manual-supervisor)
        │
   At each review point:
        │
        ▼
  UI Supervisor Proxy Thread:
    reads request file
    calls supervisor CLI automatically:
      codex.cmd exec --skip-git-repo-check "review: <task>"
      OR: claude -p "review: <task>"
    parses stdout for pass/fail/replan
    writes decision file
    SSE event → UI shows review result (no user input needed)
```

---

## Mid-Run Supervisor Switch

Because **all** runs use `--manual-supervisor`, the bridge writes a request file and waits. The proxy thread checks the **current** supervisor setting at the moment it reads the request — not the setting at launch time.

```
Run in progress with supervisor=codex
   │
   ▼  (user drops codex credits or wants to manually review next task)
User changes supervisor dropdown → "Chatbot"
   │
   ▼  (no bridge restart — bridge is still waiting on decision file)
Next request file arrives
Proxy thread reads current supervisor → "Chatbot"
Shows copy-paste card instead of calling codex
   │
   ▼  (user pastes response)
Decision written → bridge continues
```

No bridge process restart. No run interruption. The switch takes effect on the **next** review point.

---

## File Inventory (at time of writing)

| File | Lines | Role |
|---|---|---|
| `ui/templates/run.html` | 359 | Run page HTML |
| `ui/static/js/pages/run.js` | 1185 | Run page JS |
| `ui/static/css/pages/run.css` | 561 | Run page CSS |
| `ui/templates/relay.html` | 340 | AI Relay page — **DELETE in Milestone B** |
| `ui/static/js/pages/relay.js` | 906 | AI Relay JS — **DELETE in Milestone B** |
| `ui/static/css/pages/relay.css` | 633 | AI Relay CSS — **DELETE in Milestone B** |
| `ui/templates/base.html` | 302 | Nav, layout |
| `ui/app.py` | ~1978 | Flask routes |
| `ui/bridge_runner.py` | 411 | Bridge process launcher |
| `ui/state_store.py` | 359 | JSON persistence |

---

## Existing API Routes (reused across milestones)

| Route | Method | Purpose |
|---|---|---|
| `/api/run/brief` | POST | Ollama call → goal brief + clarifications |
| `/api/run/nl/plan` | POST | Ollama call → task list via relay_formatter |
| `/api/run/nl/plan/confirm` | POST | Writes plan JSON to project, persists state |
| `/api/run/nl/state` | GET/POST/DELETE | NL conversation state CRUD |
| `/api/run/nl/launch` | POST | Merges NL brief+plan → calls `_start_bridge_run()` |
| `/api/relay/generate-prompt` | POST | Builds copy-paste prompt for chatbot reviewer |
| `/api/relay/import-plan` | POST | Parses pasted plan text → task list |
| `/api/relay/submit-decision` | POST | Writes decision file for bridge |
| `/api/relay/review-packet` | GET | Polls for pending review request |
| `/api/relay/state` | GET/POST/DELETE | Relay UI state CRUD |
| `/api/relay/replan-prompt` | POST | Builds replan prompt |
| `/api/relay/import-replan` | POST | Parses replan response |
| `/api/relay/import-from-nl` | POST | Imports NL plan into relay task list |

---

## Milestone Plan

### Dependency Order

```
Milestone A → Milestone B → Milestone C → Milestone D
(Foundation)   (Chatbot)    (Universal)   (Polish)
```

Each milestone is independently deployable and testable.

---

### Milestone A — Unified Run Page

**Goal:** Remove the Structured / Natural Language mode toggle. One page, one goal. Add "Chatbot" to the supervisor grid.

**Why first:** Everything else builds on the unified page. Must land before B or C touch the same HTML.

**Files changed:**

| File | Change |
|---|---|
| `ui/templates/run.html` | Remove `.run-mode-toggle` + `#structured-panel` wrapper. Collapse NL panel to always-visible. Add Chatbot supervisor card to grid. Move M1–M3 brief+plan UI into collapsible accordion. |
| `ui/static/js/pages/run.js` | Remove `setMode()`, `_nlMode` flag, mode toggle listeners. Remove `#structured-panel` show/hide logic. Add `chatbot` to supervisor value handling. |
| `ui/static/css/pages/run.css` | Delete `.run-mode-toggle`, `.run-mode-btn` rules. Keep all NL panel styles. Add `.supervisor-option[data-value="chatbot"]` styling. |

**Resulting UX:**
```
Run page
├── Repo root (always visible)
├── Aider model (always visible)
├── Goal textarea (always visible)   ← was NL panel only
├── Generate Brief button
│   └── Brief output (accordion, collapsible)
│       └── Generate Plan button
│           └── Task list (accordion)
│               └── Confirm Plan button
└── Supervisor grid
    ├── Codex | Claude | Cursor | Windsurf
    ├── Chatbot (NEW) | Manual | Custom
    └── Launch Run button
```

**Removed:**
- Structured mode panel (all its specific fields: `validation_command`, `max_plan_attempts`, `max_task_retries`, `task_timeout`, `plan_output_file`, `plan_file`, `dry_run`) — these become advanced settings or are dropped
- Mode toggle buttons

**No backend changes** in this milestone.

---

### Milestone B — Chatbot Inline Relay Wizard

**Goal:** When supervisor = Chatbot, the run page shows the full AI Relay wizard inline (copy-paste card, task list, review loop). Delete the separate AI Relay page.

**Why second:** Depends on Milestone A's supervisor grid having the Chatbot option. Relay page deletion is clean only after the inline wizard works.

**Files changed:**

| File | Change |
|---|---|
| `ui/templates/run.html` | Add `#chatbot-relay-panel` (hidden by default). Contains: step indicator (Plan → Review → Done), task list table, copy-paste prompt card, paste response textarea + Submit button. Show when `supervisor === "chatbot"` and run is in progress. |
| `ui/static/js/pages/run.js` | Port from `relay.js`: `generateRelayPrompt()`, `importRelayPlan()`, `renderRelayTaskList()`, relay review polling loop. Integrate into run page's SSE event handler — on `supervisor_review_requested` event → show chatbot relay panel. |
| `ui/static/css/pages/run.css` | Port from `relay.css`: step indicators, relay task list, copy-paste card, relay action buttons. Keep `relay-task-type-badge[data-type]` colour rules. |
| `ui/app.py` | Add `GET /relay → redirect to /run` (302). Existing `/api/relay/*` routes stay as-is (reused). |
| `ui/templates/base.html` | Remove "AI Relay" nav item. |
| `ui/templates/relay.html` | **DELETE** |
| `ui/static/js/pages/relay.js` | **DELETE** |
| `ui/static/css/pages/relay.css` | **DELETE** |

**SSE events added:**

| Event | Payload | Meaning |
|---|---|---|
| `supervisor_review_requested` | `{request_id, prompt, task_title, diff}` | Bridge waiting for chatbot review |
| `supervisor_review_submitted` | `{request_id, decision}` | Decision written, bridge continuing |

**Chatbot relay panel layout (inline in run page):**

```
┌─── Chatbot Reviewer ───────────────────────────────────┐
│  Step: [Plan] → [Review ●] → [Done]                    │
│                                                        │
│  Task 3 of 7: "Add user auth middleware"               │
│  Status: Waiting for your review                       │
│                                                        │
│  ┌── Reviewer Prompt ──────────────────────────────┐  │
│  │  [Copy prompt button]                           │  │
│  │  Review this diff and respond pass/fail/replan: │  │
│  │  <diff text>                                    │  │
│  └─────────────────────────────────────────────────┘  │
│                                                        │
│  ┌── Paste Response ───────────────────────────────┐  │
│  │  [textarea]                                     │  │
│  └─────────────────────────────────────────────────┘  │
│  [Submit Response]                                     │
└────────────────────────────────────────────────────────┘
```

---

### Milestone C — Universal Pipeline + Supervisor Proxy

**Goal:** All supervisor types use `--manual-supervisor`. A background proxy thread in the UI backend dispatches review decisions based on current supervisor setting. Supervisor dropdown stays active mid-run.

**Why third:** Requires Milestones A+B to be done. This is the core architectural change.

**Files changed:**

| File | Change |
|---|---|
| `ui/bridge_runner.py` | Remove the `if supervisor == "ai_relay"` branch. **All** runs get `--manual-supervisor`. Remove `--supervisor-command` from launch command (proxy handles CLI calls instead). |
| `ui/app.py` | Add `SupervisorProxyThread` class. Add `POST /api/run/supervisor` route (mid-run supervisor switch). Integrate proxy thread start/stop into `_start_bridge_run()` / run teardown. |
| `ui/templates/run.html` | Make supervisor grid interactive mid-run (remove `disabled` state during run). |
| `ui/static/js/pages/run.js` | On supervisor change during active run: call `POST /api/run/supervisor`. |

**`SupervisorProxyThread` design:**

```python
class SupervisorProxyThread(threading.Thread):
    """
    Polls the bridge's manual_supervisor/requests/ directory.
    When a request file appears, dispatches to the correct backend
    based on current supervisor setting, then writes the decision file.
    """

    def __init__(self, run_id, repo_root, get_supervisor_fn):
        # get_supervisor_fn() returns current supervisor at call time
        # (not captured at thread start — allows mid-run switching)
        ...

    def run(self):
        requests_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
        decisions_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "decisions"
        seen = set()

        while not self._stop_event.is_set():
            for req_file in requests_dir.glob("*.json"):
                if req_file.stem in seen:
                    continue
                seen.add(req_file.stem)
                request = json.loads(req_file.read_text())
                supervisor = self.get_supervisor_fn()  # current setting

                if supervisor == "chatbot":
                    # Emit SSE event — UI handles the copy-paste card
                    self._emit_sse("supervisor_review_requested", {
                        "request_id": req_file.stem,
                        **request,
                    })
                    # Decision will come via POST /api/relay/submit-decision
                    # (already existing route)

                elif supervisor == "manual":
                    # Same as chatbot but without the prompt-building step
                    self._emit_sse("supervisor_review_requested", {
                        "request_id": req_file.stem,
                        "manual": True,
                        **request,
                    })

                else:
                    # Auto-call CLI supervisor
                    decision = self._call_cli_supervisor(supervisor, request)
                    (decisions_dir / f"{req_file.stem}.json").write_text(
                        json.dumps(decision), encoding="utf-8"
                    )
                    self._emit_sse("supervisor_review_submitted", {
                        "request_id": req_file.stem,
                        "decision": decision,
                    })

            time.sleep(0.5)
```

**`POST /api/run/supervisor` route:**

```python
@app.route("/api/run/supervisor", methods=["POST"])
def switch_supervisor():
    """Switch supervisor mid-run. Takes effect on next review point."""
    data = request.json or {}
    run_id = data.get("run_id")
    new_supervisor = data.get("supervisor")
    # Update in-memory setting for the proxy thread
    if run_id in _active_proxy_threads:
        _active_proxy_threads[run_id].set_supervisor(new_supervisor)
    return jsonify({"ok": True})
```

**Bridge command change (before vs after):**

```python
# BEFORE (Milestone C)
if settings.get("manual_supervisor") or supervisor == "ai_relay":
    cmd.append("--manual-supervisor")
elif settings.get("supervisor_command", "").strip():
    cmd.extend(["--supervisor-command", settings["supervisor_command"].strip()])

# AFTER (Milestone C) — all runs use manual supervisor
cmd.append("--manual-supervisor")
# proxy thread handles CLI dispatch — bridge never calls CLI directly
```

---

### Milestone D — Role Labels, Cleanup, Polish

**Goal:** Surface the Planner/Reviewer role concept in UI. Clean up stale relay endpoints. Final polish.

**Files changed:**

| File | Change |
|---|---|
| `ui/templates/run.html` | Add role indicator strip: `[Planner ✓] → [Reviewer ●]` — shown during run, updates via SSE. |
| `ui/static/js/pages/run.js` | Handle `planner_active` and `reviewer_active` SSE events → update role strip. |
| `ui/static/css/pages/run.css` | `.role-strip`, `.role-badge.--active`, `.role-badge.--done` styles. |
| `ui/app.py` | Remove stale relay routes no longer needed (evaluate which `/api/relay/*` routes are now fully replaced). Keep `/api/relay/submit-decision` (still used by chatbot path). |
| `ui/state_store.py` | Add `planner_role` and `reviewer_role` to `run_nl_states` allowed keys if needed. |
| `ui/templates/base.html` | Final nav cleanup. |
| `CHANGELOG.md` | Document the universal pipeline release. |

**Role indicator strip design:**

```
During planning phase:
┌─────────────────────────────────────────┐
│ [● Planner — generating task list...]   │
└─────────────────────────────────────────┘

During run (reviewing task 3):
┌─────────────────────────────────────────┐
│ [✓ Planner] → [● Reviewer — task 3/7]  │
└─────────────────────────────────────────┘

Run complete:
┌─────────────────────────────────────────┐
│ [✓ Planner] → [✓ Reviewer]             │
└─────────────────────────────────────────┘
```

**SSE events added in Milestone D:**

| Event | Payload | Meaning |
|---|---|---|
| `planner_active` | `{task_count}` | Planner just generated the task list |
| `planner_done` | `{}` | All tasks confirmed, planner role complete |
| `reviewer_active` | `{task_index, task_total, task_title}` | Reviewer is reviewing a task |
| `reviewer_done` | `{decision}` | Reviewer decision submitted |

---

## State Persistence Reference

All state stored in `%LOCALAPPDATA%\AiderBridge\` (Windows) or `~/.local/share/AiderBridge/` (Linux).

| File | Key used | Contents |
|---|---|---|
| `run_nl_states.json` | `repo_root` string | `{message, brief, status, tasks, plan_summary, plan_file, plan_status, last_run_id, confidence_score, risks, risk_level, updated_at}` |
| `settings.json` | — | `{goal, repo_root, aider_model, supervisor, ...}` |
| `relay_ui_state.json` | — | `{step, goal, repo_root, aider_model, ...}` — **deprecated in Milestone B** |
| `relay_tasks.json` | — | Task list array — **deprecated in Milestone B** |

The `supervisor` field in `settings.json` now includes `"chatbot"` as a valid value (added in Milestone A).

---

## Key Design Decisions

### Why `--manual-supervisor` for everyone?

The bridge's manual supervisor mode is a file-based handshake protocol. The bridge writes a request, waits for a decision, continues. This is already implemented and tested. Making all supervisors use it means:

- The UI becomes the sole coordinator of supervisor dispatch
- No bridge changes needed for new supervisor types — just add a branch in the proxy thread
- Mid-run switch is free — no process restart, no state loss
- The bridge doesn't need to know which UI supervisor the user chose

### Why keep existing `/api/relay/*` routes?

The Chatbot path still needs `generate-prompt`, `submit-decision`, and `review-packet`. Keeping these routes avoids a big rename refactor. In Milestone D we evaluate which routes are truly stale (the ones that only existed to support the relay page flow) and clean them up.

### Why port relay.js into run.js rather than import?

Single-page isolation. The run page already has SSE connection management, state persistence, and supervisor awareness. Merging the relay review loop into run.js means one SSE connection, one state machine, one page. A separate import would create two parallel event systems on the same page.

### Why Ollama for the Planner role?

Ollama is already wired for M1 (brief) and M3 (plan generation). The Planner role is exactly M3. No new LLM integration needed — the `relay_formatter.build_plan_prompt()` + `parse_plan()` pipeline already exists.

---

## Implementation Checklist

### Milestone A
- [ ] Remove `.run-mode-toggle` + `#structured-panel` from `run.html`
- [ ] Remove mode toggle JS from `run.js`
- [ ] Remove mode toggle CSS from `run.css`
- [ ] Add Chatbot supervisor card to supervisor grid in `run.html`
- [ ] Handle `chatbot` in supervisor selection JS
- [ ] Test: page loads, goal textarea visible, all supervisor options present

### Milestone B
- [ ] Add `#chatbot-relay-panel` to `run.html`
- [ ] Port `generateRelayPrompt()` from `relay.js` to `run.js`
- [ ] Port `renderRelayTaskList()` from `relay.js` to `run.js`
- [ ] Wire SSE `supervisor_review_requested` → show chatbot panel
- [ ] Port relay step indicator + task list styles to `run.css`
- [ ] Add `GET /relay → redirect /run` in `app.py`
- [ ] Remove AI Relay nav item from `base.html`
- [ ] Delete `relay.html`, `relay.js`, `relay.css`
- [ ] Test: full chatbot relay flow on run page

### Milestone C
- [ ] Remove supervisor-conditional branch from `bridge_runner.py`
- [ ] Add `--manual-supervisor` unconditionally
- [ ] Implement `SupervisorProxyThread` in `app.py`
- [ ] Integrate proxy thread into `_start_bridge_run()`
- [ ] Add `POST /api/run/supervisor` route
- [ ] Make supervisor grid interactive mid-run in `run.html`
- [ ] Call `/api/run/supervisor` on mid-run supervisor change in `run.js`
- [ ] Test: run with Codex supervisor → auto-review; switch to Chatbot mid-run → copy-paste card appears

### Milestone D
- [ ] Add role indicator strip to `run.html`
- [ ] Add `planner_active/done`, `reviewer_active/done` SSE event handlers in `run.js`
- [ ] Add role strip CSS to `run.css`
- [ ] Audit and remove stale `/api/relay/*` routes in `app.py`
- [ ] Update `CHANGELOG.md`
- [ ] Full end-to-end test: NL goal → plan → run → review (chatbot) → review (codex) → done
