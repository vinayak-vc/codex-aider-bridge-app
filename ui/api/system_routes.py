"""System API blueprint — GPU, benchmark, model recommendation, preflight.

Extracted from ui/app.py for maintainability.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time as _time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, jsonify, request

from ui import state_store

_WIN_CREATE_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/run/preflight", methods=["POST"])
def api_run_preflight():
    """Run pre-launch validation checks."""
    from ui import setup_checker
    data = request.json or {}
    result = setup_checker.check_preflight(data)
    return jsonify(result)


@system_bp.route("/api/system/gpu-processes")
def api_gpu_processes():
    """List all processes using the GPU."""
    from ui import setup_checker
    try:
        procs = setup_checker.check_gpu_processes()
        gpu = setup_checker.check_gpu()
        return jsonify({
            "processes": procs,
            "gpu": gpu,
            "total_gpu_mem_mb": sum(p.get("memory_mb", 0) for p in procs),
        })
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@system_bp.route("/api/system/kill-process", methods=["POST"])
def api_kill_process():
    """Kill a process by PID."""
    data = request.json or {}
    pid = data.get("pid", 0)
    if not pid:
        return jsonify({"error": "pid is required"}), 400

    protected = {"explorer.exe", "System", "csrss.exe", "winlogon.exe",
                 "svchost.exe", "lsass.exe", "smss.exe", "services.exe"}

    try:
        import os
        import signal
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
                creationflags=_WIN_CREATE_FLAGS,
            )
            pname = ""
            if result.stdout.strip():
                pname = result.stdout.strip().split(",")[0].strip('"')
            if pname.lower() in {p.lower() for p in protected}:
                return jsonify({"error": f"Cannot kill system process: {pname}"}), 403

            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=5,
                           creationflags=_WIN_CREATE_FLAGS)
        else:
            os.kill(int(pid), signal.SIGTERM)

        return jsonify({"ok": True, "pid": pid})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@system_bp.route("/api/system/unload-model", methods=["POST"])
def api_unload_model():
    """Tell Ollama to unload the current model from VRAM."""
    try:
        settings = state_store.load_settings()
        model = settings.get("aider_model", "").replace("ollama/", "")
        if not model:
            return jsonify({"error": "No model configured"}), 400

        body = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

        return jsonify({"ok": True, "model": model, "message": f"Model {model} unloaded from VRAM"})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@system_bp.route("/api/system/benchmark", methods=["POST"])
def api_benchmark():
    """Benchmark Ollama model speed — measures tokens/second."""
    data = request.json or {}
    settings = state_store.load_settings()
    model = data.get("model") or settings.get("aider_model", "")
    model = model.replace("ollama/", "")
    if not model:
        return jsonify({"error": "No model specified"}), 400

    prompt = "Write a Python function called reverse_string that takes a string and returns it reversed. Handle edge cases like empty strings and None."

    body = json.dumps({
        "model": model, "prompt": prompt,
        "stream": False, "keep_alive": "5s",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = _time.time()
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        elapsed = _time.time() - start

        eval_count = result.get("eval_count", 0)
        eval_duration = result.get("eval_duration", 0)

        if eval_duration > 0:
            tok_per_sec = round(eval_count / (eval_duration / 1e9), 1)
        elif elapsed > 0:
            tok_per_sec = round(eval_count / elapsed, 1)
        else:
            tok_per_sec = 0

        est_task_sec = round(500 / max(1, tok_per_sec))

        return jsonify({
            "model": model,
            "tokens_generated": eval_count,
            "elapsed_seconds": round(elapsed, 1),
            "tokens_per_second": tok_per_sec,
            "estimated_task_seconds": est_task_sec,
            "speed_tier": "fast" if tok_per_sec >= 20 else ("medium" if tok_per_sec >= 5 else "slow"),
        })
    except urllib.error.URLError as exc:
        return jsonify({"error": f"Ollama not reachable: {exc}"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@system_bp.route("/api/system/recommend-model")
def api_recommend_model():
    """Detect system specs and recommend the best Ollama coding model."""
    _root = Path(__file__).parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from utils.model_advisor import recommend
    try:
        result = recommend()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
