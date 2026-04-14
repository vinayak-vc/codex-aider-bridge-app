# Graph Report - C:\Users\winss\Documents\Projects\codex-aider-bridge-app  (2026-04-13)

## Corpus Check
- 110 files · ~118,025 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1263 nodes · 2450 edges · 76 communities detected
- Extraction: 79% EXTRACTED · 21% INFERRED · 0% AMBIGUOUS · INFERRED: 523 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Supervisor Workflow Core|Supervisor Workflow Core]]
- [[_COMMUNITY_Task Planning and Aider|Task Planning and Aider]]
- [[_COMMUNITY_App State Persistence|App State Persistence]]
- [[_COMMUNITY_Firebase Sync Service|Firebase Sync Service]]
- [[_COMMUNITY_Firebase API Routes|Firebase API Routes]]
- [[_COMMUNITY_Project Docs and Concepts|Project Docs and Concepts]]
- [[_COMMUNITY_Deterministic Execution Pipeline|Deterministic Execution Pipeline]]
- [[_COMMUNITY_Telemetry Reporting|Telemetry Reporting]]
- [[_COMMUNITY_Relay API Workflow|Relay API Workflow]]
- [[_COMMUNITY_Manual Supervisor Session|Manual Supervisor Session]]
- [[_COMMUNITY_Run Diagnostics|Run Diagnostics]]
- [[_COMMUNITY_Mechanical Validation|Mechanical Validation]]
- [[_COMMUNITY_Dashboard Frontend Core|Dashboard Frontend Core]]
- [[_COMMUNITY_Knowledge View Frontend|Knowledge View Frontend]]
- [[_COMMUNITY_Run Page Frontend|Run Page Frontend]]
- [[_COMMUNITY_Onboarding Scanner|Onboarding Scanner]]
- [[_COMMUNITY_Main Orchestration Loop|Main Orchestration Loop]]
- [[_COMMUNITY_Bridge Runner Service|Bridge Runner Service]]
- [[_COMMUNITY_Chat API Routes|Chat API Routes]]
- [[_COMMUNITY_Monitor Frontend|Monitor Frontend]]
- [[_COMMUNITY_Git Frontend|Git Frontend]]
- [[_COMMUNITY_Project Switcher UI|Project Switcher UI]]
- [[_COMMUNITY_Environment Preflight Checks|Environment Preflight Checks]]
- [[_COMMUNITY_Project Knowledge Cache|Project Knowledge Cache]]
- [[_COMMUNITY_Project Understanding Builder|Project Understanding Builder]]
- [[_COMMUNITY_Chat Drawer UI|Chat Drawer UI]]
- [[_COMMUNITY_History Page UI|History Page UI]]
- [[_COMMUNITY_Relay Formatter Utilities|Relay Formatter Utilities]]
- [[_COMMUNITY_Git Routes API|Git Routes API]]
- [[_COMMUNITY_System Routes API|System Routes API]]
- [[_COMMUNITY_Setup Page UI|Setup Page UI]]
- [[_COMMUNITY_Model Advisor|Model Advisor]]
- [[_COMMUNITY_Action Log Utility|Action Log Utility]]
- [[_COMMUNITY_Git Manager|Git Manager]]
- [[_COMMUNITY_Shared App State|Shared App State]]
- [[_COMMUNITY_Cloud Page UI|Cloud Page UI]]
- [[_COMMUNITY_Checkpoint Persistence|Checkpoint Persistence]]
- [[_COMMUNITY_Firebase Auth Frontend|Firebase Auth Frontend]]
- [[_COMMUNITY_Keyboard Shortcuts|Keyboard Shortcuts]]
- [[_COMMUNITY_Sound Effects|Sound Effects]]
- [[_COMMUNITY_Desktop Launcher|Desktop Launcher]]
- [[_COMMUNITY_Failure Feedback|Failure Feedback]]
- [[_COMMUNITY_SSE Client|SSE Client]]
- [[_COMMUNITY_Deep Scanner|Deep Scanner]]
- [[_COMMUNITY_Project Type Prompt|Project Type Prompt]]
- [[_COMMUNITY_Help Modal UI|Help Modal UI]]
- [[_COMMUNITY_Wallpaper Manager|Wallpaper Manager]]
- [[_COMMUNITY_Command Resolution|Command Resolution]]
- [[_COMMUNITY_Unity Checks|Unity Checks]]
- [[_COMMUNITY_Memory Client|Memory Client]]
- [[_COMMUNITY_Run Report Generator|Run Report Generator]]
- [[_COMMUNITY_API Fetch Helpers|API Fetch Helpers]]
- [[_COMMUNITY_Markdown Renderer|Markdown Renderer]]
- [[_COMMUNITY_Toast Notifications|Toast Notifications]]
- [[_COMMUNITY_Version Tracking|Version Tracking]]
- [[_COMMUNITY_Fallback Planner|Fallback Planner]]
- [[_COMMUNITY_Event Notifier|Event Notifier]]
- [[_COMMUNITY_Theme Toggle|Theme Toggle]]
- [[_COMMUNITY_Logging Configuration|Logging Configuration]]
- [[_COMMUNITY_Aider Config Constants|Aider Config Constants]]
- [[_COMMUNITY_Feature Manifest Loader|Feature Manifest Loader]]
- [[_COMMUNITY_Context Package Init|Context Package Init]]
- [[_COMMUNITY_Task IR Validation|Task IR Validation]]
- [[_COMMUNITY_Executor Package Init|Executor Package Init]]
- [[_COMMUNITY_Firebase Functions Index|Firebase Functions Index]]
- [[_COMMUNITY_Memory Package Init|Memory Package Init]]
- [[_COMMUNITY_Synthetic Task IDs|Synthetic Task IDs]]
- [[_COMMUNITY_Parser Package Init|Parser Package Init]]
- [[_COMMUNITY_Codex Client|Codex Client]]
- [[_COMMUNITY_Planner Package Init|Planner Package Init]]
- [[_COMMUNITY_Planning Package Init|Planning Package Init]]
- [[_COMMUNITY_UI API Package Init|UI API Package Init]]
- [[_COMMUNITY_Store Core|Store Core]]
- [[_COMMUNITY_Utils Package Init|Utils Package Init]]
- [[_COMMUNITY_Validator Package Init|Validator Package Init]]
- [[_COMMUNITY_Bridge Favicon Icon|Bridge Favicon Icon]]

## God Nodes (most connected - your core abstractions)
1. `SupervisorAgent` - 85 edges
2. `RepoScanner` - 74 edges
3. `Task` - 57 edges
4. `OnboardingScanner` - 57 edges
5. `FirebaseSync` - 36 edges
6. `AiderRunner` - 32 edges
7. `ExecutionResult` - 30 edges
8. `ManualSupervisorSession` - 30 edges
9. `AiderContext` - 26 edges
10. `MechanicalValidator` - 26 edges

## Surprising Connections (you probably didn't know these)
- `Relay API blueprint — AI relay prompt generation, plan import, review.  Extrac` --uses--> `RepoScanner`  [INFERRED]
  ui\api\relay_routes.py → context\repo_scanner.py
- `Strip all non-alphanumeric chars and lowercase for lenient matching.` --uses--> `RepoScanner`  [INFERRED]
  ui\api\relay_routes.py → context\repo_scanner.py
- `Return the plan prompt the user pastes into their web AI.` --uses--> `RepoScanner`  [INFERRED]
  ui\api\relay_routes.py → context\repo_scanner.py
- `Parse the web AI's plan response and persist the task list.` --uses--> `RepoScanner`  [INFERRED]
  ui\api\relay_routes.py → context\repo_scanner.py
- `Build the review text for a completed task.` --uses--> `RepoScanner`  [INFERRED]
  ui\api\relay_routes.py → context\repo_scanner.py

## Hyperedges (group relationships)
- **Bridge Team Roles** — technical_supervisor, bridge_orchestrator, aider_developer [EXTRACTED 1.00]
- **Project Memory Bundle** — bridge_progress_artifacts, project_knowledge_cache, task_metrics, token_log, latest_report, run_report [EXTRACTED 1.00]
- **Firebase Privacy Stack** — firebase_cloud_sync, per_user_firebase_architecture, privacy_preserving_sync, firestore_security_rules [EXTRACTED 1.00]

## Communities

### Community 0 - "Supervisor Workflow Core"
Cohesion: 0.02
Nodes (110): _build_feature_specs_block(), _build_model_roster_block(), SupervisorAgent, api_add_history_entry(), api_add_token_session(), api_current_review(), api_get_history(), api_get_tokens() (+102 more)

### Community 1 - "Task Planning and Aider"
Cohesion: 0.05
Nodes (83): Ask the supervisor to review a completed task and return PASS or REWORK., Build the FEATURE SPECIFICATIONS prompt block., Build the AVAILABLE MODELS prompt block for smart routing., Tech Supervisor agent — plans work and reviews Aider output.      This agent h, Parse supervisor sub-plan JSON into a list of SubTask objects., Build the supervisor subprocess arguments and determine prompt delivery mode., Ask the supervisor to produce a JSON atomic task plan., Ask the supervisor for micro-tasks to fix a mechanical validation failure. (+75 more)

### Community 2 - "App State Persistence"
Cohesion: 0.06
Nodes (63): add_history_entry(), add_project(), append_run_queue(), clear_chat_history(), clear_history(), clear_relay_tasks(), clear_relay_ui_state(), clear_run_nl_state() (+55 more)

### Community 3 - "Firebase Sync Service"
Cohesion: 0.07
Nodes (22): AuthError, FirebaseSync, get_firebase_sync(), Firebase Cloud Sync — authentication and data push to user's Firestore.  Uses, Start Google OAuth2 login flow. Opens browser for consent., Exchange OAuth authorization code for Firebase ID token., Return a valid ID token, refreshing if expired., Exchange refresh token for a new ID token. (+14 more)

### Community 4 - "Firebase API Routes"
Cohesion: 0.05
Nodes (24): api_sync_delete_account(), api_sync_export(), api_sync_push(), page_cloud_dashboard(), Firebase & Auth API blueprint — login, sync, user setup, cloud dashboard.  Ext, Export all user's cloud data (GDPR data portability)., Delete all cloud data and logout., Serve the personal cloud dashboard with user's Firebase config injected. (+16 more)

### Community 5 - "Project Docs and Concepts"
Cohesion: 0.06
Nodes (41): Admin Aggregates, Aider Developer, Atomic JSON Task Plan, Bridge Orchestrator, Bridge Progress Artifacts, Case Study Findings, Cloud Functions Rollup, Codex Aider Bridge App (+33 more)

### Community 6 - "Deterministic Execution Pipeline"
Cohesion: 0.1
Nodes (28): DeterministicExecutionError, execute_deterministic(), extract_function_context(), Deterministic executor — applies structured operations without LLM involvement., Extract a specific function's code from a file for context minimization., Raised when deterministic execution fails verification., Try to execute a task deterministically (no LLM).      Returns ExecutionResult, classify_complexity() (+20 more)

### Community 7 - "Telemetry Reporting"
Cohesion: 0.11
Nodes (10): get_collector(), _global_telemetry_path(), Bridge Telemetry — collects anonymized usage data for product improvement.  Wr, Build the telemetry report for AI consumption., Save telemetry to disk. Returns the file path., Global telemetry (not project-specific)., Collects structured usage events for product analytics., Record a telemetry event.          Categories:           run      — run lifec (+2 more)

### Community 8 - "Relay API Workflow"
Cohesion: 0.11
Nodes (26): api_relay_generate_prompt(), api_relay_import_plan(), api_relay_import_replan(), api_relay_replan_prompt(), api_relay_review_packet(), api_relay_skip_task(), api_relay_state(), api_relay_submit_decision() (+18 more)

### Community 9 - "Manual Supervisor Session"
Cohesion: 0.14
Nodes (2): ManualSupervisorSession, BridgeResilienceTests

### Community 10 - "Run Diagnostics"
Cohesion: 0.12
Nodes (11): _classify_aider_failure(), Run Diagnostics — structured failure analysis for AI supervisors.  Accumulates, Record the escalation history for a task., Build the final diagnostics report dict., Write the diagnostics JSON file., Return the last max_lines lines of text., Classify an Aider failure into a named reason + detail string., Build a plain-English summary for AI consumption. (+3 more)

### Community 11 - "Mechanical Validation"
Cohesion: 0.14
Nodes (10): ValidationResult, _detect_project_type(), MechanicalValidator, _ProjectType, Dispatch to the correct syntax checker based on detected project type., Query Unity console for compiler errors via the MCP HTTP server.          Grac, Detect special tokens injected by LLMs into source files., Verify that { and } counts match in each C# file. (+2 more)

### Community 12 - "Dashboard Frontend Core"
Cohesion: 0.16
Nodes (24): $(), bindControls(), connectSSE(), escHtml(), handleComplete(), handleError(), handlePaused(), handlePlanReady() (+16 more)

### Community 13 - "Knowledge View Frontend"
Cohesion: 0.19
Nodes (23): $(), applyFileFilter(), buildFileRows(), _buildTree(), esc(), _fileIcon(), _getAutoRefreshMinutes(), init() (+15 more)

### Community 14 - "Run Page Frontend"
Cohesion: 0.27
Nodes (21): $(), addBridgeMsg(), addPasteCard(), addPromptCard(), addReviewCard(), addStatusCard(), addTaskListCard(), addUserMsg() (+13 more)

### Community 15 - "Onboarding Scanner"
Cohesion: 0.13
Nodes (20): _collect_files(), _detect_language(), _detect_patterns(), _detect_project_type(), _extract_csharp_role(), _extract_go_role(), _extract_js_ts_role(), _extract_python_role() (+12 more)

### Community 16 - "Main Orchestration Loop"
Cohesion: 0.19
Nodes (21): build_argument_parser(), _build_latest_report(), _build_project_snapshot(), _build_task_metrics(), _emit_structured(), estimate_session_tokens(), _execute_sub_tasks(), execute_task_with_review() (+13 more)

### Community 17 - "Bridge Runner Service"
Cohesion: 0.13
Nodes (4): BridgeRun, Manages one bridge subprocess, parses its log output into structured     task e, Write a line of text to the running subprocess stdin. Returns True if sent., Bridge UI package — Flask web interface for the Codex Aider Bridge App.

### Community 18 - "Chat API Routes"
Cohesion: 0.19
Nodes (17): api_chat(), api_chat_history(), api_chat_history_delete(), api_chat_history_save(), api_chat_start(), api_chat_state(), api_chat_stop(), _build_chat_prompt_messages() (+9 more)

### Community 19 - "Monitor Frontend"
Cohesion: 0.19
Nodes (16): $(), appendLog(), applyTaskEvent(), connectSSE(), fmtNum(), fmtPct(), init(), loadCurrentRun() (+8 more)

### Community 20 - "Git Frontend"
Cohesion: 0.23
Nodes (17): $(), _addToGitignore(), _bindChangedFileEvents(), bindControls(), _bindTreeEvents(), getRepo(), init(), loadAll() (+9 more)

### Community 21 - "Project Switcher UI"
Cohesion: 0.28
Nodes (18): $(), addProject(), closeDropdown(), esc(), fetchCurrentPath(), fetchProjects(), initProjectBar(), loadModels() (+10 more)

### Community 22 - "Environment Preflight Checks"
Cohesion: 0.2
Nodes (16): check_aider(), check_all(), check_claude(), check_codex(), check_gpu(), check_gpu_processes(), check_ollama(), check_preflight() (+8 more)

### Community 23 - "Project Knowledge Cache"
Cohesion: 0.17
Nodes (16): _empty_knowledge(), load_knowledge(), _normalize_knowledge_shape(), Project knowledge cache — stores what every file does so any AI can understand, Update knowledge after a successful bridge run.      - Registers every file to, Produce a compact human-readable summary for injection into AI prompts.      T, Return a blank knowledge structure for a new project., Accept both the bridge-native schema and summary-style external schemas. (+8 more)

### Community 24 - "Project Understanding Builder"
Cohesion: 0.27
Nodes (15): _build_open_questions(), _build_project_doc(), _build_terminal_summary(), _collect_clarifications(), _confirm_understanding(), _discover_project_docs(), ensure_project_understanding(), _extract_title() (+7 more)

### Community 25 - "Chat Drawer UI"
Cohesion: 0.3
Nodes (15): $(), autoResize(), clearChat(), currentHistory(), initChatDrawer(), loadCurrentProject(), projectLabel(), refreshState() (+7 more)

### Community 26 - "History Page UI"
Cohesion: 0.26
Nodes (13): $(), applyFilter(), bindControls(), bindTableActions(), clearAll(), closeLogModal(), deleteEntry(), esc() (+5 more)

### Community 27 - "Relay Formatter Utilities"
Cohesion: 0.14
Nodes (15): build_plan_prompt(), build_replan_prompt(), build_review_packet(), _extract_first_json_object(), parse_decision(), parse_plan(), relay_formatter.py — Prompt/packet generation and response parsing for AI Relay, Return the substring of *text* from the first '{' to its matching '}'. (+7 more)

### Community 28 - "Git Routes API"
Cohesion: 0.24
Nodes (14): api_git_branches(), api_git_checkout(), api_git_diff(), api_git_gitignore(), api_git_log(), api_git_status(), api_vscode_open(), _get_repo() (+6 more)

### Community 29 - "System Routes API"
Cohesion: 0.14
Nodes (13): api_benchmark(), api_gpu_processes(), api_kill_process(), api_recommend_model(), api_run_preflight(), api_unload_model(), System API blueprint — GPU, benchmark, model recommendation, preflight.  Extra, Benchmark Ollama model speed — measures tokens/second. (+5 more)

### Community 30 - "Setup Page UI"
Cohesion: 0.32
Nodes (12): $(), applyCard(), esc(), init(), loadGpuInfo(), renderGpuProcs(), renderModelList(), runChecks() (+4 more)

### Community 31 - "Model Advisor"
Cohesion: 0.2
Nodes (13): detect_system(), _get_nvidia_gpu(), _get_ollama_models(), _get_ram_gb(), ModelOption, Model Advisor — recommends the best Ollama coding model based on system specs., Get total system RAM in GB., Detect NVIDIA GPU name and VRAM. (+5 more)

### Community 32 - "Action Log Utility"
Cohesion: 0.27
Nodes (8): _getState(), logAction(), logAPI(), logClick(), logError(), logSSE(), logStateChange(), _now()

### Community 33 - "Git Manager"
Cohesion: 0.38
Nodes (11): auto_commit_task_changes(), collect_git_readiness(), ensure_git_baseline_commit(), ensure_git_repository_exists(), get_git_branch_name(), has_git_head(), is_git_repository(), log_git_readiness_preview() (+3 more)

### Community 34 - "Shared App State"
Cohesion: 0.2
Nodes (7): broadcast(), build_chat_context(), get_run(), Shared application state — SSE broadcast, run access, knowledge cache.  Bluepr, Push an event to all connected SSE clients., Get the singleton BridgeRun instance., Build project knowledge context string (cached 60s).

### Community 35 - "Cloud Page UI"
Cohesion: 0.47
Nodes (9): $(), init(), loadStatus(), showConnected(), showNotConfigured(), showSetup(), showSetupMsg(), syncNow() (+1 more)

### Community 36 - "Checkpoint Persistence"
Cohesion: 0.29
Nodes (9): clear_checkpoint(), load_checkpoint(), _progress_dir(), Lightweight checkpoint: persist completed task IDs so a failed run can resume., Return (and create) the per-project bridge progress directory., Write completed task IDs and plan hash to the checkpoint file., Load completed task IDs from the checkpoint file.      If *expected_plan_hash*, Delete the checkpoint file after a fully successful run. (+1 more)

### Community 37 - "Firebase Auth Frontend"
Cohesion: 0.39
Nodes (7): $(), checkAuthStatus(), disableSync(), enableSync(), login(), logout(), updateAuthUI()

### Community 38 - "Keyboard Shortcuts"
Cohesion: 0.42
Nodes (8): buildOverlay(), cancelChord(), hideHelp(), initShortcuts(), isHelpVisible(), onKeyDown(), showHelp(), startChord()

### Community 39 - "Sound Effects"
Cohesion: 0.31
Nodes (5): ctx(), noise(), setMuted(), toggleMute(), tone()

### Community 40 - "Desktop Launcher"
Cohesion: 0.36
Nodes (7): _ensure_deps(), main(), launch_ui.py — Codex-Aider Bridge desktop application entry point.  Modes ---, Auto-install Flask and pywebview when running from source (not frozen)., Native error dialog — works even with no console / no window yet., _show_error(), _wait_for_port()

### Community 41 - "Failure Feedback"
Cohesion: 0.29
Nodes (7): build_retry_instruction(), classify_failure(), FailureFeedback, Failure feedback — structured error classification for intelligent retry.  Aft, Adjust the instruction based on failure feedback for the next retry., Structured feedback from a failed task attempt., Classify a task failure and suggest recovery action.

### Community 42 - "SSE Client"
Cohesion: 0.29
Nodes (1): SSEClient

### Community 43 - "Deep Scanner"
Cohesion: 0.29
Nodes (7): Deep scanner — extract function signatures and data shapes from source files., Scan all source files in the project and return signatures per file.      Retu, Convert scanned signatures to a compact text block for prompt injection., Extract function signatures and key patterns from a source file.      Returns, scan_file_signatures(), scan_project_signatures(), signatures_to_context()

### Community 44 - "Project Type Prompt"
Cohesion: 0.32
Nodes (7): _confirm(), describe(), _print_banner(), prompt_project_type(), Interactive project-type selection prompt shown at first bridge run.  Displaye, Display an interactive menu and return the chosen type key.      Returns None, Return a human-readable description for a type key.

### Community 45 - "Help Modal UI"
Cohesion: 0.53
Nodes (4): buildModal(), initHelp(), openHelp(), showHelp()

### Community 46 - "Wallpaper Manager"
Cohesion: 0.6
Nodes (5): applyWallpaper(), evictIfNewDay(), fetchWallpaperUrl(), initWallpaper(), todayStr()

### Community 47 - "Command Resolution"
Cohesion: 0.6
Nodes (5): build_missing_executable_message(), iter_local_script_directories(), resolve_command_arguments(), resolve_executable(), split_command()

### Community 48 - "Unity Checks"
Cohesion: 0.33
Nodes (5): call_unity_mcp_tool(), Unity-specific validation helpers — MCP tool calls, availability check.  Extra, Call a Unity MCP tool via HTTP.  Returns the result dict or None on failure., Return True if the Unity MCP server is reachable and healthy., unity_mcp_available()

### Community 49 - "Memory Client"
Cohesion: 0.4
Nodes (4): enhance_prompt(), ingest_result(), Sends an instruction to the memory service for enhancement.      Args:, Ingests a result pair (input/output) into the memory service.      Args:

### Community 50 - "Run Report Generator"
Cohesion: 0.5
Nodes (4): _build_report(), generate_run_report(), Generate a Markdown run report from a session token report.  Writes bridge_pro, Build a Markdown report and write it to bridge_progress/RUN_REPORT.md.      Re

### Community 51 - "API Fetch Helpers"
Cohesion: 0.83
Nodes (3): apiDelete(), apiFetch(), apiPost()

### Community 52 - "Markdown Renderer"
Cohesion: 1.0
Nodes (3): esc(), inlineFormat(), renderMarkdown()

### Community 53 - "Toast Notifications"
Cohesion: 0.83
Nodes (3): escHtml(), getContainer(), toast()

### Community 54 - "Version Tracking"
Cohesion: 0.5
Nodes (3): get_version_info(), Version tracking — auto-increments on each commit., Get version + git commit info.

### Community 55 - "Fallback Planner"
Cohesion: 0.67
Nodes (1): FallbackPlanner

### Community 56 - "Event Notifier"
Cohesion: 0.67
Nodes (0): 

### Community 57 - "Theme Toggle"
Cohesion: 0.67
Nodes (0): 

### Community 58 - "Logging Configuration"
Cohesion: 1.0
Nodes (0): 

### Community 59 - "Aider Config Constants"
Cohesion: 1.0
Nodes (1): Aider configuration constants — error patterns, prompt patterns, standards files

### Community 60 - "Feature Manifest Loader"
Cohesion: 1.0
Nodes (1): Read all .md files in *folder* and return as a feature manifest.

### Community 61 - "Context Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 62 - "Task IR Validation"
Cohesion: 1.0
Nodes (1): True if all operations have exact search/replace pairs.

### Community 63 - "Executor Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 64 - "Firebase Functions Index"
Cohesion: 1.0
Nodes (0): 

### Community 65 - "Memory Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 66 - "Synthetic Task IDs"
Cohesion: 1.0
Nodes (1): Synthetic ID: parent_id * 1000 + step (e.g. task 3 step 2 → 3002).

### Community 67 - "Parser Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 68 - "Codex Client"
Cohesion: 1.0
Nodes (0): 

### Community 69 - "Planner Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 70 - "Planning Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 71 - "UI API Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 72 - "Store Core"
Cohesion: 1.0
Nodes (0): 

### Community 73 - "Utils Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 74 - "Validator Package Init"
Cohesion: 1.0
Nodes (0): 

### Community 75 - "Bridge Favicon Icon"
Cohesion: 1.0
Nodes (1): Bridge Favicon Icon

## Knowledge Gaps
- **186 isolated node(s):** `launch_ui.py — Codex-Aider Bridge desktop application entry point.  Modes ---`, `Auto-install Flask and pywebview when running from source (not frozen).`, `Native error dialog — works even with no console / no window yet.`, `Load project brief / idea text from a single file or a folder of specs.      W`, `Read all .md files in *folder* and return as a feature manifest.` (+181 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Logging Configuration`** (2 nodes): `logger.py`, `configure_logging()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Aider Config Constants`** (2 nodes): `Aider configuration constants — error patterns, prompt patterns, standards files`, `aider_config.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Feature Manifest Loader`** (1 nodes): `Read all .md files in *folder* and return as a feature manifest.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Context Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Task IR Validation`** (1 nodes): `True if all operations have exact search/replace pairs.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Executor Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Firebase Functions Index`** (1 nodes): `index.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Memory Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Synthetic Task IDs`** (1 nodes): `Synthetic ID: parent_id * 1000 + step (e.g. task 3 step 2 → 3002).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Parser Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Codex Client`** (1 nodes): `codex_client.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Planner Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Planning Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `UI API Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Store Core`** (1 nodes): `store.js`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Utils Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Validator Package Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Bridge Favicon Icon`** (1 nodes): `Bridge Favicon Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SupervisorAgent` connect `Supervisor Workflow Core` to `Task Planning and Aider`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Why does `RepoScanner` connect `Supervisor Workflow Core` to `Relay API Workflow`, `Task Planning and Aider`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `AuthError` connect `Firebase Sync Service` to `Task Planning and Aider`, `Firebase API Routes`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Are the 71 inferred relationships involving `SupervisorAgent` (e.g. with `Run one task through the Aider → diff → mechanical check loop.      In auto-ap` and `Block execution if a pause file exists at the repo root.      The UI creates .`) actually correct?**
  _`SupervisorAgent` has 71 INFERRED edges - model-reasoned connections that need verification._
- **Are the 67 inferred relationships involving `RepoScanner` (e.g. with `Run one task through the Aider → diff → mechanical check loop.      In auto-ap` and `Block execution if a pause file exists at the repo root.      The UI creates .`) actually correct?**
  _`RepoScanner` has 67 INFERRED edges - model-reasoned connections that need verification._
- **Are the 56 inferred relationships involving `Task` (e.g. with `Run one task through the Aider → diff → mechanical check loop.      In auto-ap` and `Block execution if a pause file exists at the repo root.      The UI creates .`) actually correct?**
  _`Task` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 52 inferred relationships involving `OnboardingScanner` (e.g. with `ProjectDoc` and `HealthWatchdog`) actually correct?**
  _`OnboardingScanner` has 52 INFERRED edges - model-reasoned connections that need verification._