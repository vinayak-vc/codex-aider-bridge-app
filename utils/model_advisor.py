"""Model Advisor — recommends the best Ollama coding model based on system specs.

Detects: RAM, VRAM (NVIDIA GPU), CPU cores, disk space
Recommends: the largest model the system can run smoothly
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ── Model database (sorted by quality, best first) ────────────────────────────

@dataclass
class ModelOption:
    name: str           # ollama model name
    param_size: str     # human label: "32B", "14B", etc.
    min_ram_gb: float   # minimum RAM to run
    min_vram_gb: float  # minimum VRAM for full GPU offload (0 = CPU-only OK)
    disk_gb: float      # approximate download size
    quality: int        # 1-10 scale (10 = best coding quality)
    speed: str          # "fast", "medium", "slow"
    description: str


MODELS = [
    ModelOption("ollama/qwen2.5-coder:32b", "32B", 24, 20, 18, 10, "slow",
               "Best coding quality. Needs beefy GPU (24GB+ VRAM) or 32GB+ RAM for CPU."),
    ModelOption("ollama/deepseek-coder-v2", "16B", 16, 12, 9, 9, "medium",
               "Excellent coder. Good balance of quality and speed."),
    ModelOption("ollama/qwen2.5-coder:14b", "14B", 12, 10, 8, 8, "medium",
               "Strong coding model. Recommended for most systems with 16GB RAM or 10GB+ VRAM."),
    ModelOption("ollama/codellama:13b", "13B", 12, 10, 7, 7, "medium",
               "Meta's code model. Solid for general coding tasks."),
    ModelOption("ollama/qwen2.5-coder:7b", "7B", 8, 5, 4, 6, "fast",
               "Good for systems with limited resources. Fast but less accurate on complex tasks."),
    ModelOption("ollama/deepseek-coder:6.7b", "6.7B", 8, 5, 4, 6, "fast",
               "Compact coder. Fast responses, good for simple tasks."),
    ModelOption("ollama/codellama:7b", "7B", 8, 5, 4, 5, "fast",
               "Lightweight Meta code model. Fast but basic."),
    ModelOption("ollama/qwen2.5-coder:3b", "3B", 4, 2, 2, 4, "fast",
               "Minimal resource usage. For very constrained systems. Limited accuracy."),
    ModelOption("ollama/qwen2.5-coder:1.5b", "1.5B", 2, 1, 1, 2, "fast",
               "Ultra-lightweight. Last resort — will struggle with multi-file tasks."),
    ModelOption("ollama/mistral", "7B", 8, 5, 4, 5, "fast",
               "General-purpose model. Decent at code but not specialized."),
]


# ── System detection ──────────────────────────────────────────────────────────

@dataclass
class SystemSpecs:
    os: str
    cpu: str
    cpu_cores: int
    ram_gb: float
    gpu_name: str
    vram_gb: float
    disk_free_gb: float
    ollama_installed: bool
    installed_models: list[str]


def detect_system() -> SystemSpecs:
    """Detect system hardware specs."""
    import shutil

    # OS + CPU
    os_name = f"{platform.system()} {platform.release()}"
    cpu = platform.processor() or "unknown"
    cpu_cores = os.cpu_count() or 1

    # RAM
    ram_gb = _get_ram_gb()

    # GPU (NVIDIA only for now)
    gpu_name, vram_gb = _get_nvidia_gpu()

    # Disk
    try:
        usage = shutil.disk_usage(os.getcwd())
        disk_free_gb = round(usage.free / (1024 ** 3), 1)
    except Exception:
        disk_free_gb = 0

    # Ollama
    ollama_installed = shutil.which("ollama") is not None
    installed_models = _get_ollama_models() if ollama_installed else []

    return SystemSpecs(
        os=os_name,
        cpu=cpu,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        disk_free_gb=disk_free_gb,
        ollama_installed=ollama_installed,
        installed_models=installed_models,
    )


def _get_ram_gb() -> float:
    """Get total system RAM in GB."""
    try:
        if sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return round(int(line.split()[1]) / (1024 ** 2), 1)
    except Exception:
        pass
    return 0


def _get_nvidia_gpu() -> tuple[str, float]:
    """Detect NVIDIA GPU name and VRAM."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0]
            parts = line.split(",")
            name = parts[0].strip()
            vram_mb = float(parts[1].strip())
            return name, round(vram_mb / 1024, 1)
    except Exception:
        pass
    return "", 0


def _get_ollama_models() -> list[str]:
    """List installed Ollama models."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
            creationflags=_WIN_NO_WINDOW,
        )
        models = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                models.append(parts[0])
        return models
    except Exception:
        return []


# ── Recommendation engine ────────────────────────────────────────────────────

def recommend(specs: Optional[SystemSpecs] = None) -> dict:
    """Recommend the best Ollama model for this system.

    Returns a dict with:
      system: detected specs
      recommended: best model for this system
      alternatives: other viable options
      all_models: full model database with compatibility status
    """
    if specs is None:
        specs = detect_system()

    effective_mem = max(specs.ram_gb, specs.vram_gb * 1.5)  # VRAM is more effective
    compatible = []
    incompatible = []

    for m in MODELS:
        can_run = specs.ram_gb >= m.min_ram_gb or specs.vram_gb >= m.min_vram_gb
        has_disk = specs.disk_free_gb >= m.disk_gb
        installed = any(m.name.replace("ollama/", "") in im for im in specs.installed_models)

        status = {
            "name": m.name,
            "param_size": m.param_size,
            "quality": m.quality,
            "speed": m.speed,
            "description": m.description,
            "min_ram_gb": m.min_ram_gb,
            "min_vram_gb": m.min_vram_gb,
            "disk_gb": m.disk_gb,
            "can_run": can_run,
            "has_disk_space": has_disk,
            "installed": installed,
            "gpu_accelerated": specs.vram_gb >= m.min_vram_gb,
        }

        if can_run and has_disk:
            compatible.append(status)
        else:
            status["reason"] = []
            if not can_run:
                status["reason"].append(f"Needs {m.min_ram_gb}GB RAM or {m.min_vram_gb}GB VRAM")
            if not has_disk:
                status["reason"].append(f"Needs {m.disk_gb}GB disk space")
            incompatible.append(status)

    # Best recommendation: prefer GPU-accelerated, then highest quality
    gpu_models = [m for m in compatible if m["gpu_accelerated"]]
    recommended = gpu_models[0] if gpu_models else (compatible[0] if compatible else None)

    # Alternatives: next 2 best options (excluding the recommended one)
    alternatives = [m for m in compatible if m["name"] != (recommended or {}).get("name")][:2]

    return {
        "system": {
            "os": specs.os,
            "cpu": specs.cpu,
            "cpu_cores": specs.cpu_cores,
            "ram_gb": specs.ram_gb,
            "gpu_name": specs.gpu_name,
            "vram_gb": specs.vram_gb,
            "disk_free_gb": specs.disk_free_gb,
            "ollama_installed": specs.ollama_installed,
            "installed_models": specs.installed_models,
        },
        "recommended": recommended,
        "alternatives": alternatives,
        "compatible_count": len(compatible),
        "all_models": compatible + incompatible,
    }
