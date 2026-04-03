from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_cache = None
_cache_time = 0

def check_all():
    global _cache, _cache_time
    import time

    if _cache and (time.time() - _cache_time < 5):
        return _cache

    _cache = {
        "python": check_python(),
        "aider": check_aider(),
        "ollama": check_ollama(),
        "gpu": check_gpu(),
        "codex": check_codex(),
        "claude": check_claude(),
    }
    _cache_time = time.time()
    return _cache


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
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW
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
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW
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


def check_gpu() -> dict:
    """Detect GPU availability and whether Ollama is using it."""
    _flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    # 1. Detect NVIDIA GPU
    gpu_name = ""
    vram_total = 0
    vram_used = 0
    gpu_util = 0
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=_flags,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("\n")[0].split(",")
            gpu_name = parts[0].strip()
            vram_total = round(float(parts[1].strip()) / 1024, 1)  # GB
            vram_used = round(float(parts[2].strip()) / 1024, 1)
            gpu_util = int(parts[3].strip())
    except Exception:
        pass

    has_gpu = bool(gpu_name)

    # 2. Check if Ollama is using GPU (check running models)
    ollama_gpu = False
    ollama_backend = "unknown"
    running_model = ""
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True, text=True, timeout=5, creationflags=_flags,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) > 1:
                # Header: NAME ID SIZE PROCESSOR UNTIL
                row = lines[1]
                parts = row.split()
                running_model = parts[0] if parts else ""
                # The PROCESSOR column shows "100% GPU" or "100% CPU"
                row_lower = row.lower()
                if "gpu" in row_lower:
                    ollama_gpu = True
                    ollama_backend = "GPU"
                elif "cpu" in row_lower:
                    ollama_backend = "CPU"
                else:
                    ollama_backend = "unknown"
            else:
                ollama_backend = "no model loaded"
    except Exception:
        pass

    # 3. Build status and hints
    if not has_gpu:
        status = "cpu_only"
        hint = (
            "No NVIDIA GPU detected. Ollama will run on CPU which is very slow "
            "(5-20x slower than GPU). For usable performance, you need an NVIDIA GPU "
            "with at least 6GB VRAM. AMD GPUs are not yet supported by Ollama on Windows."
        )
    elif has_gpu and ollama_backend == "CPU":
        status = "gpu_available_not_used"
        hint = (
            f"GPU detected ({gpu_name}, {vram_total}GB VRAM) but Ollama is running on CPU. "
            "This makes tasks extremely slow. To fix:\n"
            "1. Restart Ollama: close it from system tray, then reopen\n"
            "2. Check NVIDIA drivers: run 'nvidia-smi' in terminal\n"
            "3. Reinstall Ollama — it auto-detects CUDA on install\n"
            "4. Make sure model fits in VRAM (14B needs ~10GB, 7B needs ~5GB)"
        )
    elif has_gpu and ollama_gpu:
        status = "gpu_active"
        hint = None
    elif has_gpu and ollama_backend == "no model loaded":
        status = "gpu_ready"
        hint = "GPU available. Ollama will use it when a model is loaded."
    else:
        status = "gpu_available"
        hint = f"GPU detected ({gpu_name}). Run a task to verify GPU acceleration."

    return {
        "has_gpu": has_gpu,
        "gpu_name": gpu_name,
        "vram_total_gb": vram_total,
        "vram_used_gb": vram_used,
        "gpu_utilization": gpu_util,
        "ollama_backend": ollama_backend,
        "ollama_gpu": ollama_gpu,
        "running_model": running_model,
        "status": status,
        "hint": hint,
    }


def check_gpu_processes() -> list[dict]:
    """List all processes using the GPU with PID, name, and memory."""
    _flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    processes = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=_flags,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    pid = int(parts[0]) if parts[0].isdigit() else 0
                    name = parts[1]
                    mem = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0
                    short = name.split("\\")[-1].split("/")[-1]
                    processes.append({"pid": pid, "name": short, "path": name, "memory_mb": mem})
    except Exception:
        pass

    # Also get graphics processes (C+G type)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, creationflags=_flags,
        )
        # Use pmon for more complete process list
        result2 = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "m"],
            capture_output=True, text=True, timeout=5, creationflags=_flags,
        )
        if result2.returncode == 0:
            seen_pids = {p["pid"] for p in processes}
            for line in result2.stdout.strip().splitlines():
                if line.startswith("#") or line.startswith("="):
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        continue
                    if pid in seen_pids or pid == 0:
                        continue
                    mem = 0
                    try:
                        mem = int(parts[3]) if parts[3] != "-" else 0
                    except (ValueError, IndexError):
                        pass
                    # Get process name from pid
                    pname = _get_process_name(pid, _flags)
                    if pname:
                        processes.append({"pid": pid, "name": pname, "path": "", "memory_mb": mem})
                        seen_pids.add(pid)
    except Exception:
        pass

    # Sort by memory usage descending
    processes.sort(key=lambda p: p.get("memory_mb", 0), reverse=True)
    return processes


def _get_process_name(pid: int, flags: int) -> str:
    """Get process name from PID."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3, creationflags=flags,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                if parts:
                    return parts[0].strip('"')
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
    except Exception:
        pass
    return f"PID {pid}"


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
