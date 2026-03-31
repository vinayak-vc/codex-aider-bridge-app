"""
Driver Detection — auto-detect which AI agent is running the bridge
and return the matching supervisor command.

Detection priority (highest → lowest):
  1. Environment variables set by the agent runtime
  2. Parent-process name scan
  3. Available CLIs + API keys
  4. Fallback → interactive (human types PASS/REWORK)

Supported drivers and their supervisor commands:
  claude     → claude --dangerously-skip-permissions -p
  codex      → codex.cmd exec --skip-git-repo-check --color never
  cursor     → cursor --no-sandbox --wait -p   (Cursor AI terminal)
  windsurf   → windsurf -p
  aider      → interactive  (aider drives itself, no separate supervisor)
  unknown    → interactive
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


# ── Supervisor command map ─────────────────────────────────────────────────────

SUPERVISOR_COMMANDS: dict[str, str] = {
    "claude":    "claude --dangerously-skip-permissions -p",
    "codex":     "codex.cmd exec --skip-git-repo-check --color never",
    "cursor":    "cursor --no-sandbox --wait -p",
    "windsurf":  "windsurf -p",
    "aider":     "interactive",
    "unknown":   "interactive",
}


# ── Detection helpers ──────────────────────────────────────────────────────────

def _env(key: str) -> str:
    return os.environ.get(key, "").strip()


def _has_env(*keys: str) -> bool:
    return any(os.environ.get(k) for k in keys)


def _cli_available(name: str) -> bool:
    return shutil.which(name) is not None


def _parent_process_name() -> str:
    """Return the name of the parent process (best-effort, empty string on failure)."""
    try:
        import psutil
        parent = psutil.Process(os.getpid()).parent()
        if parent:
            return (parent.name() or "").lower()
    except Exception:
        pass
    return ""


def _scan_process_tree(keywords: list[str]) -> bool:
    """Return True if any ancestor process name contains one of the keywords."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        while proc:
            name = (proc.name() or "").lower()
            cmdline = " ".join(proc.cmdline()).lower()
            if any(kw in name or kw in cmdline for kw in keywords):
                return True
            parent = proc.parent()
            if parent is None or parent.pid == proc.pid:
                break
            proc = parent
    except Exception:
        pass
    return False


# ── Main detection logic ───────────────────────────────────────────────────────

def detect_driver() -> tuple[str, str]:
    """
    Returns (driver_name, supervisor_command).
    driver_name  — one of: claude, codex, cursor, windsurf, aider, unknown
    supervisor_command — the CLI command to use for supervision
    """

    # 1. Claude Code — most specific env vars
    if _has_env("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_OAUTH_TOKEN"):
        return "claude", SUPERVISOR_COMMANDS["claude"]

    # 2. OpenAI Codex CLI
    if _has_env("CODEX_SANDBOX", "CODEX_ENV") or _scan_process_tree(["codex"]):
        return "codex", SUPERVISOR_COMMANDS["codex"]

    # 3. Cursor IDE terminal
    if _has_env("CURSOR_TRACE_ID", "CURSOR_SESSION_ID") or _scan_process_tree(["cursor"]):
        return "cursor", SUPERVISOR_COMMANDS["cursor"]

    # 4. Windsurf IDE
    if _has_env("WINDSURF_SESSION_ID") or _scan_process_tree(["windsurf"]):
        return "windsurf", SUPERVISOR_COMMANDS["windsurf"]

    # 5. Aider running the bridge directly (uncommon but possible)
    if _scan_process_tree(["aider"]):
        return "aider", SUPERVISOR_COMMANDS["aider"]

    # 6. Fallback: check available CLIs + API keys
    if _has_env("ANTHROPIC_API_KEY") and _cli_available("claude"):
        return "claude", SUPERVISOR_COMMANDS["claude"]

    if _has_env("OPENAI_API_KEY") and (_cli_available("codex") or _cli_available("codex.cmd")):
        return "codex", SUPERVISOR_COMMANDS["codex"]

    # 7. Unknown driver — fall back to interactive
    return "unknown", SUPERVISOR_COMMANDS["unknown"]


def detect_driver_info() -> dict:
    """Return a dict with full detection metadata for logging."""
    driver, command = detect_driver()

    return {
        "driver":           driver,
        "supervisor":       command,
        "env_claudecode":   bool(_env("CLAUDECODE")),
        "env_openai_key":   bool(_env("OPENAI_API_KEY")),
        "env_anthropic_key":bool(_env("ANTHROPIC_API_KEY")),
        "cli_claude":       _cli_available("claude"),
        "cli_codex":        _cli_available("codex") or _cli_available("codex.cmd"),
        "cli_cursor":       _cli_available("cursor"),
        "cli_windsurf":     _cli_available("windsurf"),
        "parent_process":   _parent_process_name(),
    }


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    info = detect_driver_info()
    print(f"\nDetected driver : {info['driver']}")
    print(f"Supervisor cmd  : {info['supervisor']}")
    print(f"\nDetection signals:")
    for k, v in info.items():
        if k not in ("driver", "supervisor"):
            print(f"  {k:<22} = {v}")
