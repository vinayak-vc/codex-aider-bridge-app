# Natural Language Run Mode

## Goal

Allow the **Run** panel to accept normal conversational input such as:

- "I want a login screen with email and password, and keep the current UI style."
- "The inventory bug is still happening. Please inspect the save flow first, then fix only the broken part."
- "Add a settings page, but do not touch multiplayer code yet."

Instead of requiring the user to manually translate that into a strict technical goal, the app should:

1. understand the conversational request
2. ask for clarification when needed
3. turn the request into a structured execution brief
4. produce or refine the task plan
5. launch the existing bridge + Aider workflow

This would make the Run panel feel closer to a Claude/Codex-style conversation while still keeping the bridge architecture intact.

---

## Short Answer

Yes, this is possible.

The current architecture already has most of the hard parts:

- a persistent UI
- a project-aware chat experience
- saved per-project state
- bridge command construction
- AI Relay plan generation and confirmation
- a run engine that already knows how to execute structured tasks through Aider

What is missing is a new orchestration layer between "user says something naturally" and "bridge receives a strict goal / plan".

---

## Recommended Product Shape

The cleanest design is to add a new mode inside **Run**:

- `Structured Run`
  Current behavior. User fills goal and advanced fields directly.

- `Natural Language Run`
  User talks in normal language. The UI behaves like a lightweight project driver.

Natural Language Run should not replace the existing structured mode at first. It should sit on top of it and generate the structured payload that the current bridge already understands.

---

## Core Idea

The natural-language feature should be built as a **Run Orchestrator**.

That orchestrator would:

1. collect conversation turns
2. load project context
3. maintain a working execution brief
4. decide whether clarification is needed
5. produce a structured run request
6. optionally produce a task plan
7. hand off to the existing run engine

This keeps the architecture modular:

- conversational UX stays in the UI/orchestrator layer
- task execution stays in the bridge
- code writing still stays in Aider

---

## Proposed Architecture

### 1. Run Conversation Session

Add a new persisted session type per project:

- `run_conversations.json`

Each session stores:

- project key
- conversation messages
- current status
  - `drafting`
  - `needs_clarification`
  - `ready_to_plan`
  - `ready_to_run`
  - `running`
  - `completed`
- derived execution brief
- selected model / supervisor
- generated plan metadata
- last linked run id

This should be separate from normal chat history because the intent is different:

- Chat = open-ended discussion
- Run Conversation = execution-oriented conversation that eventually becomes a run

### 2. Run Orchestrator Service

Add a backend service layer, for example:

- `ui/run_orchestrator.py`

Responsibilities:

- take the latest conversational user message
- build a prompt with project context
- call the selected reasoning backend
- return one of these outcomes:
  - `assistant_message`
  - `clarification_request`
  - `execution_brief_update`
  - `plan_ready`
  - `run_ready`

The orchestrator should not execute code directly.
Its job is to transform human conversation into a structured request.

### 3. Structured Execution Brief

Introduce a normalized object that the rest of the system can trust.

Example:

```json
{
  "title": "Add login screen",
  "goal": "Implement a login screen with email/password and preserve the current UI style.",
  "constraints": [
    "Do not change multiplayer code",
    "Keep existing design language"
  ],
  "acceptance_criteria": [
    "User can enter email and password",
    "Invalid login shows an error message",
    "Existing screens continue to work"
  ],
  "repo_root": "H:\\Project",
  "aider_model": "ollama/qwen2.5-coder:14b",
  "supervisor_mode": "codex"
}
```

This brief becomes the stable contract between the conversational layer and the current bridge execution layer.

### 4. Planner Backends

Natural-language mode can support multiple planning backends:

- `codex`
- `claude`
- `cursor`
- `windsurf`
- `ai_relay`
- `manual`

Recommended behavior:

- If the backend can converse programmatically, use it for clarification + plan generation.
- If the backend cannot be called programmatically in a reliable conversational way, fall back to AI Relay style copy-paste or to a local guided planner prompt.

### 5. Plan Hand-off

Once the brief is good enough:

1. generate the task plan
2. show the plan in a confirmation UI
3. let the user edit / approve it
4. start the existing bridge run with:
   - `goal`
   - `repo_root`
   - `aider_model`
   - supervisor settings
   - optional `plan_file`

This reuses the current `BridgeRun.build_command()` flow instead of replacing it.

### 6. Run Panel UX

Recommended Run page layout for natural-language mode:

- top mode switch:
  - `Structured`
  - `Natural Language`
- conversational message list
- input box with send button
- live "Execution Brief" side panel
- status chip:
  - `Drafting`
  - `Need clarification`
  - `Ready to plan`
  - `Ready to run`
  - `Running`
- actions:
  - `Generate Plan`
  - `Approve Brief`
  - `Launch Run`
  - `Stop`
  - `New Run Conversation`

---

## Backend Flow

### Phase A: Conversation

1. User opens Run panel in Natural Language mode.
2. User types a normal request.
3. Backend loads:
   - repo root
   - project knowledge
   - recent run history
   - optional chat summary
4. Orchestrator prompts the selected backend.
5. Backend responds with:
   - assistant reply for the user
   - structured extraction for the execution brief
   - clarification flag if needed

### Phase B: Planning

1. Once the brief is sufficient, user clicks `Generate Plan` or the system suggests it.
2. Planner backend converts the brief into the existing task JSON schema.
3. UI shows task list for confirmation.
4. User can:
   - approve
   - edit constraints
   - regenerate
   - switch to AI Relay if needed

### Phase C: Execution

1. Approved brief + plan are mapped into existing bridge settings.
2. Existing run engine launches unchanged.
3. Run page keeps showing normal logs and progress.
4. Conversation session is linked to the run id for traceability.

---

## Why This Is Safer Than Letting Chat Directly Control Aider

A tempting shortcut is:

- user chats naturally
- assistant immediately launches Aider work

That is possible, but it is riskier.

Reasons:

- open-ended chat can hide ambiguity
- execution constraints may not be explicit
- users may not realize what will be changed
- bridge validation expects a structured task workflow

The safer architecture is:

- conversation
- explicit brief
- explicit plan
- existing bridge execution

This preserves the product's current strength: structured, reviewable execution.

---

## Technical Options

## Option A: Full Conversational Run Orchestrator

This is the recommended long-term solution.

### Pros

- closest to Claude/Codex conversational workflow
- keeps strong bridge architecture
- supports clarification before execution
- reusable across Codex, Claude, and AI Relay
- can persist and resume per project

### Cons

- most implementation work
- needs a new orchestration state machine
- requires careful prompt contracts and error handling

## Option B: Goal Rewriter Only

Smaller version.

User writes conversational text, and the system only converts it into:

- cleaned goal
- clarifications
- acceptance criteria

Then the existing Run panel remains mostly unchanged.

### Pros

- much faster to build
- low risk
- minimal UI changes

### Cons

- does not feel fully conversational
- still pushes plan generation into the old workflow
- less differentiation from current Run panel

## Option C: Chat-to-Run Hand-off

Use the existing chat drawer as the conversational front end.

Add a button like:

- `Use This In Run`

That button would convert a chat conversation into a run brief and open Run pre-filled.

### Pros

- reuses current chat system
- very fast to ship
- less UI duplication

### Cons

- chat and execution intent remain mixed
- harder to model multi-step execution states cleanly
- weaker product separation than a dedicated run conversation mode

---

## Recommendation

Recommended rollout:

1. ship `Goal Rewriter`
2. add `Chat-to-Run Hand-off`
3. build `Full Conversational Run Orchestrator`

This sequence delivers value early without blocking on the full architecture.

---

## Milestone Plan

## Milestone 1: Conversational Goal Assistant

Goal:
Allow users to write in normal language and get a structured execution brief.

Scope:

- add Natural Language toggle to Run panel
- add backend endpoint such as `/api/run/brief`
- accept freeform text
- return:
  - cleaned goal
  - assumptions
  - constraints
  - acceptance criteria
  - clarification questions
- show generated brief under the input
- allow `Apply to Run`

Success criteria:

- user can type non-technical requests
- system outputs a usable structured goal
- existing run launch still works with no bridge-core changes

## Milestone 2: Persisted Run Conversations

Goal:
Make run-oriented conversations resumable per project.

Scope:

- add persisted run conversation state
- restore old run conversation on app restart
- support `New Run Conversation`
- show conversation status chips
- link a conversation to its structured brief

Success criteria:

- user can close app and continue the same run conversation later
- derived brief is not lost

## Milestone 3: Conversational Plan Generation

Goal:
Generate task plans directly from the run conversation brief.

Scope:

- add planner call from the run orchestrator
- render generated task list in Run panel
- allow regenerate and confirm
- save approved plan for traceability

Success criteria:

- user can go from conversation to plan without opening AI Relay manually
- task output follows existing bridge schema

## Milestone 4: Launch and Traceability

Goal:
Tie conversational runs into the current bridge execution flow.

Scope:

- launch bridge from approved brief + plan
- attach run id to conversation session
- allow viewing:
  - source conversation
  - derived brief
  - final plan
  - run result

Success criteria:

- run launches through the current engine
- operator can audit how the run was derived

## Milestone 5: Clarification and Safety Layer

Goal:
Avoid bad runs caused by ambiguity.

Scope:

- add confidence scoring for brief completeness
- block launch when required fields are missing
- detect risky instructions such as:
  - huge repo-wide refactors
  - destructive deletes
  - unclear technology targets
- require user confirmation on risky actions

Success criteria:

- ambiguous requests do not go straight into execution
- safety rules are explicit and understandable

## Milestone 6: Multi-backend Conversational Planning

Goal:
Support the same experience across Codex, Claude, and AI Relay style flows.

Scope:

- backend adapter interface for conversational planning
- pluggable implementations per supervisor
- AI Relay fallback prompt generation when a live conversational API is not available

Success criteria:

- same UI works with multiple supervisor backends
- unsupported live conversation backends can still use a relay fallback

---

## Suggested Modules

- `ui/run_orchestrator.py`
  Orchestrator state machine and backend adapters.

- `ui/run_conversation_store.py`
  Persistence for run conversation sessions.

- `ui/static/js/pages/run.js`
  Add natural-language mode, conversation UI, and brief rendering.

- `ui/templates/run.html`
  Add mode switch and execution-brief panel.

- `ui/app.py`
  Add endpoints for run conversation, brief generation, plan generation, and session restore.

- `utils/brief_builder.py`
  Normalize conversational output into a stable execution brief shape.

---

## API Sketch

### `POST /api/run/brief`

Input:

```json
{
  "repo_root": "H:\\Project",
  "message": "Add a login flow and do not touch multiplayer.",
  "conversation_id": "runconv_123",
  "backend": "codex"
}
```

Output:

```json
{
  "assistant_message": "I can do that. I will keep multiplayer untouched. Do you want mock auth or real backend auth?",
  "status": "needs_clarification",
  "brief": {
    "goal": "Implement a login flow while leaving multiplayer code untouched.",
    "constraints": ["Do not modify multiplayer code"],
    "acceptance_criteria": ["User can enter credentials", "Login errors are shown"]
  },
  "questions": [
    "Should authentication be mocked or integrated with a real backend?"
  ]
}
```

### `POST /api/run/plan-from-brief`

Input:

```json
{
  "conversation_id": "runconv_123"
}
```

Output:

```json
{
  "status": "ok",
  "tasks": [
    {
      "id": 1,
      "files": ["ui/login_screen.py"],
      "instruction": "Create the login UI screen.",
      "type": "create"
    }
  ]
}
```

### `POST /api/run/launch-from-brief`

Input:

```json
{
  "conversation_id": "runconv_123"
}
```

Output:

```json
{
  "status": "ok",
  "run_id": "run_20260402_001"
}
```

---

## Risks

- backend conversational CLIs may not all support stable machine-readable intermediate output
- prompt contracts can drift between backends
- some users may expect the assistant to edit immediately without confirmation
- long conversations can create noisy or conflicting intent
- mixing normal chat and execution chat can confuse state unless clearly separated

---

## Practical Alternative If Full Version Feels Too Heavy

If the full conversational orchestrator is too large right now, the best alternative is:

### Guided Goal Composer

The user types in natural language, and the UI immediately generates:

- clean technical goal
- constraints
- acceptance criteria
- recommended clarifications

Then the user clicks:

- `Use In Run`
- `Send To AI Relay`
- `Save As Brief`

This gives most of the usability win with much less architectural risk.

---

## Final Recommendation

Yes, the functionality is realistic and fits this project.

Best path:

1. build a **Natural Language Run** mode as an orchestrator layer above the current run engine
2. keep execution on the existing bridge + Aider pipeline
3. start with a smaller **Goal Rewriter / Guided Goal Composer**
4. grow it into a full conversational run workflow with persisted sessions and plan generation

That approach gives conversational UX without throwing away the current structured execution architecture.
