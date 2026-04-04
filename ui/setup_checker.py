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


def check_preflight(settings: dict) -> dict:
    """Run pre-launch validation checks. Returns checklist + can_launch flag."""
    import shutil as _shutil
    _flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    checks = []

    repo_root = settings.get("repo_root", "").strip()
    model = settings.get("aider_model", "").strip()

    # 1. Ollama running (critical)
    ollama = check_ollama()
    checks.append({
        "name": "Ollama running",
        "status": "pass" if ollama.get("installed") and ollama.get("models") is not None else "fail",
        "critical": True,
        "message": "Ollama is running" if ollama.get("installed") else (ollama.get("hint") or "Ollama not found"),
    })

    # 2. Model on GPU (warning)
    gpu = check_gpu()
    if gpu.get("has_gpu"):
        gpu_ok = gpu.get("status") in ("gpu_active", "gpu_ready", "gpu_available")
        checks.append({
            "name": "GPU acceleration",
            "status": "pass" if gpu_ok else "warn",
            "critical": False,
            "message": f"GPU: {gpu.get('gpu_name', '?')}" if gpu_ok else "GPU available but Ollama may be using CPU — tasks will be very slow",
        })
    else:
        checks.append({
            "name": "GPU acceleration",
            "status": "warn",
            "critical": False,
            "message": "No GPU detected — running on CPU will be very slow",
        })

    # 3. Model fits VRAM (warning)
    if gpu.get("has_gpu") and model:
        try:
            from utils.model_advisor import MODELS
            bare = model.replace("ollama/", "")
            match = next((m for m in MODELS if bare in m.name), None)
            if match and gpu.get("vram_total_gb", 0) < match.min_vram_gb:
                checks.append({
                    "name": "Model fits VRAM",
                    "status": "warn",
                    "critical": False,
                    "message": f"{bare} needs {match.min_vram_gb}GB VRAM but GPU has {gpu.get('vram_total_gb')}GB — will fall back to CPU",
                })
            elif match:
                checks.append({
                    "name": "Model fits VRAM",
                    "status": "pass",
                    "critical": False,
                    "message": f"{bare} fits in {gpu.get('vram_total_gb')}GB VRAM",
                })
        except Exception:
            pass

    # 4. Git repo (warning)
    if repo_root:
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root,
                               capture_output=True, text=True, timeout=5, creationflags=_flags)
            dirty = len(r.stdout.strip().splitlines()) if r.returncode == 0 else 0
            checks.append({
                "name": "Git working tree",
                "status": "pass" if dirty == 0 else "warn",
                "critical": False,
                "message": "Clean working tree" if dirty == 0 else f"{dirty} uncommitted changes",
            })
        except Exception:
            checks.append({"name": "Git working tree", "status": "warn", "critical": False, "message": "Could not check git status"})

    # 5. Disk space (warning)
    if repo_root:
        try:
            usage = _shutil.disk_usage(repo_root)
            free_gb = round(usage.free / (1024 ** 3), 1)
            checks.append({
                "name": "Disk space",
                "status": "pass" if free_gb > 1 else "warn",
                "critical": False,
                "message": f"{free_gb} GB free" if free_gb > 1 else f"Low disk space: {free_gb} GB",
            })
        except Exception:
            pass

    # 6. Aider installed (critical)
    aider = check_aider()
    checks.append({
        "name": "Aider installed",
        "status": "pass" if aider.get("installed") else "fail",
        "critical": True,
        "message": f"Aider {aider.get('version', '')}" if aider.get("installed") else (aider.get("hint") or "Aider not found"),
    })

    can_launch = all(c["status"] != "fail" for c in checks if c.get("critical"))

    # 7. Plan file validation (warning)
    task_count = 0
    plan_file = settings.get("plan_file", "").strip()
    _valid_types = {"create", "modify", "delete", "validate", "read", "investigate"}
    if plan_file:
        try:
            import json as _json
            pd = _json.loads(Path(plan_file).read_text(encoding="utf-8"))
            plan_tasks = pd.get("tasks", [])
            task_count = len(plan_tasks)

            # Validate each task
            missing_files = []
            bad_types = []
            dup_ids = []
            seen_ids = set()
            for pt in plan_tasks:
                tid = pt.get("id", "?")
                if tid in seen_ids:
                    dup_ids.append(tid)
                seen_ids.add(tid)

                ttype = pt.get("type", "")
                if ttype and ttype not in _valid_types:
                    bad_types.append(f"task {tid}: {ttype}")

                if ttype not in ("create",) and repo_root:
                    for fp in pt.get("files", []):
                        if not (Path(repo_root) / fp).exists():
                            missing_files.append(fp)

            if missing_files:
                checks.append({
                    "name": "Plan files exist",
                    "status": "warn",
                    "critical": False,
                    "message": f"{len(missing_files)} file(s) not found: {', '.join(missing_files[:3])}",
                })
            if bad_types:
                checks.append({
                    "name": "Plan task types",
                    "status": "warn",
                    "critical": False,
                    "message": f"Invalid types: {', '.join(bad_types[:3])}",
                })
            if dup_ids:
                checks.append({
                    "name": "Plan task IDs",
                    "status": "warn",
                    "critical": False,
                    "message": f"Duplicate IDs: {dup_ids}",
                })
            if task_count > 0 and not missing_files and not bad_types:
                checks.append({
                    "name": "Plan validated",
                    "status": "pass",
                    "critical": False,
                    "message": f"{task_count} tasks, all files exist",
                })
        except Exception:
            pass

    # Cost estimate

    speed_tier_seconds = {"fast": 90, "medium": 150, "slow": 300}
    model_speed = "medium"
    if model:
        try:
            from utils.model_advisor import MODELS
            bare = model.replace("ollama/", "")
            match = next((m for m in MODELS if bare in m.name), None)
            if match:
                model_speed = match.speed
        except Exception:
            pass

    est_seconds = task_count * speed_tier_seconds.get(model_speed, 150)
    est_minutes = round(est_seconds / 60, 1)
    est_supervisor_tokens = task_count * 2000
    est_aider_tokens = task_count * 3000

    estimate = {
        "task_count": task_count,
        "estimated_minutes": est_minutes,
        "estimated_supervisor_tokens": est_supervisor_tokens,
        "estimated_aider_tokens": est_aider_tokens,
        "model_speed": model_speed,
    }

    return {"checks": checks, "can_launch": can_launch, "estimate": estimate}


def validate_config(settings: dict) -> list[dict]:
    """Validate all settings and return issues."""
    issues = []

    repo = settings.get("repo_root", "").strip()
    if not repo:
        issues.append({"field": "repo_root", "level": "error", "message": "Project folder not set"})
    elif not Path(repo).is_dir():
        issues.append({"field": "repo_root", "level": "error", "message": f"Directory not found: {repo}"})
    elif not (Path(repo) / ".git").exists():
        issues.append({"field": "repo_root", "level": "warn", "message": "Not a git repository — bridge requires git"})

    model = settings.get("aider_model", "").strip()
    if not model:
        issues.append({"field": "aider_model", "level": "warn", "message": "No model set — using default"})
    elif model.startswith("ollama/"):
        ollama = check_ollama()
        if ollama.get("models"):
            bare = model[7:]
            found = any(bare in m for m in ollama["models"])
            if not found:
                issues.append({"field": "aider_model", "level": "warn", "message": f"Model {bare} not pulled in Ollama"})

    timeout = settings.get("task_timeout", 600)
    if isinstance(timeout, (int, float)):
        if timeout < 60:
            issues.append({"field": "task_timeout", "level": "warn", "message": "Timeout under 60s — tasks may fail prematurely"})
        elif timeout > 3600:
            issues.append({"field": "task_timeout", "level": "warn", "message": "Timeout over 1 hour — consider reducing"})

    return issues


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
