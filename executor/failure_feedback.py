"""Failure feedback — structured error classification for intelligent retry.

After a task fails, this module classifies the failure type and generates
structured feedback that the retry system uses to adjust its approach:

  pattern_mismatch → switch to whole format
  no_change        → simplify instruction
  context_overflow → strip context, reduce prompt
  useless_response → switch model or escalate
  syntax_error     → ask LLM to fix specific error
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FailureFeedback:
    """Structured feedback from a failed task attempt."""
    failure_type: str  # pattern_mismatch | no_change | context_overflow | useless_response | syntax_error | timeout | unknown
    reason: str
    observed_output: str = ""  # what the LLM actually produced (truncated)
    expected_pattern: str = ""  # what we expected to find
    suggested_action: str = ""  # what the retry system should do
    should_switch_edit_mode: bool = False
    should_strip_context: bool = False
    should_simplify_instruction: bool = False
    should_escalate: bool = False
    is_retryable: bool = True


def classify_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    file_changed: bool,
    instruction: str,
) -> FailureFeedback:
    """Classify a task failure and suggest recovery action."""
    stdout_lower = (stdout or "").lower()
    stderr_lower = (stderr or "").lower()
    combined = f"{stdout_lower}\n{stderr_lower}"

    # 1. Context overflow — model acknowledged but didn't code
    useless_patterns = [
        "i will keep that in mind",
        "i'll keep that in mind",
        "let me know if you need",
        "please let me know",
        "if you have any questions",
        "i understand the task",
        "i'll help you with that",
    ]
    for p in useless_patterns:
        if p in stdout_lower:
            return FailureFeedback(
                failure_type="context_overflow",
                reason=f"Model gave useless response: '{p}'",
                observed_output=stdout[:200],
                suggested_action="Strip context files, reduce repo-map to 0, shorten instruction",
                should_strip_context=True,
                should_simplify_instruction=True,
            )

    # 2. Token limit exceeded
    if "exceeds the" in combined and "token" in combined:
        return FailureFeedback(
            failure_type="context_overflow",
            reason="Prompt exceeds model context window",
            suggested_action="Remove repo-map, strip context files, minimize prompt",
            should_strip_context=True,
        )

    # 3. SEARCH/REPLACE pattern mismatch
    _normalized = combined.replace(" ", "").replace("_", "").replace("-", "").lower()
    if "searchreplaceno" in _normalized or "failed to match" in combined or "block failed to match" in combined:
        return FailureFeedback(
            failure_type="pattern_mismatch",
            reason="SEARCH block didn't match actual file content",
            observed_output=stdout[:300],
            suggested_action="Switch from diff to whole format",
            should_switch_edit_mode=True,
        )

    # 4. Edit format conformance error
    if "did not conform to the edit format" in combined:
        return FailureFeedback(
            failure_type="pattern_mismatch",
            reason="LLM output didn't follow the required edit format",
            suggested_action="Switch to whole format (more forgiving)",
            should_switch_edit_mode=True,
        )

    # 5. Timeout / stall (check BEFORE no_change)
    if "timed out" in combined or "stalled" in combined:
        return FailureFeedback(
            failure_type="timeout",
            reason="Aider/Ollama timed out",
            suggested_action="Strip context, reduce file size, use lightweight mode",
            should_strip_context=True,
            is_retryable=True,
        )

    # 6. Connection / config errors (check BEFORE no_change)
    config_errors = [
        "litellm.badrequesterror", "connection refused", "model not found",
        "connection error", "invalid_api_key", "authenticationerror",
        "could not connect", "connecterror",
    ]
    for err in config_errors:
        if err in combined:
            return FailureFeedback(
                failure_type="config_error",
                reason=f"LLM configuration error: {err}",
                suggested_action="Fix configuration — this won't self-heal with retries",
                is_retryable=False,
                should_escalate=True,
            )

    # 7. No changes made (silent failure — checked after config/timeout)
    if not file_changed:
        return FailureFeedback(
            failure_type="no_change",
            reason="Aider exited successfully but made no file changes",
            suggested_action="Simplify instruction, be more explicit about what to change",
            should_simplify_instruction=True,
        )

    # 8. Generic failure
    return FailureFeedback(
        failure_type="unknown",
        reason=stderr[:200] if stderr else "Unknown failure",
        observed_output=stdout[:200],
        suggested_action="Retry with simplified instruction",
        should_simplify_instruction=True,
    )


def build_retry_instruction(
    original_instruction: str,
    feedback: FailureFeedback,
    attempt: int,
) -> str:
    """Adjust the instruction based on failure feedback for the next retry."""
    if feedback.failure_type == "pattern_mismatch":
        return (
            f"{original_instruction}\n\n"
            "NOTE: Previous attempt failed because the edit pattern didn't match "
            "the actual file content. Rewrite the ENTIRE relevant section — do not "
            "try to match specific lines."
        )

    if feedback.failure_type == "no_change":
        return (
            f"{original_instruction}\n\n"
            "IMPORTANT: Previous attempt made NO changes to the file. "
            "You MUST modify the file. Do not just acknowledge — write the actual code."
        )

    if feedback.failure_type == "context_overflow":
        # Shorten the instruction to save tokens
        words = original_instruction.split()
        if len(words) > 100:
            shortened = " ".join(words[:80])
            return f"{shortened}\n\n(Instruction shortened to fit context window)"
        return original_instruction

    if attempt >= 3:
        return (
            f"{original_instruction}\n\n"
            f"This is attempt {attempt}. Previous attempts failed with: {feedback.reason}. "
            "Try a COMPLETELY DIFFERENT approach."
        )

    return original_instruction
