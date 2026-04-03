# AI Relay Supervisor — Implementation Plan

> **Status:** PENDING — ready to implement
> **Branch:** `chatbot_llm`
> **Start from:** `H:/Vinayak_Project/codex-aider-bridge-app/.claude/worktrees/elastic-lamarr/`
> **Spec reference:** `AI_RELAY_SPEC.md` — read before starting
> **Resumption rule:** Read this file, check git log for latest milestone commit, continue from next milestone.

---

## What We Are Building

A new supervisor mode called **AI Relay** that lets you use any web-based AI
(ChatGPT Plus, Claude.ai Pro, Gemini Advanced, Grok — any subscription) as the
planning and review brain with **no API key required**.

The user becomes the relay. The bridge formats everything as ready-to-paste
prompts and parses the AI's responses when pasted back.

```
You  <-->  Bridge  <-->  Aider + Ollama (local, free)
 |
 +--> copy prompt --> ChatGPT / Claude.ai / Gemini web
 |
 <-- paste plan / decision / replan back
```

---

## Design System Reference

Reuses the existing design system from the UI rebuild. No new tokens needed.

```
Colours:    --color-accent, --color-success, --color-warning, --color-danger, --color-info
Surfaces:   --color-surface, --color-surface-2, --color-surface-3
Text:       --color-text, --color-text-muted, --color-text-subtle
Radius:     --radius-sm (4px), --radius-md (8px), --radius-lg (12px), --radius-pill (999px)
Fonts:      --font-sans, --font-mono
Components: .btn, .card, .badge, .input, .field, .field-label, .toggle, .log-terminal
```

---

## Target File Structure

```
utils/
└── relay_formatter.py          NEW: plan prompt, review packet, decision parser, replan prompt

ui/
├── app.py                      MODIFY: add 6 relay API routes + /relay page route
├── bridge_runner.py            MODIFY: add ai_relay supervisor type (~30 lines)
├── templates/
│   ├── base.html               MODIFY: add Relay nav item + g+a shortcut
│   └── relay.html              NEW: 3-step wizard page
└── static/
    ├── css/pages/
    │   └── relay.css           NEW: wizard + review styles
    └── js/
        ├── core/
        │   └── shortcuts.js    MODIFY: add a -> /relay chord
        └── pages/
            └── relay.js        NEW: wizard controller, SSE, copy/paste
```

---

## New API Routes

```
GET  /relay
     → render_template("relay.html", active_page="relay")

POST /api/relay/generate-prompt
     body:    { goal, repo_root }
     returns: { prompt: "...", context_summary: "N files, language X, type Y" }
     calls:   relay_formatter.build_plan_prompt()

POST /api/relay/import-plan
     body:    { raw_text: "..." }
     returns: { tasks: [...], plan_summary: "...", task_count: N }
     calls:   relay_formatter.parse_plan()

GET  /api/relay/review-packet
     query:   ?task_id=N
     returns: { packet: "...", task: {...} }
     reads:   diff from bridge_progress/manual_supervisor/requests/

POST /api/relay/submit-decision
     body:    { task_id, raw_text: "..." }
     returns: { ok, decision, instruction?, reason?, error? }
     calls:   relay_formatter.parse_decision()
     writes:  bridge_progress/manual_supervisor/decisions/task_NNNN_decision.json

POST /api/relay/replan-prompt
     body:    { task_id, failed_reason }
     returns: { prompt: "..." }
     calls:   relay_formatter.build_replan_prompt()

POST /api/relay/import-replan
     body:    { raw_text: "...", replace_from_task_id: N }
     returns: { ok, new_tasks: [...] }
     calls:   relay_formatter.parse_plan()
```

**Key design:** `submit-decision` writes to the existing
`manual_supervisor/decisions/` directory — `bridge_runner.py` already polls
that dir, so the relay mode reuses the entire manual supervisor infrastructure.

---

## Milestones

### M1 — `utils/relay_formatter.py`
**Commit message:** `feat(relay/M1): relay_formatter — plan prompt, review packet, decision parser`

**Depends on:** nothing (pure stdlib, no UI deps)

Files:
- `utils/relay_formatter.py`

#### Functions to implement

**`build_plan_prompt(goal, knowledge_context, repo_root) -> str`**

Returns a prompt the user copies into ChatGPT/Claude/Gemini to generate a task plan.

Content:
```
You are a software planning assistant for a project.

PROJECT CONTEXT:
[knowledge_context -- file roles, language, patterns, type]

GOAL:
[goal]

Output ONLY valid JSON (no markdown, no explanation):
{
  "plan_summary": "One sentence describing the overall approach",
  "tasks": [
    {
      "id": 1,
      "title": "Short title (max 60 chars)",
      "files": ["relative/path/to/file.ext"],
      "instruction": "Exact instruction for the code editor -- specific and technical",
      "context": "Why this task exists / what it connects to"
    }
  ]
}

Rules:
- Each task = one atomic change, one or two files max
- Order tasks: dependencies first
- "instruction" is sent directly to Aider -- no ambiguity allowed
- Maximum 15 tasks
```

**`parse_plan(raw_text) -> list[dict]`**

Extracts JSON from pasted AI response. Handles:
- Bare JSON `{ "tasks": [...] }`
- Fenced code block: ` ```json ... ``` `
- JSON embedded in longer text (extract first `{` ... `}` block)

Validates:
- Required fields on each task: `id` (int), `title` (str), `instruction` (str)
- `files` defaults to `[]` if missing
- `context` defaults to `""` if missing

Raises `ValueError` with a human-readable message on any failure.

**`build_review_packet(task, diff, validation_result, attempt, total_tasks, goal) -> str`**

Returns the review text the user copies into ChatGPT after each task.

Format:
```
===================================================
BRIDGE REVIEW REQUEST -- Task [N] of [total]
===================================================

ORIGINAL GOAL:
[goal]

TASK [N]: [task.title]
INSTRUCTION GIVEN TO AIDER:
[task.instruction]

FILES CHANGED:
[file: +X -Y lines -- one line per file]

DIFF:
[unified diff -- max 300 lines; if truncated add: "... (truncated, [N] more lines)"]

VALIDATION: [PASSED / FAILED: [output]]
ATTEMPT: [attempt] of [max_retries]

===================================================
RESPOND WITH EXACTLY ONE LINE:

APPROVED
REWORK: [your specific instruction -- what to change]
FAILED: [reason -- why this needs a new plan]
===================================================
```

**`parse_decision(raw_text) -> dict`**

Parses the AI's pasted response. Scans the first 5 lines, case-insensitive.

Returns one of:
```python
{"decision": "approved"}
{"decision": "rework",      "instruction": "Move error handling to middleware"}
{"decision": "failed",      "reason": "Wrong approach -- use events not polling"}
{"decision": "unparseable", "raw": "...the full pasted text..."}
```

Rules:
- `APPROVED` anywhere on a line → approved
- `REWORK:` followed by non-empty text → rework
- `FAILED:` followed by non-empty text → failed
- No match in first 5 lines → unparseable

**`build_replan_prompt(task, failed_reason, diff, goal) -> str`**

Returns a prompt asking the AI to provide replacement tasks for a failed task.

Format:
```
===================================================
BRIDGE REPLAN REQUEST
===================================================

Task [task.id] FAILED: [task.title]
Failure reason: [failed_reason]

ORIGINAL INSTRUCTION:
[task.instruction]

FAILED IMPLEMENTATION (diff):
[diff -- max 200 lines]

Please provide replacement tasks starting at id=[task.id].
Use the same JSON format:
{
  "tasks": [
    { "id": [task.id], "title": "...", "files": [...], "instruction": "...", "context": "..." }
  ]
}
Only include the replacement tasks (not the whole plan).
===================================================
```

#### Acceptance
1. `parse_plan(json_string)` returns correct task list ✓
2. `parse_plan("```json\n{...}\n```")` strips fencing and parses ✓
3. `parse_plan("Here is the plan:\n{...}\nLet me know")` extracts JSON ✓
4. `parse_plan("not json")` raises `ValueError` ✓
5. `parse_decision("APPROVED")` → `{"decision": "approved"}` ✓
6. `parse_decision("REWORK: add error handling")` → `{"decision": "rework", ...}` ✓
7. `parse_decision("nonsense")` → `{"decision": "unparseable", ...}` ✓

---

### M2 — Backend API Routes
**Commit message:** `feat(relay/M2): relay API routes — generate-prompt, import-plan, review-packet, submit-decision`

**Depends on:** M1

Files:
- `ui/app.py` — add `/relay` page route + 6 `/api/relay/*` routes

#### Route details

`POST /api/relay/generate-prompt`
- Load settings for `repo_root`
- Load project knowledge via `load_knowledge(Path(repo_root))` + `to_context_text()`
- Call `relay_formatter.build_plan_prompt(goal, knowledge_ctx, repo_root)`
- Also return `context_summary`: e.g. `"42 files · Python · type: python"`
- If repo not set: return `{ prompt: "...", context_summary: "no repo configured" }`

`POST /api/relay/import-plan`
- Call `relay_formatter.parse_plan(raw_text)`
- On success: save parsed tasks to `state_store` key `relay_pending_tasks`
- Return `{ tasks, plan_summary, task_count }`
- On `ValueError`: return `{ error: e.args[0] }` with 400

`GET /api/relay/review-packet?task_id=N`
- Load settings for `repo_root`
- Find request file: `<repo_root>/bridge_progress/manual_supervisor/requests/task_NNNN_request.json`
- Extract `diff`, `validation_result`, `attempt` from request JSON
- Load task from `state_store` relay tasks by id
- Call `relay_formatter.build_review_packet(...)`
- Return `{ packet, task }`
- If request file not found: `{ error: "No review request found for task N" }` 404

`POST /api/relay/submit-decision`
- Call `relay_formatter.parse_decision(raw_text)`
- If `unparseable`: return `{ ok: false, decision: "unparseable", raw: "..." }`
- Otherwise build decision JSON matching manual supervisor format:
  ```json
  { "task_id": N, "decision": "pass" }
  { "task_id": N, "decision": "rework", "instruction": "..." }
  { "task_id": N, "decision": "fail", "reason": "..." }
  ```
- Write to `<repo_root>/bridge_progress/manual_supervisor/decisions/task_NNNN_decision.json`
- Return `{ ok: true, decision, instruction?, reason? }`

`POST /api/relay/replan-prompt`
- Load request file for `task_id`
- Call `relay_formatter.build_replan_prompt(task, failed_reason, diff, goal)`
- Return `{ prompt }`

`POST /api/relay/import-replan`
- Call `relay_formatter.parse_plan(raw_text)`
- Splice replacement tasks into `relay_pending_tasks` starting at `replace_from_task_id`
- Return `{ ok: true, new_tasks }`

#### Acceptance
1. `POST /api/relay/generate-prompt` with no repo returns prompt with note about missing context ✓
2. `POST /api/relay/import-plan` with valid JSON returns task list ✓
3. `POST /api/relay/import-plan` with bad input returns 400 + human error ✓
4. `POST /api/relay/submit-decision` with `"APPROVED"` writes decision file ✓
5. `POST /api/relay/submit-decision` with nonsense returns `unparseable` without writing file ✓

---

### M3 — `ui/bridge_runner.py` — AI Relay Supervisor Type
**Commit message:** `feat(relay/M3): bridge_runner ai_relay supervisor type`

**Depends on:** M2

Files:
- `ui/bridge_runner.py`

#### Changes

When `settings.get("supervisor") == "ai_relay"`:

1. **Plan phase**: Skip calling a supervisor CLI entirely.
   - Bridge reads the pre-imported plan from `state_store.load_relay_tasks()`
   - If no tasks in store: emit `error` event — "No plan imported. Use the Relay page to import a plan first."
   - Convert relay task format → internal `Task` objects

2. **Review phase**: After each task completes mechanical validation:
   - Emit `relay_review_needed` SSE event (same structure as `review_required`):
     ```json
     {
       "type": "relay_review_needed",
       "task_id": N,
       "request_file": "path/to/request.json"
     }
     ```
   - Wait for decision file (same polling loop as manual supervisor)

3. **Decision handling**:
   - `"pass"` → continue to next task
   - `"rework"` → re-queue task with updated instruction (same as manual rework)
   - `"fail"` → emit `relay_replan_needed` SSE event:
     ```json
     {
       "type": "relay_replan_needed",
       "task_id": N,
       "failed_reason": "..."
     }
     ```
     Pause the run, wait for the user to import a replan via `/api/relay/import-replan`

4. **After replan import**: emit `relay_replan_imported` SSE event, resume loop from task N

Add to `state_store.py`:
```python
def load_relay_tasks() -> list[dict]: ...
def save_relay_tasks(tasks: list[dict]) -> None: ...
def clear_relay_tasks() -> None: ...
```

#### Acceptance
1. Selecting `ai_relay` supervisor with no imported plan emits SSE error ✓
2. With plan imported, tasks execute one-by-one ✓
3. After task completes, `relay_review_needed` SSE fires ✓
4. Submitting `APPROVED` via `/api/relay/submit-decision` unblocks next task ✓
5. Submitting `FAILED` fires `relay_replan_needed` and pauses run ✓
6. Importing replan and resuming continues from replaced task ✓

---

### M4 — Relay Page (3-Step Wizard)
**Commit message:** `feat(relay/M4): relay page — 3-step wizard with plan import, run, and review loop`

**Depends on:** M2 + M3

Files:
- `ui/templates/relay.html`
- `ui/static/css/pages/relay.css`
- `ui/static/js/pages/relay.js`

#### relay.html layout

Three `.relay-step` divs, only one visible at a time (controlled by JS `data-step` on `.relay-shell`):

**Step 1 — Generate Plan**
```html
<div class="relay-step" data-step="1">
  <div class="relay-step-header">
    <span class="relay-step-badge">Step 1 of 3</span>
    <h2>Generate Plan</h2>
  </div>

  <!-- Goal + repo (same fields as Run page) -->
  <div class="field"> Goal textarea </div>
  <div class="field"> Repo folder picker </div>

  <!-- Instructions card -->
  <div class="relay-instructions">
    <div class="relay-instruction-step">1. Click Generate Prompt</div>
    <div class="relay-instruction-step">2. Copy the prompt → paste into ChatGPT / Claude / Gemini</div>
    <div class="relay-instruction-step">3. Copy the AI's JSON response → paste below</div>
  </div>

  <button id="btn-generate-prompt">Generate Prompt</button>

  <!-- Generated prompt (hidden until generated) -->
  <div id="prompt-area" hidden>
    <div class="relay-copy-block">
      <span>Prompt to copy:</span>
      <button id="btn-copy-prompt">Copy</button>
    </div>
    <textarea id="generated-prompt" readonly></textarea>
    <div class="field-label">Context: <span id="context-summary"></span></div>
  </div>

  <!-- Paste area -->
  <div class="field">
    <label>Paste AI response here:</label>
    <textarea id="plan-paste-area" placeholder="Paste the JSON plan from ChatGPT / Claude / Gemini..."></textarea>
  </div>
  <div id="plan-parse-error" class="relay-error" hidden></div>
  <button id="btn-import-plan">Import Plan →</button>
</div>
```

**Step 2 — Confirm Tasks**
```html
<div class="relay-step" data-step="2" hidden>
  <div class="relay-step-header">
    <span class="relay-step-badge">Step 2 of 3</span>
    <h2>Confirm Plan <span id="task-count-badge" class="badge"></span></h2>
  </div>

  <p id="plan-summary-text" class="text-muted"></p>

  <div id="task-preview-list" class="relay-task-list"></div>

  <div class="relay-step-actions">
    <button id="btn-back-to-step1" class="btn btn--secondary">← Re-import</button>
    <button id="btn-start-relay"   class="btn btn--primary">Start Relay Run →</button>
  </div>
</div>
```

**Step 3 — Run + Review Loop**
```html
<div class="relay-step" data-step="3" hidden>
  <div class="relay-step-header">
    <span class="relay-step-badge">Step 3 of 3</span>
    <h2>Running <span id="relay-progress-label"></span></h2>
  </div>

  <!-- Progress bar -->
  <div class="relay-progress-bar">
    <div id="relay-progress-fill" class="relay-progress-fill"></div>
  </div>

  <!-- Task status list -->
  <div id="relay-task-list" class="relay-task-list"></div>

  <!-- Review panel (hidden until relay_review_needed SSE) -->
  <div id="relay-review-panel" class="relay-review-panel" hidden>
    <div class="relay-review-header">
      <span>Review Required — Task <span id="review-task-label"></span></span>
    </div>

    <p class="text-muted">Copy this into your AI, then paste the decision below:</p>
    <div class="relay-copy-block">
      <button id="btn-copy-packet">Copy Review Packet</button>
    </div>
    <textarea id="review-packet-text" readonly></textarea>

    <div class="field">
      <label>Paste AI's decision (APPROVED / REWORK: ... / FAILED: ...):</label>
      <textarea id="decision-paste-area"
                placeholder="APPROVED
or
REWORK: your instruction here
or
FAILED: your reason here"></textarea>
    </div>
    <div id="decision-parse-error" class="relay-error" hidden></div>
    <button id="btn-submit-decision" class="btn btn--primary">Submit Decision</button>

    <!-- Replan section (hidden unless FAILED) -->
    <div id="relay-replan-section" hidden>
      <hr>
      <p>Task failed. Generate a replan prompt, get new tasks from AI, then import them.</p>
      <button id="btn-generate-replan" class="btn btn--secondary">Generate Replan Prompt</button>
      <div id="replan-prompt-area" hidden>
        <textarea id="replan-prompt-text" readonly></textarea>
        <button id="btn-copy-replan">Copy Replan Prompt</button>
      </div>
      <div class="field">
        <label>Paste replacement tasks from AI:</label>
        <textarea id="replan-paste-area"></textarea>
      </div>
      <button id="btn-import-replan" class="btn btn--primary">Import Replacement Tasks</button>
    </div>
  </div>

  <!-- Run complete panel (hidden until complete SSE) -->
  <div id="relay-complete-panel" class="relay-complete-panel" hidden>
    <span id="relay-complete-icon"></span>
    <h3 id="relay-complete-title"></h3>
    <p id="relay-complete-message"></p>
    <a href="/history" class="btn btn--secondary">View in History</a>
    <button id="btn-new-relay" class="btn btn--primary">New Relay Run</button>
  </div>
</div>
```

#### relay.css — key styles

```css
.relay-shell          /* page container, max-width 720px, margin auto */
.relay-step-badge     /* "Step N of 3" pill — accent colour */
.relay-instructions   /* numbered instruction cards with accent left border */
.relay-copy-block     /* flex row: label + Copy button (right-aligned) */
.relay-task-list      /* list of task rows */
.relay-task-row       /* task row: index, title, files chip, status badge */
.relay-task-row[data-status="approved"]   /* green left border */
.relay-task-row[data-status="rework"]     /* yellow left border */
.relay-task-row[data-status="running"]    /* blue left border + pulse */
.relay-task-row[data-status="failed"]     /* red left border */
.relay-task-row[data-status="pending"]    /* muted */
.relay-progress-bar   /* outer bar: surface-2, rounded */
.relay-progress-fill  /* inner fill: accent, transition width */
.relay-review-panel   /* card with warning border, padding 20px */
.relay-replan-section /* section that appears on FAILED */
.relay-error          /* red error text below textareas */
.relay-complete-panel /* centered success/failure card */
```

#### relay.js — controller

State:
```javascript
let _step = 1;           // current wizard step (1, 2, or 3)
let _tasks = [];         // imported task list
let _currentReviewTaskId = null;
let _sse = null;
let _goal = '';
```

Key functions:
- `goToStep(n)` — shows step n, hides others
- `generatePrompt()` — POST `/api/relay/generate-prompt`, shows prompt area
- `copyToClipboard(text)` — `navigator.clipboard.writeText(text)`
- `importPlan()` — POST `/api/relay/import-plan`, validates, renders task preview, goToStep(2)
- `renderTaskPreview(tasks)` — populates `#task-preview-list`
- `startRelayRun()` — POST `/api/run` with `{ supervisor: "ai_relay", ...settings }`, connects SSE, goToStep(3)
- `connectSSE()` — listen for `relay_review_needed`, `relay_replan_needed`, `relay_replan_imported`, `task_update`, `complete`, `error`
- `onReviewNeeded(taskId)` — GET `/api/relay/review-packet?task_id=N`, show `#relay-review-panel`
- `submitDecision()` — POST `/api/relay/submit-decision`, handle unparseable, handle FAILED → show replan section
- `generateReplanPrompt()` — POST `/api/relay/replan-prompt`, show `#replan-prompt-area`
- `importReplan()` — POST `/api/relay/import-replan`, hide replan section, resume run
- `updateTaskRow(task)` — updates status badge and border on `#relay-task-list`
- `onComplete(data)` — show `#relay-complete-panel` with success/failure styling

#### Acceptance
1. Step 1: click Generate Prompt → textarea fills, Copy button copies to clipboard ✓
2. Step 1: paste valid JSON → Import Plan → step 2 shows task list ✓
3. Step 1: paste invalid text → error message shown, stays on step 1 ✓
4. Step 2: task list renders with title and file chips ✓
5. Step 2: Back button returns to step 1 ✓
6. Step 3: Start Run → run launches, SSE connects, task rows appear ✓
7. Step 3: `relay_review_needed` SSE → review panel appears with packet ✓
8. Step 3: paste APPROVED → decision submitted, panel hides, next task starts ✓
9. Step 3: paste REWORK text → task row turns yellow, Aider re-runs ✓
10. Step 3: paste FAILED text → replan section appears ✓
11. Step 3: replan prompt generated, replacement tasks imported, run resumes ✓
12. Step 3: run complete → complete panel shown with correct status ✓

---

### M5 — Nav Item + Keyboard Shortcut
**Commit message:** `feat(relay/M5): add Relay nav item and g+a keyboard shortcut`

**Depends on:** M4

Files:
- `ui/templates/base.html`
- `ui/static/js/core/shortcuts.js`

#### base.html — add Relay nav item

Insert between Run and Chat nav items:

```html
<a href="/relay" class="nav-item {% if active_page == 'relay' %}--active{% endif %}" data-page="relay">
  <span class="nav-item-icon">
    <!-- CPU/circuit chip icon (heroicons) -->
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 0 0 2.25-2.25V6.75a2.25 2.25 0 0 0-2.25-2.25H6.75A2.25 2.25 0 0 0 4.5 6.75v10.5a2.25 2.25 0 0 0 2.25 2.25Zm.75-12h9v9h-9v-9Z"/>
    </svg>
  </span>
  <span class="nav-item-label">AI Relay</span>
</a>
```

#### shortcuts.js changes

Add to `NAV_CHORDS`:
```javascript
a: '/relay',   // a = agentic
```

Add to `SHORTCUTS` array:
```javascript
{ keys: ['g', 'a'], desc: 'Go to AI Relay' },
```

#### Acceptance
1. Relay nav item appears in sidebar between Run and Chat ✓
2. `/relay` loads with sidebar active state on Relay ✓
3. Press `g` then `a` on any page → navigates to `/relay` ✓
4. `?` help overlay shows `g a — Go to AI Relay` ✓

---

## Component Reference (Relay-Specific)

| Component | Class | Description |
|---|---|---|
| Step badge | `.relay-step-badge` | "Step N of 3" pill |
| Instructions | `.relay-instructions` | Numbered how-to cards |
| Copy block | `.relay-copy-block` | Label + copy button row |
| Task list | `.relay-task-list` | Ordered task rows |
| Task row | `.relay-task-row[data-status]` | Title + files chip + status |
| Progress bar | `.relay-progress-bar / .relay-progress-fill` | Linear progress |
| Review panel | `.relay-review-panel` | Review packet + decision input |
| Replan section | `.relay-replan-section` | Shown on FAILED |
| Complete panel | `.relay-complete-panel` | Success/failure final card |
| Error text | `.relay-error` | Red inline validation message |

---

## SSE Events (Relay-Specific)

| Event | Key fields | Handled in |
|---|---|---|
| `relay_review_needed` | `task_id`, `request_file` | relay.js → show review panel |
| `relay_replan_needed` | `task_id`, `failed_reason` | relay.js → show replan section |
| `relay_replan_imported` | `task_id`, `new_task_count` | relay.js → hide replan, resume |
| `task_update` | `task{id, status, title}` | relay.js → update task row |
| `complete` | `status`, `elapsed` | relay.js → show complete panel |
| `error` | `message` | relay.js → show error in review panel |

---

## Decision File Format (Written by `submit-decision`)

Matches existing manual supervisor format so `bridge_runner.py` needs no changes
to its decision-reading logic:

```json
{ "task_id": 3, "decision": "pass" }
{ "task_id": 3, "decision": "rework", "instruction": "Move error handling to a middleware layer" }
{ "task_id": 3, "decision": "fail",   "reason": "Wrong approach — use events not polling" }
```

Written to: `<repo_root>/bridge_progress/manual_supervisor/decisions/task_0003_decision.json`

---

## Estimated Code Size

| File | Lines |
|---|---|
| `utils/relay_formatter.py` | ~200 |
| `ui/app.py` additions | ~80 |
| `ui/bridge_runner.py` additions | ~30 |
| `ui/state_store.py` additions | ~20 |
| `ui/templates/relay.html` | ~160 |
| `ui/static/css/pages/relay.css` | ~200 |
| `ui/static/js/pages/relay.js` | ~350 |
| `ui/templates/base.html` additions | ~10 |
| `ui/static/js/core/shortcuts.js` additions | ~2 |
| **Total** | **~1,052** |

---

## Notes for Implementation

- **No `confirm()` or `alert()`** — use inline error divs and relay-error class
- **No npm, no bundler, no framework** — vanilla ES modules only
- **Copy to clipboard**: use `navigator.clipboard.writeText()`, fall back to `document.execCommand('copy')`
- **Paste areas**: plain `<textarea>` — no special paste handling needed
- **SSE**: reuse the existing `SSEClient` from `core/sse.js`
- **relay_formatter.py**: stdlib only — no external packages
- **Decision file path**: zero-padded to 4 digits — `task_0003_decision.json`
- **relay_pending_tasks in state_store**: stored as JSON in `ui/data/relay_tasks.json`, cleared on new import
- Each milestone is **one git commit** — complete the full milestone before committing
- Test each milestone's acceptance criteria before moving on
