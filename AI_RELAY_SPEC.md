# AI Relay Supervisor — Implementation Spec

> **Status:** PLANNED — implement after Chat feature is complete
> **Branch:** `chatbot_llm`
> **Resumption rule:** Read this file, then `UI_REBUILD_PLAN.md` for design system reference.

---

## What It Is

A new supervisor mode called **AI Relay** that lets you use any web-based AI
(ChatGPT Plus, Claude.ai Pro, Gemini Advanced, Grok — any subscription) as the
planning and review brain, with **no API key required**.

You become the relay: the bridge formats everything as ready-to-paste prompts,
and parses the AI's responses when you paste them back.

---

## Why

| | AI Relay | Codex CLI / Claude CLI |
|---|---|---|
| Cost | Free (web subscription) | API credits per token |
| API key needed? | No | Yes (separate from Plus/Pro) |
| Quality | Full GPT-4o / Claude 3.5 intelligence | Same models |
| Speed | Slower (~2-3 copy-pastes per task) | Fully automatic |
| Control | High — you see every decision | Automatic |
| Works with ChatGPT Plus? | Yes | No |
| Works with Claude Pro? | Yes | Via `claude login` only |

---

## Architecture

```
+------------------------------------------------------------------+
|                        AI RELAY FLOW                             |
|                                                                  |
|  PHASE 1 - Plan Generation                                       |
|  +----------------+   copy prompt    +----------------------+   |
|  |  Bridge UI     | --------------> |  ChatGPT / Claude /  |   |
|  |  (generates    |                 |  Gemini / Grok web   |   |
|  |   plan prompt) | <-------------- |  (your subscription) |   |
|  +----------------+   paste plan    +----------------------+   |
|          |                                                       |
|          v                                                       |
|  Bridge parses plan -> N tasks (preview + confirm)              |
|                                                                  |
|  PHASE 2 - Task Loop (repeats N times)                          |
|  +------------------------------------------------------------+  |
|  |  Bridge -> Aider (Task N) -> Ollama implements             |  |
|  |  Bridge generates formatted "review packet"               |  |
|  |  You copy packet -> paste into web AI                      |  |
|  |  Web AI: APPROVED / REWORK: [details] / FAILED: [reason]  |  |
|  |  You paste decision back -> Bridge reads it                |  |
|  |                                                            |  |
|  |  APPROVED  -> Bridge continues to Task N+1                |  |
|  |  REWORK    -> Aider re-runs with AI's instruction         |  |
|  |  FAILED    -> Bridge generates replan prompt              |  |
|  |               -> You paste into AI -> get new tasks        |  |
|  |               -> You import replacement tasks              |  |
|  +------------------------------------------------------------+  |
+------------------------------------------------------------------+
```

---

## Files to Create / Modify

| File | Change |
|---|---|
| `utils/relay_formatter.py` | NEW — generates all prompts and parses AI responses |
| `ui/templates/relay.html` | NEW — dedicated 3-step wizard page |
| `ui/static/css/pages/relay.css` | NEW |
| `ui/static/js/pages/relay.js` | NEW — wizard steps, SSE, copy/paste |
| `ui/app.py` | ADD 6 new routes under `# Chat` section |
| `ui/templates/base.html` | ADD Relay nav item (between Run and Chat) |
| `ui/bridge_runner.py` | ADD `ai_relay` supervisor type |

---

## Milestone Breakdown

### M1 — `utils/relay_formatter.py`

Five functions:

#### `build_plan_prompt(goal, knowledge_context, repo_root)` -> `str`

Generates the prompt the user copies into ChatGPT to get a task plan.

Includes:
- Project context (from `knowledge_context` — file roles, language, patterns, type)
- The user's goal text
- Strict JSON output format instructions
- Rules: max 15 tasks, one file per task, atomic instructions

**Required AI output format:**
```json
{
  "plan_summary": "One sentence describing the overall approach",
  "tasks": [
    {
      "id": 1,
      "title": "Short task title (max 60 chars)",
      "files": ["relative/path/to/file.ext"],
      "instruction": "Exact instruction for Aider — specific and technical",
      "context": "Why this task exists / what it connects to"
    }
  ]
}
```

#### `parse_plan(raw_text)` -> `list[dict]`

Extracts JSON plan from pasted AI response. Handles:
- Bare JSON
- JSON wrapped in ` ```json ... ``` ` code block
- JSON embedded in longer text (extracts first `{` ... `}` block)
- Validates required fields: `id`, `title`, `instruction`
- Raises `ValueError` with human-readable message on bad input

#### `build_review_packet(task, diff, validation_result, attempt, total_tasks, goal)` -> `str`

Generates the review text the user copies into ChatGPT after each task completes.

```
===================================================
BRIDGE REVIEW REQUEST -- Task [N] of [total]
===================================================

ORIGINAL GOAL:
[goal]

TASK [N]: [task title]
INSTRUCTION GIVEN TO AIDER:
[task.instruction]

FILES CHANGED:
[file: +X -Y lines per file]

DIFF:
[unified diff -- max 300 lines, truncated with note if longer]

VALIDATION: [PASSED / FAILED: output]
ATTEMPT: [N] of [max_retries]

===================================================
RESPOND WITH EXACTLY ONE LINE:

APPROVED
REWORK: [your specific instruction -- what to change]
FAILED: [reason -- why this needs replanning]
===================================================
```

#### `parse_decision(raw_text)` -> `dict`

Parses the AI's pasted response. Returns one of:
```python
{"decision": "approved"}
{"decision": "rework", "instruction": "Move error handling to middleware"}
{"decision": "failed",  "reason": "Wrong approach -- use events not polling"}
{"decision": "unparseable", "raw": "..."}  # triggers UI re-paste prompt
```

Rules:
- Case-insensitive on APPROVED / REWORK: / FAILED:
- Scans first 5 lines
- `REWORK:` and `FAILED:` must have non-empty text after colon

#### `build_replan_prompt(task, failed_reason, diff, goal)` -> `str`

For FAILED tasks — generates a prompt asking the AI to produce replacement tasks.
Same JSON format as initial plan but for replacement tasks starting at task N.

---

### M2 — Backend Routes (`ui/app.py`)

Six new routes added under the existing Chat section:

```
POST /api/relay/generate-prompt
  body:    { goal, repo_root }
  returns: { prompt: "...", context_summary: "N files, language X" }

POST /api/relay/import-plan
  body:    { raw_text: "..." }
  returns: { tasks: [...], plan_summary: "...", task_count: N }

GET  /api/relay/review-packet
  query:   ?task_id=N
  returns: { packet: "...", task: {...} }
  reads:   completed task diff from bridge_progress/

POST /api/relay/submit-decision
  body:    { task_id, raw_text: "..." }
  returns: { ok, decision, instruction?, reason?, error? }
  writes:  decision file to bridge_progress/manual_supervisor/decisions/
           (same directory the existing manual supervisor watches -- minimal
           changes to bridge_runner.py required)

POST /api/relay/replan-prompt
  body:    { task_id, failed_reason }
  returns: { prompt: "..." }

POST /api/relay/import-replan
  body:    { raw_text: "...", replace_from_task_id: N }
  returns: { ok, new_tasks: [...] }
```

**Key design:** `submit-decision` writes to the existing
`manual_supervisor/decisions/` directory. `bridge_runner.py` already knows how
to wait for and consume those files — minimal new code needed.

---

### M3 — `ui/bridge_runner.py` changes (~30 lines)

When `supervisor == "ai_relay"`:

1. **Plan phase**: Skip supervisor CLI call entirely. Bridge receives the plan as
   pre-imported JSON (stored in `state_store` after `/api/relay/import-plan`).
2. **Review phase**: After each task, emit `relay_review_needed` SSE event and
   wait for the decision file — exactly like `manual_supervisor` mode.
3. **FAILED handling**: Emit `relay_replan_needed` SSE event — UI shows the
   replan prompt flow.

The relay mode **reuses the entire existing manual supervisor infrastructure**:
decision file mechanism, pause/resume system, task re-queuing on rework.

---

### M4 — `/relay` Page (3-step wizard)

#### Step 1 — Generate Plan
```
+-----------------------------------------------------+
|  Step 1 of 3 -- Generate Plan                       |
|                                                      |
|  Goal  [textarea]                                    |
|  Repo  [folder picker]                               |
|                                                      |
|  Instructions:                                       |
|  1. Click "Generate Prompt"                          |
|  2. Copy it -> paste into ChatGPT / Claude / Gemini  |
|  3. Paste the AI's JSON response below               |
|                                                      |
|  [Generate Prompt btn]                               |
|  [readonly textarea: prompt + Copy btn]              |
|                                                      |
|  Paste AI response here:                            |
|  [editable textarea]                                 |
|  [Import Plan ->]                                    |
+-----------------------------------------------------+
```

#### Step 2 — Confirm Tasks
```
+-----------------------------------------------------+
|  Step 2 of 3 -- Confirm Plan  (8 tasks)             |
|                                                      |
|  "[plan_summary from AI]"                           |
|                                                      |
|  1. [title]  [files chip]                           |
|  2. [title]  [files chip]                           |
|  ...                                                 |
|                                                      |
|  [<- Re-import]           [Start Relay Run ->]      |
+-----------------------------------------------------+
```

#### Step 3 — Run + Review Loop
```
+-----------------------------------------------------+
|  Step 3 of 3 -- Running  (Task 3 of 8)              |
|                                                      |
|  [============================----] 37%              |
|                                                      |
|  [v] Task 1: Create User model       APPROVED       |
|  [v] Task 2: Add auth endpoint       APPROVED       |
|  [*] Task 3: Add tests               Aider working..|
|  [ ] Task 4-8: pending                              |
|                                                      |
|  -- Review Required -------------------------------- |
|  Task 3 complete. Copy this into your AI:           |
|  [review packet textarea + Copy button]              |
|                                                      |
|  Paste AI's decision:                               |
|  [decision textarea]                                 |
|  [Submit Decision]                                   |
|                                                      |
|  (if FAILED appears): [Generate Replan Prompt]      |
+-----------------------------------------------------+
```

---

### M5 — Nav + Shortcuts

- Add **Relay** nav item in `base.html` between Run and Chat (robot/circuit icon)
- Shortcut: `g + a` -> `/relay`  ("a" = agentic)
- `SHORTCUTS` array entry: `{ keys: ['g', 'a'], desc: 'Go to AI Relay' }`
- `NAV_CHORDS` entry: `a: '/relay'`

---

## Decision Parse UX

| AI responded | Bridge action | UI shows |
|---|---|---|
| `APPROVED` | Writes decision file, continues | Green badge, next task starts automatically |
| `REWORK: [details]` | Re-queues task with instruction | Yellow badge, Aider re-runs |
| `FAILED: [reason]` | Pauses run | "Generate Replan Prompt" button appears |
| Unparseable | Nothing | Warning: "Couldn't parse -- did AI respond with APPROVED / REWORK / FAILED?" |

**FAILED -> Replan flow:**
1. Bridge shows "Generate Replan Prompt" button
2. User clicks -> copies prompt -> pastes into AI -> AI returns replacement JSON
3. User pastes back -> "Import Replan" -> replacement tasks splice into queue
4. Run continues with new tasks from position N

---

## Estimated Code Size

| File | Estimated lines |
|---|---|
| `utils/relay_formatter.py` | ~200 |
| `ui/templates/relay.html` | ~160 |
| `ui/static/css/pages/relay.css` | ~200 |
| `ui/static/js/pages/relay.js` | ~350 |
| `ui/app.py` additions | ~80 |
| `ui/templates/base.html` additions | ~10 |
| `ui/bridge_runner.py` additions | ~30 |
| **Total** | **~1,030** |

One focused implementation session.

---

## Subscription Compatibility

| Web AI | Works as relay? | Notes |
|---|---|---|
| ChatGPT (Plus / Pro) | Yes | GPT-4o, o1 — copy-paste in browser |
| Claude.ai (Pro) | Yes | Claude 3.5 Sonnet — copy-paste in browser |
| Gemini Advanced | Yes | Gemini 1.5 Pro — copy-paste in browser |
| Cursor AI chat | Yes | Built-in chat panel |
| Windsurf AI chat | Yes | Built-in chat panel |
| Grok (X Premium) | Yes | copy-paste in browser |

The bridge does not care which AI you use — it only needs the response to contain
`APPROVED` / `REWORK:` / `FAILED:` formatted text.

---

## Implementation Order

```
M1  utils/relay_formatter.py          (core logic, no UI deps)
M2  ui/app.py relay routes            (depends on M1)
M3  ui/bridge_runner.py ai_relay type (depends on M2)
M4  relay.html + relay.css + relay.js (depends on M2 + M3)
M5  base.html nav + shortcuts.js      (depends on M4)
```
