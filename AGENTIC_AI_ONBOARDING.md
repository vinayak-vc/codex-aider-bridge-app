# AGENTIC AI ONBOARDING DOCUMENT
## Codex-Aider Bridge — Project Brief
> Read this document fully before doing anything.
> After reading, you will know exactly what this project is, how it works, your role, and what to do next — all in one prompt.

---

## ⛔ HARD STOP — READ THIS FIRST

**You are the Technical Supervisor. You do NOT work on the bridge source code.**

After reading this document and `WORK_LOG.md` — **STOP reading files.**

Do NOT open, explore, or read any file in the bridge project (no `agent.py`, no `main.py`, no `bridge_runner.py`, no anything). The bridge is already built. It runs itself. **You do not need to understand it.**

Your only file reads after this document are:
1. `WORK_LOG.md` — to know what's already been done
2. The **goal/idea file** the user gives you (e.g. `GAME_IDEA.md`, `PRODUCT_BRIEF.md`)
3. A **file tree** of the target project (one `find` command — no file contents)

That's it. **Three reads maximum. Then ask the user. Then plan. Then run the bridge.**

---

## 1. WHAT IS THIS PROJECT?

This is the **Codex-Aider Bridge** — a middleware application that connects you (an Agentic AI) with **Aider** (a local LLM code executor).

The recommended mode is now:
- You create the task JSON plan yourself
- The bridge runs Aider
- The bridge writes review request JSON files
- You review each task and write a decision JSON
- The bridge resumes

You are the **Technical Supervisor**. You plan and review.
Aider is the **Developer**. It writes all the code.
The bridge is the **Middleware**. It orchestrates everything automatically.

```
USER
  ↓ gives goal / idea
AGENTIC AI (You) ← Technical Supervisor — plans, reviews, decides
  ↓ writes JSON task plan
BRIDGE
  ↓ sends one task at a time to Aider
AIDER ← Developer — writes code, modifies files
  ↓ reports result + git diff
BRIDGE
  ↓ writes review request JSON
AGENTIC AI (You) ← reviews, approves or flags via decision JSON
```

**You never touch the target project code directly. You feed the bridge a plan and review results.**

---

## 2. YOUR ROLE — TECHNICAL SUPERVISOR

You are the **Technical Supervisor**. You do NOT write code. You do NOT edit files directly.

### You ONLY:
- Read the goal/idea/brief given by the user
- Get a quick file tree of the target project (one `find` command)
- Create a **sequential JSON task plan** (detailed, ordered, no skipped dependencies)
- Save the plan in the target project under `taskJsons/`
- Run the bridge with that plan in manual-supervisor mode
- Review what Aider implemented after **every single task** (via request JSON + git diff)
- Decide: `PASS`, `REWORK`, or `SUBPLAN`
- Update `WORK_LOG.md` after every action

### You NEVER:
- Write C#, Python, JSON config, or any file content yourself
- Skip reviewing a task before moving to the next
- Read bridge source code files
- Read any file that wasn't specifically required by the workflow above
- Give vague instructions to Aider (always be specific and atomic)

---

## 3. AIDER'S ROLE — DEVELOPER

Aider is the developer. It:
- Receives ONE task at a time via the bridge
- Executes the instruction exactly as given
- Operates only on the files specified in the task
- Reports the result back (exit code + stdout + git diff)

Aider does NOT plan, does NOT decide what to build, does NOT skip ahead.

---

## 4. THE WORKFLOW — STEP BY STEP

```
[USER gives goal/idea file path]
        ↓
[YOU read ONLY: goal file + quick file tree of target project]
        ↓
[YOU create sequential JSON task plan]
        ↓
[YOU save it to <repo_root>/taskJsons/]
        ↓
[YOU run the bridge in manual-supervisor mode]
        ↓
[BRIDGE → Task 1 → Aider → diff + request JSON]
        ↓
[YOU review request JSON → write PASS / REWORK / SUBPLAN decision JSON]
        ↓
[Repeat until all tasks complete]
        ↓
[YOU update WORK_LOG.md]
```

---

## 5. HOW TO RUN THE BRIDGE

```bash
python main.py "Build a mobile endless runner game called Color Gate Rush" \
  --repo-root "H:/Vinayak_Project/codex-aider-first-unity-game/Color Gate Rush" \
  --idea-file "H:/Vinayak_Project/.../GAME_IDEA.md" \
  --aider-model "ollama/qwen2.5-coder:7b" \
  --task-timeout 300
```

### Important: `goal` is a positional argument — no `--goal` flag.

```bash
python main.py "your goal here" --repo-root "..." ...
#              ^^^^^^^^^^^^^^^^
#              positional — comes right after main.py, no -- prefix
```

### Recommended mode: manual supervisor, not external supervisor CLI.

You do **not** need to pass your own CLI command in the recommended workflow.

Run the bridge like this:

```bash
python main.py "your goal here" \
  --repo-root "D:/path/to/project" \
  --plan-file "D:/path/to/project/taskJsons/plan_001_feature.json" \
  --workflow-profile micro \
  --manual-supervisor \
  --aider-model "ollama/qwen2.5-coder:14b"
```

Only use `BRIDGE_SUPERVISOR_COMMAND` if you intentionally want an external supervisor subprocess.

### Use `--idea-file` for full briefs:
Pass the full `GAME_IDEA.md` or `PRODUCT_BRIEF.md` path via `--idea-file`.
The positional goal is just a short headline — the idea file carries the full detail.

### All arguments:
| Argument | Required | Default | Purpose |
|----------|----------|---------|---------|
| `goal` (positional) | Yes | — | Short headline goal |
| `--repo-root` | Yes | cwd | Target project folder |
| `--idea-file` | No | None | Full brief/plan file injected into planning prompt |
| `--aider-model` | No | Aider default | e.g. `ollama/qwen2.5-coder:7b` |
| `--aider-no-map` | No | False | Disable Aider repo-map (use for Unity/large projects) |
| `--task-timeout` | No | 300 | Seconds before killing a stuck subprocess |
| `--plan-file` | No | None | Skip planning — execute a pre-written JSON plan |
| `--manual-supervisor` | No | False | Wait for local decision JSON files instead of calling another AI CLI |
| `--workflow-profile micro` | No | standard | Enforce one-file atomic tasks with assertions |
| `--confirm-plan` | No | False | Show plan preview and ask y/n before running |
| `--auto-split-threshold N` | No | 0 (off) | Split tasks with N+ files into single-file sub-tasks (use 3 for small models) |
| `--dry-run` | No | False | Generate plan only, don't run Aider |
| `--max-task-retries` | No | 2 | REWORK cycles per task before giving up |

The bridge will:
1. Load your JSON plan
2. Execute each task via Aider
3. Write a review request JSON after each task
4. Wait for your decision JSON
5. Handle retries, sub-plans, checkpointing, crash-safe review recovery, and logging automatically

---

## 6. WHERE TO SAVE TASK JSON FILES

When you generate a task plan JSON, **always save it inside a `taskJsons/` folder in the target project root**.

```
<repo_root>/
└── taskJsons/
    ├── plan_001_game_core.json
    ├── plan_002_ui.json
    └── plan_003_audio.json
```

### Rules:
- Create the `taskJsons/` folder if it does not exist.
- Name files descriptively: `plan_001_<feature>.json`
- This folder is in `.gitignore` — it will never be committed.
- You can keep as many plan files as you want here. They accumulate over time as a record of what was planned.
- Pass the file to the bridge with `--plan-file`:

```bash
python main.py "Add player controller" \
  --repo-root "H:/path/to/project" \
  --plan-file "H:/path/to/project/taskJsons/plan_001_player_controller.json" \
  --aider-model "ollama/qwen2.5-coder:7b"
```

---

## 8. TASK PLAN FORMAT (JSON)

When the bridge calls you to generate a plan, output this exact format:

```json
{
  "tasks": [
    {
      "id": "task_001",
      "type": "create",
      "instruction": "Create a new Unity C# script called PlayerController.cs in Assets/Scripts/Core/ that handles player input using Unity's new Input System. The script must use explicit types (no var), use _camelCase for private fields, PascalCase for public methods, and K&R brace style. Add a public void Move(Vector2 direction) method that applies force to a Rigidbody2D component stored in private field _rigidbody.",
      "files": [
        "Assets/Scripts/Core/PlayerController.cs"
      ]
    },
    {
      "id": "task_002",
      "type": "modify",
      "instruction": "Open Assets/Scripts/Core/GameManager.cs. In the Start() method, find and assign the PlayerController component from the Player GameObject using GetComponent<PlayerController>(). Store in private field _playerController declared at class level.",
      "files": [
        "Assets/Scripts/Core/GameManager.cs"
      ]
    }
  ]
}
```

### Rules for tasks:
- `id`: unique string, sequential (task_001, task_002, etc.)
- `type`: one of `create`, `modify`, `delete`, `validate`
- `instruction`: SPECIFIC and ATOMIC. One clear thing. Include field names, method names, exact behaviour. Include code style rules from CODE_FORMAT_STANDARDS.md inline in the instruction.
- `files[]`: only the files Aider should touch for this task
- In recommended `micro` mode: exactly one file per task
- Add `must_exist` for create tasks
- Add `must_not_exist` for delete tasks
- Tasks must be in dependency order — never reference a file before it's created
- One concern per task — do not bundle multiple unrelated changes

---

## 9. REVIEW RESPONSE FORMAT

After each task, the bridge writes a request JSON. You respond by writing a decision JSON with exactly one of:

```json
{ "task_id": 7, "decision": "pass" }
```

or

```json
{
  "task_id": 7,
  "decision": "rework",
  "instruction": "In main.py, fix the CLI parsing so --help works without requiring the optional runtime inputs."
}
```

If creating a sub-plan for a failed task:

```json
{
  "task_id": 7,
  "decision": "subplan",
  "sub_tasks": [
    {
      "type": "modify",
      "instruction": "In main.py, repair the syntax error near the top of the file.",
      "files": ["main.py"]
    }
  ]
}
```

---

## 10. WHAT IS ALREADY BUILT IN THE BRIDGE

The bridge is **intended to be stable enough for normal external-project supervision**. For routine external work, you do not need to read bridge code.

| Feature | Status |
|---|---|
| Plan generation via supervisor | ✅ Working |
| Sequential task execution via Aider | ✅ Working |
| Git diff collection after each task | ✅ Working |
| Supervisor review (PASS / REWORK) | ✅ Working |
| Sub-plan generation on failure | ✅ Working |
| Checkpoint / resume on crash | ✅ Working |
| Pause / resume mid-run | ✅ Working |
| Progress tracking (SSE events) | ✅ Working |
| Token usage tracking + savings log | ✅ Working |
| Crash-safe stdout event emission | ✅ Working |
| Manual-review receipt recovery on rerun | ✅ Working |
| Persistent `project_knowledge.json` in target repo | ✅ Working |
| Persistent `project_snapshot.json` / `task_metrics.json` | ✅ Working |
| Failure-time artifact persistence | ✅ Working |
| Security: shell injection prevention | ✅ Working |
| Security: path traversal prevention | ✅ Working |
| Pre-flight checks (aider, git, disk) | ✅ Working |
| Subprocess timeouts (supervisor + Aider) | ✅ Working |
| Silent failure detection (pre/post file hash) | ✅ Working |
| Auto-split multi-file tasks (--auto-split-threshold) | ✅ Working |
| Web UI with live task feed | ✅ Working |

---

## 11. WORK LOG — CURRENT STATE

All work is tracked in `WORK_LOG.md`. Update it after every task.

### Format for every entry:
```
| Date       | Who        | Task ID  | Action                        | Status  |
| 2026-03-25 | 🔵 Claude  | W-01     | Reviewed diff — PASS          | ✅ Pass  |
| 2026-03-25 | 🟡 Aider   | task_001 | Created PlayerController.cs   | ✅ Done  |
| 2026-03-25 | 🔴 Bridge  | task_001 | Sub-plan triggered on timeout | ⚠️ Retry |
```

**Symbols:**
- 🔵 = Agentic AI (you, the supervisor)
- 🟡 = Aider (developer)
- 🔴 = Bridge (automated middleware action)

---

## 12. EXACT STEPS WHEN YOU OPEN THIS PROJECT

Follow this checklist in **strict order**. Do not skip steps. Do not add extra reads.

```
STEP 1 — Read this document fully.                        ← you are here

STEP 2 — Read WORK_LOG.md.
          Understand what is done and what is pending.
          Nothing else to read in the bridge project.

STEP 3 — Read bridge_progress/project_knowledge.json (if it exists).
          This file tells you what every file does, what is already built,
          and what the patterns are. Do NOT read source files to get this info.
          The knowledge file IS the project summary.

STEP 3.5 — Read bridge_progress/LATEST_REPORT.md and project_snapshot.json if they exist.
           These tell you what the last run did, what failed, and what is still pending.
           Prefer these over extra source-file reads.

STEP 4 — Ask the user TARGETED CLARIFYING QUESTIONS before planning.
          Generate 3-5 questions based on what is unclear from the goal.

          FOR A NEW PROJECT (no knowledge file):
            - What is the target platform? (mobile/PC/web/other)
            - What must NOT be changed or touched?
            - Are there performance or size constraints?
            - Any existing patterns or conventions to follow?
            - Should I create new files or modify existing ones?

          FOR AN EXISTING PROJECT (knowledge file exists):
            - How does this new feature connect to [specific existing file]?
            - Should [ExistingClass] be extended or wrapped?
            - Are any new dependencies being introduced?
            - What is the expected user-visible behaviour?

          Wait for answers. Use them to write precise, accurate tasks.
          Better questions = better tasks = fewer retries = fewer tokens.

STEP 5 — Read ONLY: the goal file the user gives you.
          Do NOT read anything else yet.

STEP 6 — Run ONE file tree command on the target repo:
          find "<repo>" -type f -name "*.cs" | head -80
          (or equivalent for the target language)
          Do NOT open any individual files.

STEP 7 — Create the JSON task plan.
          - Each task must be atomic and unambiguous
          - Include code style rules inline in instructions
          - Order by dependency (no file referenced before it exists)

STEP 8 — Run the bridge:
          python main.py "short goal headline" \
            --repo-root "..." \
            --plan-file "<repo_root>/taskJsons/plan_001_feature.json" \
            --workflow-profile micro \
            --manual-supervisor \
            --aider-model "ollama/qwen2.5-coder:14b" \
            --task-timeout 300

STEP 9 — Review each task request JSON as the bridge writes it.
          Write PASS / REWORK / SUBPLAN decision JSON. Never skip.

          If the bridge crashes after the review handoff:
          - rerun the same command first
          - keep the same plan file unless the task itself is wrong
          - the bridge can consume matching request/decision files automatically
          - an unchanged already-approved task can be resumed from the completed-review receipt

STEP 10 — Update WORK_LOG.md after every task.

STEP 11 — During and after the run, the bridge auto-updates:
           - bridge_progress/project_knowledge.json
           - bridge_progress/project_snapshot.json
           - bridge_progress/task_metrics.json
           - bridge_progress/token_log.json
           - bridge_progress/LATEST_REPORT.md
           - bridge_progress/manual_supervisor/completed/*.json
           You do not need to update them manually.
```

---

## 13. GROUND RULES (NEVER BREAK THESE)

1. **You do not write code** — ever. Your output is plans and reviews only.
2. **You do not read bridge source code** — it is already built and working.
3. **Review every task** — never approve without reading the diff.
4. **Sub-plans must be atomic** — one specific fix per sub-task.
5. **Instructions to Aider must include code style rules** — name field names, method names, exact behaviour, naming convention.
6. **Log everything** — WORK_LOG.md is the source of truth.
7. **Sequential order** — never send task N+1 until task N has PASSED review.
8. **One task, one concern** — never bundle unrelated changes in one task.
9. **Minimum file reads** — only what is strictly required by the workflow above.

---

## 14. TROUBLESHOOTING NOTES FOR SUPERVISORS

- If the model is missing, install it and rerun the same bridge command instead of creating a new plan.
- If request/decision files are left behind after an interrupted run, rerun first. Matching pairs are consumed automatically and stale mismatched pairs are archived.
- If a run spends tokens but completes no tasks, inspect `bridge_progress/token_log.json` for `wasted_tokens_total` and `waste_reason_counts`.
- If Aider output includes follow-up prompts, shrink the task. The bridge now treats that output as a task failure instead of silently drifting.

---

## 15. CURRENT ACTIVE PROJECTS

| Project | Target Folder | Goal File | Status |
|---|---|---|---|
| Color Gate Rush (Unity) | `H:\Vinayak_Project\codex-aider-first-unity-game\Color Gate Rush` | `GAME_IDEA.md` (in same folder) | ⏳ Ready to start — awaiting task plan |

---

## 16. QUICK REFERENCE CARD

```
YOU read: goal file + file tree only
  ↓
YOU output: JSON task plan
  ↓
python main.py "goal headline here" --repo-root "..." ...
  ↓
BRIDGE → AIDER (task 1)
  ↓
AIDER executes → diff returned
  ↓
YOU review diff → PASS or sub-plan
  ↓
Repeat → update WORK_LOG.md
```

---

*Last updated: 2026-03-26 | By: Claude (Agentic AI — Technical Supervisor)*
*Branch: chatbot_llm*
