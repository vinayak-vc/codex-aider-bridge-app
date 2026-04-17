# AI Understanding

Status: `pending confirmation`
Project: `codex-aider-bridge-app`
Type: `python` | Language: `Python`

## Summary

# Codex Aider Bridge App is a local orchestrator that separates **planning and review** from **code execution**. It supports both: an external supervisor CLI flow ( , , etc.) a manual supervisor flow where the active agent session reviews each task directly and the bridge waits for decision JSON fil

## Important Docs

- `README.md`: # Codex Aider Bridge App is a local orchestrator that separates **planning and review** from **code execution**. It supports both: an external supervisor CLI flow ( , , etc.) a manual supervisor flow where the active agent session reviews each task directly and the bridge waits for decision JSON files instead of calling another AI process For the most accurate low-token workflow, use: a pre-written That profile is designed for: Codex supervises, Aider implements, bridge validates. It ships with a **web UI** you can launch by double-clicking , and a CLI you can drive directly from the terminal.
- `.aider.chat.history.md`: # aider chat started at 2026-03-25 14:07:14 Can't initialize prompt toolkit: No Windows console found. You can skip this check with --no-gitignore Add .aider* to .gitignore (recommended)? (Y)es/(N)o [Yes]: y Added .aider* to .gitignore C:\Python311\Scripts\aider.EXE --yes-always --no-pretty --no-stream --no-auto-commits --message Create a file named dummy_file.txt containing the word 'Hello' --file H:\Vinayak_Project\codex-aider-bridge-app\dummy_file.txt Warning: ollama/qwen2.5-coder:7b expects these environment variables OLLAMA_API_BASE: Not set Note: You may need to restart your terminal or command prompt for to take effect.
- `AGENTIC_AI_ONBOARDING.md`: # AGENTIC AI ONBOARDING DOCUMENT ## Codex-Aider Bridge — Project Brief Read this document fully before doing anything. After reading, you will know exactly what this project is, how it works, your role, and what to do next — all in one prompt. The **Codex-Aider Bridge** is a local Flask web application that separates **AI planning/review** (expensive cloud AI like you) from **code execution** (cheap local Ollama LLM via Aider).
- `AGENT_CONTEXT.md`: # AGENT_CONTEXT ## Architecture Summary The bridge is a local CLI orchestrator with a strict two-role separation: The loop is strictly sequential and acknowledgement-gated: the supervisor must approve each task before the next one starts. A **web UI** ( ) provides a browser-based front end over the same bridge, enabling setup detection, live task progress, and run history without any terminal interaction. --- ## Module Responsibilities ### Core bridge (CLI) Orchestrates repo scanning, plan acquisition, the sequential task-review loop, logging, git-readiness pre-flight, per-task auto-commit, and CLI argument handling.
- `AI_SUPERVISOR_PROMPT.md`: # AI Supervisor Reference This document explains how an agentic AI should behave when supervising Aider through this bridge. The recommended operating mode is no longer “spawn another AI CLI and hope it plans well”. The recommended mode is: the active AI session creates the task plan the bridge runs Aider the bridge validates and records results the active AI session reviews each task the bridge resumes from the AI’s decision the bridge keeps a structured project memory under --- ## The Supervisor Role The supervisor is the **technical lead**.

## Key Files

- `AGENTIC_AI_ONBOARDING.md`: > Read this document fully before doing anything.
- `AGENT_CONTEXT.md`: The bridge is a local CLI orchestrator with a strict two-role separation:
- `AI Relay Supervisor.md`: A new supervisor mode called **"AI Relay"** where you use any web-based AI (ChatGPT Plus, Claude.ai Pro, Gemini, Grok — anything) as the brain via copy-paste, with no API key. The bridge formats every
- `AI_RELAY_PLAN.md`: > **Status:** PENDING — ready to implement
- `AI_RELAY_SPEC.md`: > **Status:** PLANNED — implement after Chat feature is complete
- `AI_SUPERVISOR_PROMPT.md`: This document explains how an agentic AI should behave when supervising Aider through this bridge.
- `CHANGELOG.md`: - **Universal Pipeline**: all supervisor types now use `--manual-supervisor` under the hood; the UI backend dispatches review decisions via a `SupervisorProxyThread`
- `HOW_TO.md`: This bridge is designed for a three-part workflow:
- `HOW_TO_TALK_TO_SUPERVISOR.md`: This document tells you exactly how to communicate with the Agentic AI (Supervisor)
- `MISSING_FEATURES_AND_IMPROVEMENTS.md`: > Audit Date: 2026-03-25 | Branch: chatbot_llm | Done by: 🔵 Claude (Technical Supervisor)
- `README.md`: `codex-aider-bridge-app` is a local orchestrator that separates **planning and review** from **code execution**.
- `TASK_PLAN.json`: {

## Architecture Signals

- Framework: Flask
- Framework: unittest
- Multiple classes inherit Exception (3 found)
- Code-review-graph nodes=1044, edges=9367, files=84
- Code-review-graph flows=262, communities=71
- CRG community: ui-api (size=103, cohesion=0.12)
- CRG community: ui-load (size=47, cohesion=0.45)
- CRG community: utils-firestore (size=42, cohesion=0.31)

## Context Text

This is the compact context summary that can be reused in later bridge sessions.

```text
PROJECT: codex-aider-bridge-app (python/Python)
(roles inferred by static scan - not task-authored)
SUMMARY: # Codex Aider Bridge App is a local orchestrator that separates **planning and review** from **code execution**. It supports both: an external supervisor CLI flow ( , , etc.) a manual supervisor flow where the active agent session reviews each task directly and the bridge waits for decision JSON fil

DOCUMENTATION SIGNALS:
  README.md
    -> # Codex Aider Bridge App is a local orchestrator that separates **planning and review** from **code execution**. It supports both: an external supervisor CLI flow ( , , etc.) a manual supervisor flow where the active agent session reviews each task directly and the bridge waits for decision JSON files instead of calling another AI process For the most accurate low-token workflow, use: a pre-written That profile is designed for: Codex supervises, Aider implements, bridge validates. It ships with a **web UI** you can launch by double-clicking , and a CLI you can drive directly from the terminal.
  .aider.chat.history.md
    -> # aider chat started at 2026-03-25 14:07:14 Can't initialize prompt toolkit: No Windows console found. You can skip this check with --no-gitignore Add .aider* to .gitignore (recommended)? (Y)es/(N)o [Yes]: y Added .aider* to .gitignore C:\Python311\Scripts\aider.EXE --yes-always --no-pretty --no-stream --no-auto-commits --message Create a file named dummy_file.txt containing the word 'Hello' --file H:\Vinayak_Project\codex-aider-bridge-app\dummy_file.txt Warning: ollama/qwen2.5-coder:7b expects these environment variables OLLAMA_API_BASE: Not set Note: You may need to restart your terminal or command prompt for to take effect.
  AGENTIC_AI_ONBOARDING.md
    -> # AGENTIC AI ONBOARDING DOCUMENT ## Codex-Aider Bridge — Project Brief Read this document fully before doing anything. After reading, you will know exactly what this project is, how it works, your role, and what to do next — all in one prompt. The **Codex-Aider Bridge** is a local Flask web application that separates **AI planning/review** (expensive cloud AI like you) from **code execution** (cheap local Ollama LLM via Aider).
  AGENT_CONTEXT.md
    -> # AGENT_CONTEXT ## Architecture Summary The bridge is a local CLI orchestrator with a strict two-role separation: The loop is strictly sequential and acknowledgement-gated: the supervisor must approve each task before the next one starts. A **web UI** ( ) provides a browser-based front end over the same bridge, enabling setup detection, live task progress, and run history without any terminal interaction. --- ## Module Responsibilities ### Core bridge (CLI) Orchestrates repo scanning, plan acquisition, the sequential task-review loop, logging, git-readiness pre-flight, per-task auto-commit, and CLI argument handling.
  AI_SUPERVISOR_PROMPT.md
    -> # AI Supervisor Reference This document explains how an agentic AI should behave when supervising Aider through this bridge. The recommended operating mode is no longer “spawn another AI CLI and hope it plans well”. The recommended mode is: the active AI session creates the task plan the bridge runs Aider the bridge validates and records results the active AI session reviews each task the bridge resumes from the AI’s decision the bridge keeps a structured project memory under --- ## The Supervisor Role The supervisor is the **technical lead**.

FILE REGISTRY (what each file does):
  AGENTIC_AI_ONBOARDING.md
    -> > Read this document fully before doing anything.
  AGENT_CONTEXT.md
    -> The bridge is a local CLI orchestrator with a strict two-role separation:
  AI Relay Supervisor.md
    -> A new supervisor mode called **"AI Relay"** where you use any web-based AI (ChatGPT Plus, Claude.ai Pro, Gemini, Grok — anything) as the brain via copy-paste, with no API key. The bridge formats every
  AI_RELAY_PLAN.md
    -> > **Status:** PENDING — ready to implement
  AI_RELAY_SPEC.md
    -> > **Status:** PLANNED — implement after Chat feature is complete
  AI_SUPERVISOR_PROMPT.md
    -> This document explains how an agentic AI should behave when supervising Aider through this bridge.
  CHANGELOG.md
    -> - **Universal Pipeline**: all supervisor types now use `--manual-supervisor` under the hood; the UI backend dispatches review decisions via a `SupervisorProxyThread`
  HOW_TO.md
    -> This bridge is designed for a three-part workflow:
  HOW_TO_TALK_TO_SUPERVISOR.md
    -> This document tells you exactly how to communicate with the Agentic AI (Supervisor)
  MISSING_FEATURES_AND_IMPROVEMENTS.md
    -> > Audit Date: 2026-03-25 | Branch: chatbot_llm | Done by: 🔵 Claude (Technical Supervisor)
  README.md
    -> `codex-aider-bridge-app` is a local orchestrator that separates **planning and review** from **code execution**.
  TASK_PLAN.json
    -> {
  TASK_PLAN_2.json
    -> {
  Universal Pipeline.md
    -> The bridge has two roles, played by the same entity (the supervisor):
  WHY_THIS_REPO.md
    -> > Written by: 🔵 Claude (Technical Supervisor) | 2026-03-25
  WORK_LOG.md
    -> > Branch: `chatbot_llm`
  bridge_logging/__init__.py
    -> Logging helpers for the bridge app
  bridge_logging/logger.py
    -> exposes configure_logging
  build.bat
    -> @echo off
  context/__init__.py
    -> Context package
  context/file_selector.py
    -> defines FileSelector; exposes select
  context/idea_loader.py
    -> defines IdeaLoader; exposes load
  context/project_understanding.py
    -> defines ProjectDoc; exposes ensure_project_understanding, understanding_file_path
  context/repo_scanner.py
    -> defines RepoScanner; exposes scan
  example plan.json
    -> {
  executor/__init__.py
    -> Executor package
  executor/aider_runner.py
    -> defines AiderRunner; exposes run
  executor/diff_collector.py
    -> defines DiffCollector; exposes collect
  features.md
    -> This document lists the currently implemented features of `codex-aider-bridge-app` based on the code in this repository.
  launch_ui.bat
    -> @echo off
  launch_ui.py
    -> launch_ui
  main.py
    -> exposes build_argument_parser, load_plan_from_file, auto_split_tasks, obtain_plan, show_plan_preview
  memory/memory_client.py
    -> Import os, logging, urllib.request, urllib.error, json.
  models/__init__.py
    -> Typed models used across the bridge app
  models/task.py
    -> defines Task, AiderContext, SubTask, SelectedFiles; exposes id
  parser/__init__.py
    -> Parser package
  parser/task_parser.py
    -> defines PlanParseError, TaskParser; exposes parse
  planner/__init__.py
    -> Planner package
  planner/codex_client.py
    -> no description
  planner/fallback_planner.py
    -> defines FallbackPlanner; exposes build_plan
  requirements.txt
    -> no description
  requirements_ui.txt
    -> flask>=3.0
  supervisor/__init__.py
    -> Supervisor package — Tech Supervisor agent for planning and review
  supervisor/agent.py
    -> defines SupervisorError, SupervisorAgent; exposes generate_plan, generate_subplan, review_task
  tests/test_bridge_resilience.py
    -> defines BridgeResilienceTests; exposes test_safe_stdout_write_swallows_os_error, test_find_unexpected_files_ignores_python_runtime_artifacts, test_manual_supervisor_resumes_completed_task_when_files_m
  ui/__init__.py
    -> Bridge UI package — Flask web interface for the Codex Aider Bridge App
  ui/app.py
    -> defines _ChatRuntime, SupervisorProxyThread; exposes index, page_dashboard, page_run, page_git, page_knowledge
  ui/bridge_runner.py
    -> defines BridgeRun; exposes get_run, add_listener, remove_listener, build_command, start
  ui/data/history.json
    -> [
  ui/data/projects.json
    -> [
  ui/data/settings.json
    -> {
  ui/setup_checker.py
    -> exposes check_all, check_python, check_aider, check_ollama, check_codex
  ui/state_store.py
    -> exposes load_settings, save_settings, load_history, add_history_entry, update_history_entry
  ui/static/css/base.css
    -> html {
  ui/static/css/chat-drawer.css
    -> .chat-drawer-toggle {
  ui/static/css/components.css
    -> .btn {
  ui/static/css/pages/chat.css
    -> .chat-shell {
  ui/static/css/pages/dashboard.css
    -> .dashboard-stats {
  ui/static/css/pages/git.css
    -> .git-page {
  ui/static/css/pages/history.css
    -> .history-toolbar {
  ui/static/js/core/api.js
    -> Fetch JSON from the bridge API.
  ui/static/js/core/chat-drawer.js
    -> no description
  ui/static/js/core/event-notifier.js
    -> exports initEventNotifications
  ui/static/js/pages/chat.js
    -> no description
  ui/static/js/pages/dashboard.js
    -> no description
  ui/static/js/pages/git.js
    -> no description
  ui/static/js/pages/history.js
    -> no description
  ui/templates/base.html
    -> <!DOCTYPE html>
  ui/templates/chat.html
    -> {% extends "base.html" %}
  ui/templates/dashboard.html
    -> {% extends "base.html" %}
  ui/templates/git.html
    -> {% extends "base.html" %}
  ui/templates/history.html
    -> {% extends "base.html" %}
  ui/templates/index.html
    -> <!DOCTYPE html>
  ui/templates/knowledge.html
    -> {% extends "base.html" %}
  ui/templates/run.html
    -> {% extends "base.html" %}
  ui/templates/setup.html
    -> {% extends "base.html" %}
  ui/templates/tokens.html
    -> {% extends "base.html" %}
  utils/__init__.py
    -> no description
  utils/checkpoint.py
    -> Lightweight checkpoint: persist completed task IDs so a failed run can resume
  utils/command_resolution.py
    -> exposes split_command, resolve_command_arguments, resolve_executable, iter_local_script_directories, build_missing_executable_message
  utils/manual_supervisor.py
    -> defines ManualSupervisorError, ManualSupervisorSession; exposes submit_review_request, consume_existing_decision, record_completed_review, try_resume_completed_task, wait_for_decision
  utils/onboarding_scanner.py
    -> Onboarding scanner — one-time source analysis for existing projects
  utils/project_knowledge.py
    -> Project knowledge cache — stores what every file does so any AI can
understand the project architecture by reading one JSON file
  utils/project_type_prompt.py
    -> Interactive project-type selection prompt shown at first bridge run
  utils/relay_formatter.py
    -> relay_formatter
  utils/report_generator.py
    -> Generate a Markdown run report from a session token report
  utils/run_diagnostics.py
    -> Run Diagnostics — structured failure analysis for AI supervisors
  utils/token_tracker.py
    -> Token usage tracker for the bridge
  validator/__init__.py
    -> Validator package
  validator/validator.py
    -> defines _ProjectType, MechanicalValidator; exposes is_unity_project, validate

CODE PATTERNS:
  -Framework: Flask
  -Framework: unittest
  -Multiple classes inherit Exception (3 found)
  -Code-review-graph nodes=1044, edges=9367, files=84
  -Code-review-graph flows=262, communities=71
  -CRG community: ui-api (size=103, cohesion=0.12)
  -CRG community: ui-load (size=47, cohesion=0.45)
  -CRG community: utils-firestore (size=42, cohesion=0.31)
  -CRG community: pages-render (size=41, cohesion=0.51)
  -CRG community: pages-handle (size=32, cohesion=0.29)

ALREADY IMPLEMENTED: memory_client, main, README

LAST RUN: 2026-04-16 | 1 tasks | "test"
```
