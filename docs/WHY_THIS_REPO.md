# Why This Repo Matters — Codex-Aider Bridge App
> Written by: 🔵 Claude (Technical Supervisor) | 2026-03-25

---

## The Problem It Solves

Every developer using AI coding tools today faces the same two problems:

### Problem 1 — AI is Expensive at Scale
When you ask Claude or GPT to write an entire feature, it writes every line of code itself.
That means you pay API tokens for every character it generates.
A 500-line Unity script costs ~8,000 tokens. A 30-feature game? **~240,000 tokens. Just for code typing.**

### Problem 2 — AI Doesn't Check Its Own Work
When AI writes code and you accept it, there's no second opinion.
Bugs get committed. Broken imports get merged. Silent failures pile up.
You only find out something is wrong when the build fails — 10 tasks later.

**This bridge solves both problems simultaneously.**

---

## What This Bridge Actually Does

```
You describe what you want to build.

  Claude (Supervisor) — thinks, plans, reviews     → costs almost nothing
         ↕ (bridge passes tasks one at a time)
  Aider  (Developer)  — writes all the actual code → costs nothing (local GPU)
```

The bridge is the protocol between them.

---

## Why This Architecture is Genuinely Novel

### 1. Role Separation by Design

Most "AI coding" tools are one agent doing everything — planning, writing, reviewing, deciding.
This bridge **enforces a strict separation of concerns**:

| Role | Agent | Does | Costs |
|---|---|---|---|
| Technical Supervisor | Claude / Codex | Plans + Reviews only | ~1,100 tokens/task |
| Developer | Aider (local LLM) | Writes all code | $0 — local GPU |
| Bridge | This app | Passes tasks, collects diffs | $0 |

The Supervisor **never writes a single line of code.**
The Developer **never makes a single decision.**

This mirrors how real senior engineering teams work:
> A senior architect reviews blueprints. A developer builds. They don't swap roles.

---

### 2. Token Cost Reduction of ~85%

| Approach | Tokens per script | Who pays |
|---|---|---|
| Claude writes everything | ~8,000 | You (API cost) |
| Claude supervises + Aider writes | ~1,100 | ~300 (plan) + ~800 (review) |

**Savings: ~85% per task.**

For a 29-task game project:
- Without bridge: ~232,000 Claude tokens
- With bridge: ~31,900 Claude tokens

**You pay for thinking. Aider handles typing — for free.**

---

### 3. Quality Gates After Every Single Task

The bridge doesn't just blindly accept what Aider produces.
After every task, it runs a **3-stage quality check**:

```
Stage 1 — Mechanical Check (instant, free)
  → Does the file exist?
  → Is the Python syntax valid?
  → Does the CI command pass?

Stage 2 — Supervisor Review (cheap, ~800 tokens)
  → Claude reads the actual git diff
  → Checks: did Aider do what was asked?
  → Returns: PASS or REWORK: <specific correction>

Stage 3 — Retry Loop
  → On REWORK: new instruction sent to Aider
  → Max retries configurable
  → Never moves to Task N+1 until Task N is verified
```

**Result: No broken code silently reaches the next task.**

---

### 4. Works With Any AI Supervisor

The bridge doesn't lock you into one AI.
The supervisor slot accepts:

- Claude (Anthropic)
- Codex (OpenAI)
- Any CLI-based LLM that takes a prompt and returns text

Switch supervisors by changing one config line. Your plan format stays the same.

---

### 5. Works With Local Models via Aider

Aider supports Ollama, LM Studio, and any OpenAI-compatible local endpoint.
This means:

- **No per-token cost for code generation**
- **Works fully offline** (after model download)
- **Your code never leaves your machine** (for the developer step)

Only the Supervisor (review + plan) hits the cloud.
The Developer runs on your GPU.

---

### 6. Fully Auditable — Every Decision is Logged

Every task execution produces:
- The instruction given to Aider
- The exact git diff of what Aider changed
- The supervisor's verdict (PASS or REWORK + reason)
- Number of retries
- Timestamps

You can inspect exactly **why** every line of code exists.
There are no black-box changes.

---

### 7. The Bridge is Language-Agnostic

The plan format is plain JSON. The tasks are plain English instructions.
Today it's used for a Unity (C#) game.
Tomorrow it could be:

- A React frontend
- A Python backend
- A Go microservice
- A Rust systems tool

The bridge doesn't care. Aider handles the language. Claude reviews the diff.

---

## Real-World Use Case: Building a Unity Game

The game **"Hold & Release — Orbit Escape"** is being built entirely through this bridge:

- 29 tasks planned by Claude
- Each task implemented by Aider (local LLM, free)
- Each task reviewed by Claude (diff only, ~800 tokens)
- Total Claude cost for 29 tasks: ~31,900 tokens
- Total Aider cost: $0

Without this bridge, building the same game with Claude writing all scripts directly would cost ~232,000 tokens.

**The bridge saves ~86% in AI cost while adding quality gates that didn't exist before.**

---

## Who This Is For

| User | Why This Helps |
|---|---|
| **Solo developers** | Get senior-engineer-quality review on every AI-generated change, at low cost |
| **Startups** | Build faster without burning API budget on boilerplate code generation |
| **AI researchers** | Study how supervisor-developer agent separation affects code quality |
| **Game developers** | Generate large Unity projects from a design doc with verified output at each step |
| **Anyone using Aider** | Add a free quality gate on top of Aider's output without changing your workflow |

---

## What Makes It Different from Other AI Coding Tools

| Feature | This Bridge | GitHub Copilot | Cursor | Raw Claude/GPT |
|---|---|---|---|---|
| Quality gate after each change | ✅ | ❌ | ❌ | ❌ |
| Local LLM for code generation | ✅ | ❌ | ❌ | ❌ |
| Supervisor ≠ Developer (role separation) | ✅ | ❌ | ❌ | ❌ |
| Sequential plan with dependencies | ✅ | ❌ | ❌ | ❌ |
| Full audit log per task | ✅ | ❌ | ❌ | ❌ |
| Model-agnostic supervisor | ✅ | ❌ | ❌ | ✅ |
| Works offline (developer step) | ✅ | ❌ | ❌ | ❌ |
| Token cost ~85% lower than AI-only | ✅ | N/A | N/A | ❌ |

---

## The Bigger Vision

This bridge is a prototype of something larger:

> **AI development teams where different models have different roles, accountabilities, and costs.**

Today: one supervisor + one developer.
Tomorrow:
- Supervisor (Claude) — planning and review
- Developer (Aider/Ollama) — implementation
- QA Agent — automated testing
- Security Agent — vulnerability scanning
- DevOps Agent — CI/CD management

Each agent does one job. Each agent is replaceable.
The bridge coordinates them.

**This is not just a tool. It's an architecture for how AI-assisted engineering will work.**

---

## Summary

| What | Impact |
|---|---|
| Reduces Claude token cost by ~85% | Build more for less |
| Quality gate after every task | No silent broken code |
| Local LLM for all code generation | Zero marginal cost for code writing |
| Full audit trail | Complete transparency |
| Model-agnostic | Not locked in to one AI provider |
| Mirrors real engineering teams | Supervisor + Developer separation |
| Language-agnostic | Works for any tech stack |

---

*This repo is not a chatbot wrapper. It is a structured AI engineering workflow.*
*The bridge is what turns two AI models into a functioning development team.*
