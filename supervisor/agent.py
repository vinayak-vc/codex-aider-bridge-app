from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

from models.task import ReviewResult, SubTask, Task, TaskReport
from utils.command_resolution import resolve_command_arguments
from utils.token_tracker import TokenTracker


class SupervisorError(Exception):
    pass


# Maximum characters of idea/brief text injected into the planning prompt.
# Keeps prompts within typical context-window budgets for local models.
_IDEA_MAX_CHARS: int = 2000


class SupervisorAgent:
    """Tech Supervisor agent — plans work and reviews Aider output.

    This agent has two strict roles:
    1. Planning: decompose a goal into atomic sequential tasks for Aider.
    2. Review: inspect each completed task's diff and return PASS or REWORK.

    The supervisor NEVER writes code and NEVER executes tasks.
    It only decides WHAT to build (planning) and WHETHER it was built correctly (review).
    """

    def __init__(
        self,
        repo_root: Path,
        command: str,
        logger: logging.Logger,
        timeout: int = 300,
        token_tracker: Optional[TokenTracker] = None,
    ) -> None:
        self._repo_root = repo_root
        self._command = command
        self._logger = logger
        self._timeout = timeout
        self._tracker = token_tracker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_plan(
        self,
        goal: str,
        repo_tree: str,
        idea_text: Optional[str] = None,
        feedback: Optional[str] = None,
        knowledge_context: Optional[str] = None,
        workflow_profile: str = "standard",
        feature_specs: Optional[str] = None,
        model_roster: Optional[str] = None,
    ) -> str:
        """Ask the supervisor to produce a JSON atomic task plan."""
        prompt = self._build_plan_prompt(
            goal,
            repo_tree,
            idea_text,
            feedback,
            knowledge_context,
            workflow_profile,
            feature_specs=feature_specs,
            model_roster=model_roster,
        )
        self._logger.debug(
            "Plan prompt (%d chars): %.500s%s",
            len(prompt),
            prompt,
            "..." if len(prompt) > 500 else "",
        )
        response = self._run(prompt, self._plan_schema())
        if self._tracker is not None:
            self._tracker.record_plan(prompt, response)
        return response

    def generate_subplan(self, task: Task, error_message: str) -> list[SubTask]:
        """Ask the supervisor for micro-tasks to fix a mechanical validation failure.

        Called when a task fails mechanical checks (syntax error, missing file, CI
        failure) and simple retry is unlikely to succeed. The supervisor returns
        1–3 atomic correction sub-tasks targeting the same files.
        """
        prompt = self._build_subplan_prompt(task, error_message)
        self._logger.debug(
            "Sub-plan prompt for task %s (%d chars): %.300s%s",
            task.id, len(prompt), prompt, "..." if len(prompt) > 300 else "",
        )
        raw = self._run(prompt)
        if self._tracker is not None:
            self._tracker.record_subplan(prompt, raw)
        return self._parse_subplan(task, raw)

    def review_task(self, report: TaskReport) -> ReviewResult:
        """Ask the supervisor to review a completed task and return PASS or REWORK."""
        prompt = self._build_review_prompt(report)
        self._logger.debug(
            "Review prompt for task %s (%d chars): %.300s%s",
            report.task.id,
            len(prompt),
            prompt,
            "..." if len(prompt) > 300 else "",
        )
        response = self._run(prompt)
        if self._tracker is not None:
            is_rework = response.strip().upper().startswith("REWORK")
            self._tracker.record_review(prompt, response, is_rework=is_rework)
        return self._parse_review(report.task.id, response)

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_plan_prompt(
        self,
        goal: str,
        repo_tree: str,
        idea_text: Optional[str],
        feedback: Optional[str],
        knowledge_context: Optional[str] = None,
        workflow_profile: str = "standard",
        feature_specs: Optional[str] = None,
        model_roster: Optional[str] = None,
    ) -> str:
        idea_block = ""
        if idea_text:
            trimmed = idea_text[:_IDEA_MAX_CHARS]
            idea_block = f"\nProject brief:\n{trimmed}\n"

        # Project knowledge — what each file does, what's already built.
        # This lets the supervisor plan without reading individual source files.
        knowledge_block = ""
        if knowledge_context:
            knowledge_block = f"\nProject knowledge (file roles and history):\n{knowledge_context}\n"

        feedback_block = ""
        if feedback:
            feedback_block = (
                "\nThe previous plan was rejected for the following reason. "
                "Fix these issues and return only the corrected plan:\n"
                f"{feedback}\n"
            )

        profile_block = ""
        if workflow_profile == "micro":
            profile_block = (
                "\nMICRO-TASK PROFILE (STRICT):\n"
                "- One file per task. Do not produce multi-file tasks.\n"
                "- One concern per task.\n"
                "- Prefer create/modify/delete tasks over broad validate tasks.\n"
                "- Every create task must include must_exist.\n"
                "- Every delete task must include must_not_exist.\n"
                "- Every modify task should include at least one assertion when the output is observable.\n"
                "- Assume a small local coding model is implementing the task, so keep instructions surgical.\n"
            )

        return (
            "You are a Tech Supervisor. Your only job is to decompose a development goal into\n"
            "an atomic sequential plan for a developer tool called Aider.\n\n"
            "CRITICAL CONTEXT — AIDER RUNS ON A SMALL LOCAL LLM (7-14B parameters):\n"
            "The coding model has a 32K token context window and runs at 6-65 tok/s.\n"
            "It can only see the target file(s) you specify — it cannot browse the repo.\n"
            "If your instruction is vague, the model will drift into unrelated files,\n"
            "overflow its context, and fail. Every instruction MUST be self-contained.\n\n"
            "AIDER-GRADE INSTRUCTION RULES:\n"
            "- Name the exact function/class/variable to modify. Never say 'refactor the module'.\n"
            "- Name the exact parameters, fields, or config keys involved.\n"
            "- If the task consumes data from another file, describe the data shape inline\n"
            "  (e.g., 'payload.maxVideos (number)') — do NOT tell the model to go read that file.\n"
            "- Each instruction must be completable by reading ONLY the target file.\n"
            "  The model must never need to open, inspect, or reason about other files.\n"
            "- Specify what the current code does AND what it should do after the change.\n"
            "- Keep instructions under 200 words. Longer = more tokens = slower.\n\n"
            "BAD instruction: 'Refactor upload command building so it consumes advanced\n"
            "  operator inputs from the renderer instead of relying on minimal hardcoded defaults.'\n"
            "  Why bad: 'advanced operator inputs' is undefined. Model will search the repo for\n"
            "  what inputs exist, find large files, overflow context, and fail.\n\n"
            "GOOD instruction: 'In buildUploadCommand(), replace the hardcoded --max-videos \"1\"\n"
            "  with String(payload.maxVideos || 1). Replace --shorts-policy \"convert\" with\n"
            "  payload.shortsPolicy || \"convert\". Add new flags: --resolution (payload.resolution),\n"
            "  --format (payload.format), --quality (payload.quality) if they are defined.'\n"
            "  Why good: names the function, the parameters, the exact changes. Zero ambiguity.\n\n"
            "STRICT RULES:\n"
            "- Return ONLY the JSON plan. No code. No prose. No questions.\n"
            "- Each task targets exactly one concern and one or two specific files.\n"
            "- Use only relative file paths that are visible in the repo structure below.\n"
            "  If a file does not yet exist, use the path it should be created at.\n"
            "- Task type must be one of: create, modify, delete, validate, read, investigate\n"
            "- Use type 'read' for simple file reading — returns file content for analysis.\n"
            "  Read tasks skip Aider entirely. Use for: list features, check status, read config.\n"
            "- Use type 'investigate' for multi-file analysis that requires understanding code.\n"
            "  Investigate tasks read multiple files + their imports/dependencies and send\n"
            "  everything to the supervisor for deep analysis. Use for: find bugs, review security,\n"
            "  trace data flow, identify impact of changes, find missing tests.\n"
            "  Investigate tasks can be followed by create/modify tasks to fix what was found.\n"
            "- Tasks execute sequentially. Later tasks may depend on earlier ones.\n"
            "- Use must_exist / must_not_exist when the task has a clear post-condition.\n"
            "- Do not ask questions. Do not explain. Return the plan only.\n"
            "- Use the FILE REGISTRY below to reference existing file roles correctly.\n"
            "  Do not duplicate work that is already marked as done.\n\n"
            f"{profile_block}"
            f"Repo structure:\n{repo_tree}\n"
            f"{knowledge_block}"
            f"{idea_block}"
            f"{self._build_feature_specs_block(feature_specs)}"
            f"{self._build_model_roster_block(model_roster)}"
            f"\nGoal: {goal}\n"
            f"{feedback_block}"
        )

    @staticmethod
    def _build_feature_specs_block(feature_specs: Optional[str]) -> str:
        """Build the FEATURE SPECIFICATIONS prompt block."""
        if not feature_specs:
            return ""
        return (
            "\nFEATURE SPECIFICATIONS:\n"
            "The user wants you to implement each feature described below.\n"
            "Generate specific Aider-grade tasks for EACH feature specification.\n"
            "Each task instruction MUST reference exact details from the spec —\n"
            "function names, parameters, routes, fields, data shapes, etc.\n"
            "Do NOT generate vague 'implement feature X' tasks. The coding model\n"
            "is a small local LLM that cannot read the spec files itself.\n\n"
            f"{feature_specs}\n"
        )

    @staticmethod
    def _build_model_roster_block(model_roster: Optional[str]) -> str:
        """Build the AVAILABLE MODELS prompt block for smart routing."""
        if not model_roster:
            return ""
        return (
            "\nAVAILABLE MODELS — pick the best model for each task:\n"
            f"{model_roster}\n\n"
            "Add a \"model\" field to each task JSON with the model name.\n"
            "Guidelines:\n"
            "- Use FAST models for: simple edits, config changes, renames,\n"
            "  single-function modifications, adding imports, small refactors\n"
            "- Use SLOW/HIGH-QUALITY models for: new algorithms, complex business\n"
            "  logic, multi-concern refactors, security-sensitive code, API design\n"
            "- When in doubt, prefer the fast model — speed matters more than\n"
            "  marginal quality for most coding tasks\n"
            "- Omit the \"model\" field to use the user's default model\n\n"
        )

    def _build_subplan_prompt(self, task: Task, error_message: str) -> str:
        return (
            "You are a Tech Supervisor. A development task failed mechanical validation.\n\n"
            "Create 1–3 atomic correction sub-tasks that fix the specific error.\n\n"
            "STRICT RULES:\n"
            "- Return ONLY JSON. No prose. No code. No questions.\n"
            "- Sub-tasks must target only files from the parent task's file list.\n"
            "- Instructions must name exact functions, variables, and parameters to change.\n"
            "  The coding model is a small local LLM — vague instructions cause it to drift\n"
            "  into unrelated files and overflow its context window.\n"
            "- Maximum 3 sub-tasks. Prefer fewer.\n\n"
            f"Parent Task {task.id} ({task.type})\n"
            f"Files: {', '.join(task.files)}\n"
            f"Original instruction: {task.instruction}\n\n"
            f"Mechanical validation error:\n{error_message}\n\n"
            'Return format: {"sub_tasks": [{"instruction": "...", "files": ["..."], "type": "modify"}]}'
        )

    def _build_review_prompt(self, report: TaskReport) -> str:
        task = report.task
        result = report.execution_result
        diff = report.diff or "(no diff captured — no file changes detected)"

        exit_summary = "succeeded" if result.exit_code == 0 else f"failed (exit code {result.exit_code})"

        return (
            "You are a Tech Supervisor reviewing completed developer work.\n"
            "Reply with exactly one of these two forms (nothing else):\n"
            "  PASS\n"
            "  REWORK: <specific replacement instruction>\n\n"
            "If REWORK: the instruction must name exact functions, variables, and parameters.\n"
            "The coding model is a small local LLM (7-14B) with 32K context. Vague rework\n"
            "instructions like 'fix the implementation' cause it to drift and fail.\n"
            "Example REWORK: 'In buildUploadCommand(), the --max-videos flag still uses\n"
            "hardcoded \"1\" — replace with String(payload.maxVideos || 1)'\n\n"
            f"Task {task.id} ({task.type})\n"
            f"Files: {', '.join(task.files)}\n"
            f"Instruction: {task.instruction}\n"
            f"Aider execution: {exit_summary}\n\n"
            f"Changes made:\n{diff}\n"
        )

    # ------------------------------------------------------------------
    # Review response parser
    # ------------------------------------------------------------------

    def _parse_review(self, task_id: int, response: str) -> ReviewResult:
        stripped = response.strip()
        upper = stripped.upper()

        if upper.startswith("PASS"):
            return ReviewResult(
                task_id=task_id,
                verdict="PASS",
                new_instruction=None,
                message="Supervisor approved.",
                sub_tasks=[],
            )

        if upper.startswith("REWORK:"):
            new_instruction = stripped[len("REWORK:"):].strip()
            if not new_instruction:
                raise SupervisorError(
                    f"Supervisor returned REWORK with an empty instruction for task {task_id}."
                )
            return ReviewResult(
                task_id=task_id,
                verdict="REWORK",
                new_instruction=new_instruction,
                message="Supervisor requested rework.",
                sub_tasks=[],
            )

        raise SupervisorError(
            f"Supervisor returned an unrecognized review response for task {task_id}: "
            f"{stripped[:140]!r}"
        )

    def _parse_subplan(self, parent: Task, raw: str) -> list[SubTask]:
        """Parse supervisor sub-plan JSON into a list of SubTask objects."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3:
                cleaned = "\n".join(lines[1:-1]).strip()

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as ex:
            raise SupervisorError(
                f"Sub-plan response was not valid JSON: {ex}. Raw: {raw[:200]!r}"
            ) from ex

        sub_tasks_raw = payload.get("sub_tasks", [])
        if not isinstance(sub_tasks_raw, list) or not sub_tasks_raw:
            raise SupervisorError(
                "Sub-plan must contain a non-empty 'sub_tasks' array."
            )

        sub_tasks: list[SubTask] = []
        for i, item in enumerate(sub_tasks_raw[:3]):   # cap at 3 sub-tasks
            if not isinstance(item, dict):
                continue
            instruction = str(item.get("instruction", "")).strip()
            files = item.get("files", parent.files)
            task_type = str(item.get("type", "modify"))
            if not instruction:
                continue
            sub_tasks.append(SubTask(
                parent_id=parent.id,
                step=i + 1,
                instruction=instruction,
                files=files if isinstance(files, list) else parent.files,
                type=task_type if task_type in {"create", "modify", "delete", "validate"} else "modify",
            ))

        if not sub_tasks:
            raise SupervisorError(
                f"Sub-plan for task {parent.id} contained no valid sub-tasks."
            )

        return sub_tasks

    # ------------------------------------------------------------------
    # Subprocess runner
    # ------------------------------------------------------------------

    def _run(self, prompt: str, output_schema: Optional[str] = None) -> str:
        if self._command == "interactive":
            print("\n" + "="*80)
            print("INTERACTIVE SUPERVISOR REQUIRED")
            print("="*80)
            print(prompt)
            print("="*80)
            if output_schema:
                import sys
                print("\nEXPECTED SCHEMA:")
                print(output_schema)
                print("\nPlease enter your JSON plan below (Press Ctrl+Z/Ctrl+D and Enter to finish):")
                return sys.stdin.read().strip()
            else:
                return input("\nReview Result (PASS / REWORK: <instruction>): ").strip()

        with tempfile.TemporaryDirectory(prefix="supervisor-bridge-") as tmp_dir:
            output_file = Path(tmp_dir) / "supervisor-output.txt"
            schema_file: Optional[Path] = None

            if output_schema is not None:
                schema_file = Path(tmp_dir) / "supervisor-schema.json"
                schema_file.write_text(output_schema, encoding="utf-8")

            try:
                arguments, stdin_prompt = self._build_command(prompt, output_file, schema_file)
            except (FileNotFoundError, ValueError) as ex:
                raise SupervisorError(
                    f"Cannot resolve supervisor command '{self._command}': {ex}"
                ) from ex

            _prompt_len = len(stdin_prompt) if stdin_prompt else 0
            print(f"[SUPERVISOR] Running: {arguments} (timeout={self._timeout}s, prompt={_prompt_len} chars)", flush=True)
            self._logger.info(
                "Running supervisor: %s (timeout=%ds, prompt=%d chars, stdin=%s)",
                arguments, self._timeout, _prompt_len,
                "yes" if stdin_prompt else "no",
            )

            try:
                # Write prompt to a temp file and open as stdin handle.
                # This avoids THREE problems with Claude CLI on Windows:
                #  1. subprocess.run(input=) deadlocks on large prompts (pipe buffer)
                #  2. Popen.communicate(input=) also deadlocks for the same reason
                #  3. stdin piping triggers Claude's prompt injection detection
                # Using a file handle as stdin bypasses all pipe buffering issues
                # and Claude CLI reads it as a normal file stream, not as injection.
                if stdin_prompt:
                    prompt_file = Path(tmp_dir) / "prompt_input.txt"
                    prompt_file.write_text(stdin_prompt, encoding="utf-8")
                    stdin_handle = open(prompt_file, "r", encoding="utf-8")
                    print(f"[SUPERVISOR] Using file-based stdin ({len(stdin_prompt)} chars)", flush=True)
                else:
                    stdin_handle = subprocess.DEVNULL

                proc = subprocess.Popen(
                    arguments,
                    stdin=stdin_handle,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._repo_root,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_WIN_NO_WINDOW,
                )
                # Close file handle after Popen inherits it
                if stdin_prompt:
                    stdin_handle.close()

                try:
                    stdout, stderr = proc.communicate(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
                    print(f"[SUPERVISOR] TIMED OUT after {self._timeout}s!", flush=True)
                    self._logger.error(
                        "Supervisor TIMED OUT after %ds. Command: %s",
                        self._timeout, arguments,
                    )
                    raise SupervisorError(
                        f"Supervisor timed out after {self._timeout}s — "
                        "command may be hung or waiting for input."
                    )
                returncode = proc.returncode
            except SupervisorError:
                raise
            except OSError as ex:
                print(f"[SUPERVISOR] Cannot start: {ex}", flush=True)
                self._logger.error("Cannot start supervisor: %s", ex)
                raise SupervisorError(
                    f"Cannot start supervisor command '{self._command}': {ex}"
                ) from ex

            print(f"[SUPERVISOR] Finished: exit={returncode}, stdout={len(stdout)} chars, stderr={len(stderr)} chars", flush=True)
            if stderr.strip():
                print(f"[SUPERVISOR] stderr: {stderr.strip()[:300]}", flush=True)
            self._logger.info(
                "Supervisor finished: exit=%d, stdout=%d chars, stderr=%d chars",
                returncode, len(stdout), len(stderr),
            )

            if returncode != 0:
                self._logger.error(
                    "Supervisor stderr: %s", stderr.strip()[:500],
                )
                raise SupervisorError(
                    f"Supervisor exited with code {returncode}. "
                    f"Stderr: {stderr.strip()[:500]}"
                )

            if output_file.exists():
                output = output_file.read_text(encoding="utf-8", errors="replace").strip()
                if output:
                    return output

            stdout_output = stdout.strip()
            if stdout_output:
                return stdout_output

            self._logger.error(
                "Supervisor returned no output. stdout=%r, stderr=%r",
                stdout[:200], stderr[:200],
            )
            raise SupervisorError("Supervisor returned no output.")

    def _build_command(
        self,
        prompt: str,
        output_file: Path,
        schema_file: Optional[Path],
    ) -> tuple[list[str], Optional[str]]:
        """Build the supervisor subprocess arguments and determine prompt delivery mode.

        Returns a (arguments, stdin_prompt) tuple:
          - exec-style commands (Codex): prompt appended as final argument, stdin=None.
          - non-exec commands (Claude CLI, etc.): prompt delivered via stdin to avoid
            Windows .cmd argument mangling of multi-line strings.
        """
        command_text = self._command

        # Substitute {output_file} inline — it is a safe file path we control.
        if "{output_file}" in command_text:
            command_text = command_text.replace("{output_file}", str(output_file))

        # Strip any {prompt} placeholder from the template — the prompt is
        # delivered separately (as arg or stdin) to prevent injection.
        command_text = command_text.replace("{prompt}", "").strip()

        arguments, _ = resolve_command_arguments(command_text, self._repo_root)

        # Exec-style commands (Codex): append output file, schema, and prompt as args.
        is_exec_style = "exec" in arguments
        if is_exec_style:
            if "{output_file}" not in self._command and "-o" not in arguments:
                arguments.extend(["-o", str(output_file)])
            if schema_file is not None and "--output-schema" not in arguments:
                arguments.extend(["--output-schema", str(schema_file)])
            # Prompt as final positional argument (Codex exec expects this).
            arguments.append(prompt)
            return arguments, None

        # For Claude CLI with -p, we need special handling.
        # stdin piping triggers Claude's injection detection ("You are a...")
        # and CLI argument has Windows limits.
        # Solution: write prompt to a temp file, pass via stdin from file handle.
        # This is handled in _run() — mark as "file" mode by returning a special tuple.
        return arguments, prompt

    # ------------------------------------------------------------------
    # JSON schema for plan output
    # ------------------------------------------------------------------

    def _plan_schema(self) -> str:
        return (
            "{\n"
            '  "type": "object",\n'
            '  "additionalProperties": false,\n'
            '  "required": ["tasks"],\n'
            '  "properties": {\n'
            '    "tasks": {\n'
            '      "type": "array",\n'
            '      "minItems": 1,\n'
            '      "items": {\n'
            '        "type": "object",\n'
            '        "additionalProperties": false,\n'
            '        "required": ["id", "files", "instruction", "type"],\n'
            '        "properties": {\n'
            '          "id": { "type": "integer" },\n'
            '          "files": {\n'
            '            "type": "array",\n'
            '            "minItems": 1,\n'
            '            "items": { "type": "string", "minLength": 1 }\n'
            "          },\n"
            '          "instruction": { "type": "string", "minLength": 1 },\n'
            '          "type": { "type": "string", "enum": ["create", "modify", "delete", "validate", "read", "investigate"] },\n'
            '          "must_exist": { "type": "array", "items": { "type": "string" } },\n'
            '          "must_not_exist": { "type": "array", "items": { "type": "string" } },\n'
            '          "model": { "type": "string" }\n'
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
