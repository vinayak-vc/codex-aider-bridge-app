## AI Relay Supervisor — Implementation Spec

---

### Concept Summary

A new supervisor mode called **"AI Relay"** where you use any web-based AI (ChatGPT Plus, Claude.ai Pro, Gemini, Grok — anything) as the brain via copy-paste, with no API key. The bridge formats everything as ready-to-paste prompts and parses the AI's responses.

---

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         AI RELAY FLOW                           │
│                                                                  │
│  PHASE 1 — Plan Generation                                       │
│  ┌────────────────┐   copy prompt    ┌──────────────────────┐   │
│  │  Bridge UI     │ ─────────────→   │  ChatGPT / Claude /  │   │
│  │  (generates    │                  │  Gemini / Grok web   │   │
│  │   plan prompt) │ ←─────────────   │  (your subscription) │   │
│  └────────────────┘   paste plan     └──────────────────────┘   │
│          ↓                                                        │
│  Bridge parses plan → N tasks                                    │
│                                                                  │
│  PHASE 2 — Task Loop (repeats N times)                           │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Bridge → Aider (Task N) → Ollama implements               │  │
│  │  Bridge generates "review packet"                          │  │
│  │  You copy packet → paste into web AI                       │  │
│  │  Web AI: APPROVED / REWORK: [details] / FAILED: [reason]  │  │
│  │  You paste decision back → Bridge continues                │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### Files to Create / Modify

| File | Change |
|---|---|
| `utils/relay_formatter.py` | NEW — generates all prompts and parses AI responses |
| `ui/templates/relay.html` | NEW — dedicated relay page |
| `ui/static/css/pages/relay.css` | NEW |
| `ui/static/js/pages/relay.js` | NEW — relay coordinator |
| `ui/app.py` | ADD 6 new routes |
| `ui/templates/base.html` | ADD Relay nav item |
| `ui/bridge_runner.py` | ADD `ai_relay` supervisor support |

---

### Phase 1 — `utils/relay_formatter.py`

This is the core utility. Three responsibilities:

#### A. `build_plan_prompt(goal, knowledge_context, repo_root)` → `str`

Generates the prompt the user copies into ChatGPT. Includes:

```
You are a software planning assistant for a project.

PROJECT CONTEXT:
[knowledge_context — file roles, patterns, language, type]

GOAL:
[user's goal text]

Create a detailed task plan. Output ONLY valid JSON, no markdown, no explanation:

{
  "plan_summary": "One sentence describing the overall approach",
  "tasks": [
    {
      "id": 1,
      "title": "Short title (max 60 chars)",
      "files": ["relative/path/to/file.ext"],
      "instruction": "Exact instruction for the code editor — be specific and technical",
      "context": "Why this task exists / what it connects to"
    }
  ]
}

Rules:
- Each task = one atomic change, one or two files max
- Order tasks: dependencies first
- "instruction" is sent directly to Aider — no ambiguity
- Maximum 15 tasks
```

#### B. `parse_plan(raw_text)` → `list[dict]`

Extracts the JSON plan from pasted AI response. Handles:
- Bare JSON
- JSON wrapped in markdown code block (\`\`\`json ... \`\`\`)
- JSON inside a longer response (extract first `{` ... `}` block)
- Validates required fields: `id`, `title`, `instruction`
- Raises `ValueError` with human-readable message on bad input

#### C. `build_review_packet(task, diff, validation_result, attempt, total_tasks, goal)` → `str`

Generates the review text the user copies into ChatGPT after Aider finishes a task:

```
═══════════════════════════════════════════════
BRIDGE REVIEW REQUEST — Task [N] of [total]
═══════════════════════════════════════════════

ORIGINAL GOAL:
[goal]

TASK [N]: [task title]
INSTRUCTION GIVEN TO AIDER:
[task.instruction]

FILES CHANGED:
[file: +X -Y lines, per file]

DIFF:
```diff
[unified diff, max 300 lines — truncated if longer]
```

VALIDATION: [PASSED / FAILED: output]
ATTEMPT: [N] of [max_retries]

═══════════════════════════════════════════════
RESPOND WITH EXACTLY ONE LINE:

APPROVED
REWORK: [your specific instruction — what to change]
FAILED: [reason — why this needs replanning]
═══════════════════════════════════════════════
```

#### D. `parse_decision(raw_text)` → `dict`

Parses the AI's response. Returns:
```python
{"decision": "approved"}
{"decision": "rework", "instruction": "Move error handling to middleware"}
{"decision": "failed",  "reason": "Wrong approach — use events not polling"}
```

Rules:
- Case-insensitive matching
- Accept `APPROVED` anywhere in the first 3 lines
- `REWORK:` must have text after the colon
- `FAILED:` must have text after the colon
- If unparseable → return `{"decision": "unparseable", "raw": text}` so UI can ask user to re-paste

#### E. `build_replan_prompt(task, failed_reason, diff, goal)` → `str`

For when a task is marked FAILED — generates a prompt asking the AI to fix the plan:

```
═══════════════════════════════════════════════
BRIDGE REPLAN REQUEST
═══════════════════════════════════════════════

Task [N] FAILED: [task title]
Failure reason: [reason from your previous FAILED decision]

ORIGINAL INSTRUCTION:
[what aider was told]

FAILED IMPLEMENTATION (diff):
```diff
[diff]
```

Please provide a replacement plan for this task (and any follow-up tasks if needed).
Use the same JSON format:

{
  "tasks": [
    { "id": [N], "title": "...", "files": [...], "instruction": "...", "context": "..." }
  ]
}
```

---

### Phase 2 — Backend Routes (`ui/app.py`)

```
POST /api/relay/generate-prompt
  body:    { goal, repo_root }
  returns: { prompt: "...", task_count_estimate: N, context_summary: "..." }
  does:    calls relay_formatter.build_plan_prompt()

POST /api/relay/import-plan
  body:    { raw_text: "..." }
  returns: { tasks: [...], plan_summary: "...", task_count: N }
  does:    calls relay_formatter.parse_plan(), validates, returns task list

GET  /api/relay/review-packet
  query:   ?task_id=N
  returns: { packet: "...", task: {...}, diff: "..." }
  does:    reads completed task diff from bridge_progress, 
           calls relay_formatter.build_review_packet()

POST /api/relay/submit-decision
  body:    { task_id, raw_text: "..." }
  returns: { ok, decision, instruction?, reason?, error? }
  does:    calls relay_formatter.parse_decision(),
           writes decision to manual_supervisor/decisions/ dir
           (same dir that manual supervisor already watches)

POST /api/relay/replan-prompt
  body:    { task_id, failed_reason }
  returns: { prompt: "..." }
  does:    builds replan prompt for the user to paste into AI

POST /api/relay/import-replan
  body:    { raw_text: "...", replace_from_task_id: N }
  returns: { ok, new_tasks: [...] }
  does:    parses replacement tasks, injects them into the running plan
```

**Key insight:** The `submit-decision` route writes to the same `bridge_progress/manual_supervisor/decisions/` directory that the existing manual supervisor already watches. This means `bridge_runner.py` needs minimal changes — it already knows how to wait for and read decision files.

---

### Phase 3 — `ui/bridge_runner.py` changes

Add `ai_relay` as a supervisor type. When `supervisor == "ai_relay"`:

1. **Plan phase**: Instead of calling a CLI to generate a plan, the bridge receives the plan as a pre-imported JSON (via `/api/relay/import-plan`) and skips the supervisor plan-generation step
2. **Review phase**: After each task, instead of calling the supervisor CLI for review, emit a `relay_review_needed` SSE event (same structure as `review_required`) and wait for the decision file — exactly like manual supervisor
3. **FAILED handling**: Emit `relay_replan_needed` SSE event — UI shows "paste replan prompt into AI, then import the new tasks"

This means the relay mode **reuses the entire existing manual supervisor infrastructure** — the decision file mechanism, the pause/resume system, task re-queuing on rework. The only new code is the formatter and the import/parse routes.

---

### Phase 4 — `/relay` Page (`ui/templates/relay.html`)

Dedicated page with a **3-step wizard UI**:

#### Step 1: Generate Plan
```
┌─────────────────────────────────────────────────────┐
│  Step 1 of 3 — Generate Plan                        │
│                                                      │
│  [Goal textarea]                                     │
│  [Repo folder picker]                                │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ 1. Click "Generate Prompt"                   │   │
│  │ 2. Copy the prompt below                     │   │
│  │ 3. Paste into ChatGPT / Claude / Gemini      │   │
│  │ 4. Paste the AI's response here              │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  [Generate Prompt]                                   │
│                                                      │
│  ── Prompt to copy ──────────────────────────────   │
│  [readonly textarea with "Copy" button]              │
│                                                      │
│  ── Paste AI response here ─────────────────────    │
│  [editable textarea]                                 │
│  [Import Plan →]                                     │
└─────────────────────────────────────────────────────┘
```

#### Step 2: Confirm Tasks
```
┌─────────────────────────────────────────────────────┐
│  Step 2 of 3 — Confirm Plan (8 tasks)               │
│                                                      │
│  Plan summary: [AI's plan_summary]                  │
│                                                      │
│  1. [title]  [files]                                │
│  2. [title]  [files]                                │
│  ...                                                 │
│                                                      │
│  [← Re-import]          [Start Relay Run →]         │
└─────────────────────────────────────────────────────┘
```

#### Step 3: Run + Review Loop
```
┌─────────────────────────────────────────────────────┐
│  Step 3 of 3 — Running (Task 3 of 8)                │
│                                                      │
│  [progress bar / ring]                               │
│                                                      │
│  ✓ Task 1: Create User model    APPROVED            │
│  ✓ Task 2: Add auth endpoint    APPROVED            │
│  ● Task 3: Add tests            Aider working...    │
│  ○ Task 4–8: pending                                │
│                                                      │
│  ─── Review Required ─────────────────────────────  │
│  Task 3 complete. Copy this into your AI:           │
│  [review packet textarea with "Copy" button]         │
│                                                      │
│  Paste AI's decision:                               │
│  [decision textarea]                                 │
│  [Submit Decision]                                   │
│                                                      │
│  If AI said FAILED: [Generate Replan Prompt]        │
└─────────────────────────────────────────────────────┘
```

---

### Phase 5 — Decision Parse UX

Three outcomes after user pastes a decision:

| AI said | Bridge does | UI shows |
|---|---|---|
| `APPROVED` | Writes decision file, continues | Green badge on task, next task starts |
| `REWORK: [details]` | Re-queues task with added instruction | Task gets yellow "rework" badge, Aider re-runs |
| `FAILED: [reason]` | Pauses run | "Generate Replan Prompt" button appears |
| Unparseable | Nothing | "Couldn't parse — did the AI respond with APPROVED/REWORK/FAILED?" + retry textarea |

For FAILED → Replan:
1. Bridge generates replan prompt (user clicks button, copies it)
2. User pastes into AI, AI returns new task JSON
3. User pastes back → "Import Replan" → new tasks splice into the queue at position N
4. Run continues with replacement tasks

---

### Phase 6 — Nav + Shortcuts

- Add **Relay** nav item in `base.html` (robot/link icon) between Run and Chat
- Shortcut: `g + a` → `/relay`
- Help overlay updated

---

### Summary of new code

```
utils/relay_formatter.py          ~200 lines  (prompts, parsers)
ui/templates/relay.html           ~150 lines  (3-step wizard)
ui/static/css/pages/relay.css     ~200 lines  (wizard + review styles)
ui/static/js/pages/relay.js       ~350 lines  (wizard steps, SSE, copy/paste)
ui/app.py                         +80 lines   (6 new routes)
ui/templates/base.html            +10 lines   (nav item)
ui/bridge_runner.py               +30 lines   (ai_relay supervisor type)
```

Total: ~1,020 lines of new code. One focused implementation session.

---

### What you get

- Works with **any web AI** — ChatGPT Plus, Claude.ai Pro, Gemini Advanced, Grok, even Perplexity
- **Zero API keys** — you're the relay, subscriptions cover it
- **Full supervisor intelligence** — plan generation, diff review, rework decisions, replan on failure — all using the best available AI
- **~2-3 copy-pastes per task** — for a 10-task run, about 20-30 interactions, each taking 15-30 seconds
- **Aider stays local** — Ollama handles all code changes, no code leaves your machine except what you manually copy

---

Ready to implement when you say the word.