# HOW TO TALK TO THE SUPERVISOR AI

This document tells you exactly how to communicate with the Agentic AI (Supervisor)
to get maximum bridge and Aider usage — minimum supervisor tokens, maximum Aider coding.

---

## The Core Rule

> **Give paths + goal + "run via bridge" = Supervisor plans, Aider codes, you review diffs.**

Anything vague = the Supervisor discusses instead of executes. Be directive, not conversational.

---

## 1. STARTING A NEW PROJECT

Use this template exactly. Fill in your values.

```
Project Dir:       <absolute path to the repo>
Goal File:         <absolute path to GAME_IDEA.md / PRODUCT_BRIEF.md / etc.>
Code Standards:    <absolute path to CODE_FORMAT_STANDARDS.md>

Aider model:       ollama/qwen2.5-coder:7b
Supervisor:        claude
Bridge run via:    main.py

Read the goal file and the file tree — then create the task plan and run the bridge.
All code goes through Aider. You do not write any code directly.
Track token usage throughout.
```

### What happens next (automatically):
1. Supervisor reads goal file + one file tree command — nothing else
2. Supervisor creates a detailed atomic JSON task plan
3. Bridge runs: `python main.py --goal "..." --repo-root "..." ...`
4. Aider executes each task, one at a time
5. Supervisor reviews each diff — PASS or sub-plan
6. WORK_LOG.md is updated after every task
7. Token usage tracked and saved to `token_log.json`

---

## 2. ADDING A FEATURE MID-PROJECT

```
Project Dir:  <absolute path to the repo>

New feature: <describe the feature clearly — what it does, where it appears, key behaviour>
Follow CODE_FORMAT_STANDARDS.md.

Run via bridge. All code through Aider.
```

You do not need to re-explain the whole project. The Supervisor reads `WORK_LOG.md`
to know the current state and continues from there.

### Example:
```
Project Dir:  H:\Vinayak_Project\codex-aider-first-unity-game\Color Gate Rush

New feature: Ad monetization — rewarded ads shown on game over screen,
banner ad on main menu. Use Unity Ads SDK. Rewarded ad grants the player
one extra life. Banner is non-intrusive, bottom of screen.
Follow CODE_FORMAT_STANDARDS.md.

Run via bridge. All code through Aider.
```

---

## 3. FIXING A BUG

```
Project Dir:  <absolute path to the repo>

Bug: <describe exactly what is wrong — what you see, what you expected>
Affected file (if known): <path>

Run via bridge. All code through Aider.
```

### Example:
```
Project Dir:  H:\Vinayak_Project\codex-aider-first-unity-game\Color Gate Rush

Bug: Player passes through gates when moving at high speed. Expected: collision
always detected. Affected file: Assets/Scripts/Gameplay/GateCollision.cs

Run via bridge. All code through Aider.
```

---

## 4. RESUMING AFTER A CRASH OR PAUSE

```
Project Dir:  <absolute path to the repo>

Resume the last run. Pick up from where it stopped.
Run via bridge. All code through Aider.
```

The bridge has checkpoint support — it automatically skips already-completed tasks
and continues from the next pending one.

---

## 5. CHECKING STATUS / PROGRESS

```
Project Dir:  <absolute path to the repo>

What is the current status? What has been done and what is pending?
Do not run the bridge yet — just report.
```

The Supervisor reads `WORK_LOG.md` and gives you a summary without starting any execution.

---

## 6. SEEING TOKEN SAVINGS

```
Show me the token usage report for the last run.
```

The Supervisor reads `token_log.json` and shows:
- Tokens used by Supervisor (plan + review)
- Estimated tokens that WOULD have been used if Supervisor wrote all code directly
- Tokens saved by using Aider
- Savings percentage

---

## WHAT NOT TO SAY

| Avoid | Why | Say instead |
|-------|-----|-------------|
| "Can you help me with X?" | Triggers a chat answer, not a bridge run | "New feature: X. Run via bridge." |
| "What do you think about Y?" | Triggers discussion, not execution | Just give the goal |
| "Implement X" (no project dir) | Supervisor will ask clarifying questions | Always include Project Dir |
| "Do it yourself if Aider can't" | Breaks role separation — Aider always codes | Never say this |
| "Explain how X works" | Triggers file reading and explanation | Not useful during a run |
| "Fix everything" | Too vague — no atomic plan possible | Describe one specific thing |

---

## QUICK REFERENCE — COMMAND TEMPLATES

### New project
```
Project Dir:    <path>
Goal File:      <path>
Code Standards: <path>
Aider model:    ollama/qwen2.5-coder:7b
Supervisor:     claude
Bridge run via: main.py

Read the goal file and file tree. Create the task plan. Run the bridge.
All code through Aider. Track token usage.
```

### New feature
```
Project Dir: <path>
New feature: <description>
Follow CODE_FORMAT_STANDARDS.md.
Run via bridge. All code through Aider.
```

### Bug fix
```
Project Dir: <path>
Bug: <what is wrong, what is expected>
Affected file: <path if known>
Run via bridge. All code through Aider.
```

### Resume
```
Project Dir: <path>
Resume the last run. Run via bridge. All code through Aider.
```

### Status check
```
Project Dir: <path>
What is done and what is pending? Do not run the bridge yet.
```

---

## WHY THIS PROTOCOL SAVES TOKENS

Without the bridge, the Supervisor AI writes all code directly:
- Plan: ~1,000 tokens
- Writing 20 scripts × ~5,000 tokens each = 100,000 tokens
- **Total: ~101,000 tokens (all charged)**

With the bridge:
- Plan: ~1,000 tokens
- Review 20 diffs × ~300 tokens each = ~6,000 tokens
- **Total: ~7,000 tokens — Aider wrote everything for free (local GPU)**
- **Savings: ~85-93%**

The more precise your prompts, the less the Supervisor asks clarifying questions,
and the more tokens go toward actual planning and review rather than conversation.

---

*Last updated: 2026-03-26 | Codex-Aider Bridge Project*
*Branch: chatbot_llm*
