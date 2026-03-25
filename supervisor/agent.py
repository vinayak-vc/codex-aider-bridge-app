from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from models.task import ReviewResult, TaskReport
from utils.command_resolution import resolve_command_arguments


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

    def __init__(self, repo_root: Path, command: str, logger: logging.Logger, timeout: int = 300) -> None:
        self._repo_root = repo_root
        self._command = command
        self._logger = logger
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_plan(
        self,
        goal: str,
        repo_tree: str,
        idea_text: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> str:
        """Ask the supervisor to produce a JSON atomic task plan."""
        prompt = self._build_plan_prompt(goal, repo_tree, idea_text, feedback)
        self._logger.debug(
            "Plan prompt (%d chars): %.500s%s",
            len(prompt),
            prompt,
            "..." if len(prompt) > 500 else "",
        )
        return self._run(prompt, self._plan_schema())

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
    ) -> str:
        idea_block = ""
        if idea_text:
            trimmed = idea_text[:_IDEA_MAX_CHARS]
            idea_block = f"\nProject brief:\n{trimmed}\n"

        feedback_block = ""
        if feedback:
            feedback_block = (
                "\nThe previous plan was rejected for the following reason. "
                "Fix these issues and return only the corrected plan:\n"
                f"{feedback}\n"
            )

        return (
            "You are a Tech Supervisor. Your only job is to decompose a development goal into\n"
            "an atomic sequential plan for a developer tool called Aider.\n\n"
            "STRICT RULES:\n"
            "- Return ONLY the JSON plan. No code. No prose. No questions.\n"
            "- Each task targets exactly one concern and one or more specific files.\n"
            "- Use only relative file paths that are visible in the repo structure below.\n"
            "  If a file does not yet exist, use the path it should be created at.\n"
            "- Task type must be one of: create, modify, validate\n"
            "- Tasks execute sequentially. Later tasks may depend on earlier ones.\n"
            "- Instructions must be concrete but code-free: say WHAT to build, never HOW.\n"
            "- Do not ask questions. Do not explain. Return the plan only.\n\n"
            f"Repo structure:\n{repo_tree}\n"
            f"{idea_block}"
            f"\nGoal: {goal}\n"
            f"{feedback_block}"
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
            "  REWORK: <one-sentence atomic replacement instruction — no code>\n\n"
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
            )

        raise SupervisorError(
            f"Supervisor returned an unrecognized review response for task {task_id}: "
            f"{stripped[:140]!r}"
        )

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
                arguments = self._build_command(prompt, output_file, schema_file)
            except (FileNotFoundError, ValueError) as ex:
                raise SupervisorError(
                    f"Cannot resolve supervisor command '{self._command}': {ex}"
                ) from ex

            self._logger.debug("Running supervisor: %s", arguments)

            try:
                result = subprocess.run(
                    arguments,
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired as ex:
                if ex.process:
                    ex.process.kill()
                raise SupervisorError(
                    f"Supervisor timed out after {self._timeout}s — "
                    "command may be hung or waiting for input."
                ) from ex
            except OSError as ex:
                raise SupervisorError(
                    f"Cannot start supervisor command '{self._command}': {ex}"
                ) from ex

            if result.returncode != 0:
                raise SupervisorError(
                    f"Supervisor exited with code {result.returncode}. "
                    f"Stderr: {result.stderr.strip()}"
                )

            if output_file.exists():
                output = output_file.read_text(encoding="utf-8", errors="replace").strip()
                if output:
                    return output

            stdout_output = result.stdout.strip()
            if stdout_output:
                return stdout_output

            raise SupervisorError("Supervisor returned no output.")

    def _build_command(
        self,
        prompt: str,
        output_file: Path,
        schema_file: Optional[Path],
    ) -> list[str]:
        command_text = self._command

        # Substitute {output_file} inline — it is a safe file path we control.
        if "{output_file}" in command_text:
            command_text = command_text.replace("{output_file}", str(output_file))

        # Strip any {prompt} placeholder from the template — the prompt is
        # ALWAYS passed as a separate final argument (never inlined into the
        # command string) to prevent shell-metacharacter injection.
        command_text = command_text.replace("{prompt}", "").strip()

        arguments, _ = resolve_command_arguments(command_text, self._repo_root)

        # Auto-append -o <output_file> for codex exec style commands
        if "{output_file}" not in self._command and "exec" in arguments and "-o" not in arguments:
            arguments.extend(["-o", str(output_file)])

        # Auto-append --output-schema for codex exec style commands
        if schema_file is not None and "--output-schema" not in arguments and "exec" in arguments:
            arguments.extend(["--output-schema", str(schema_file)])

        # Prompt is always the final argument — a separate list element,
        # never embedded in the command string that gets shell-parsed.
        arguments.append(prompt)

        return arguments

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
            '          "type": { "type": "string", "enum": ["create", "modify", "validate"] }\n'
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
