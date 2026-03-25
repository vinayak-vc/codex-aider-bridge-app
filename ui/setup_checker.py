from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def check_all() -> dict:
    return {
        "python": check_python(),
        "aider": check_aider(),
        "ollama": check_ollama(),
        "codex": check_codex(),
        "claude": check_claude(),
    }


def check_python() -> dict:
    return {
        "installed": True,
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "path": sys.executable,
        "ok": sys.version_info >= (3, 10),
        "hint": None if sys.version_info >= (3, 10) else "Python 3.10+ required. Please upgrade.",
    }


def check_aider() -> dict:
    path = shutil.which("aider")
    if not path:
        # Search common virtual-environment script directories
        for venv_dir in [".venv/Scripts", "venv/Scripts", "aider-env/Scripts"]:
            for ext in ("", ".exe", ".cmd"):
                candidate = Path(venv_dir) / f"aider{ext}"
                if candidate.exists():
                    path = str(candidate)
                    break
            if path:
                break

    if not path:
        return {
            "installed": False,
            "version": None,
            "path": None,
            "hint": "Run: pip install aider-chat",
        }

    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=8, encoding="utf-8",
        )
        version = (result.stdout.strip() or result.stderr.strip()).splitlines()[0]
    except Exception:
        version = "unknown"

    return {"installed": True, "version": version, "path": path, "hint": None}


def check_ollama() -> dict:
    path = shutil.which("ollama")
    if not path:
        return {
            "installed": False,
            "models": [],
            "path": None,
            "hint": "Download from https://ollama.com and install, then run: ollama pull mistral",
        }

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
        models: list[str] = []
        for line in result.stdout.splitlines()[1:]:  # skip header row
            parts = line.split()
            if parts:
                models.append(parts[0])
        return {
            "installed": True,
            "models": models,
            "path": path,
            "hint": None if models else "No models found. Run: ollama pull mistral",
        }
    except Exception as ex:
        return {
            "installed": True,
            "models": [],
            "path": path,
            "hint": f"Ollama is installed but could not list models: {ex}. Is Ollama running?",
        }


def check_codex() -> dict:
    path = shutil.which("codex") or shutil.which("codex.cmd")
    return {
        "installed": bool(path),
        "path": path,
        "hint": None if path else "Install Codex CLI from https://github.com/openai/codex",
    }


def check_claude() -> dict:
    path = shutil.which("claude")
    return {
        "installed": bool(path),
        "path": path,
        "hint": None if path else "Install Claude CLI from https://claude.ai/download",
    }
