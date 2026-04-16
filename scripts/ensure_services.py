"""
ensure_services.py
==================
Auto-start script called by Claude Code's session hook.

Checks and boots (in order):
  1. Qdrant vector DB      → downloads binary if missing, starts on port 6333
  2. nomic-embed-text      → ollama pull if not present
  3. bridge-memory-service → npm install if needed, starts on port 3000

Run manually:   python scripts/ensure_services.py
Auto-run via:   .claude/settings.json PreToolUse hook
"""

import os
import sys
import time
import json
import shutil
import platform
import zipfile
import urllib.request
import subprocess
import pathlib

ROOT = pathlib.Path(__file__).parent.parent          # bridge repo root
MEMORY_SVC_DIR = ROOT / "services" / "memory-service"
QDRANT_DIR     = ROOT / "services" / "qdrant"
QDRANT_LOG     = QDRANT_DIR / "qdrant.log"
MEMORY_LOG     = ROOT / "services" / "memory-service.log"

IS_WIN = platform.system() == "Windows"
QDRANT_EXE = QDRANT_DIR / ("qdrant.exe" if IS_WIN else "qdrant")

# ── helpers ──────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[ensure_services] {msg}", flush=True)

def port_open(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0

def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 500
    except Exception:
        return False

def run_bg(cmd: list, cwd: pathlib.Path, log_file: pathlib.Path) -> subprocess.Popen:
    """Start a process in background, redirect stdout/stderr to log_file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as lf:
        return subprocess.Popen(
            cmd, cwd=str(cwd), stdout=lf, stderr=lf,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
        )

def wait_for_port(port: int, timeout: int = 30) -> bool:
    for _ in range(timeout):
        if port_open(port):
            return True
        time.sleep(1)
    return False

# ── 1. Qdrant ─────────────────────────────────────────────────────────────────

def ensure_qdrant():
    if http_ok("http://localhost:6333/health") or http_ok("http://localhost:6333/"):
        log("Qdrant already running on :6333")
        return

    # Download binary if missing
    if not QDRANT_EXE.exists():
        log("Qdrant binary not found — downloading …")
        QDRANT_DIR.mkdir(parents=True, exist_ok=True)

        # Detect arch + OS
        machine = platform.machine().lower()
        if IS_WIN:
            triple = "x86_64-pc-windows-msvc"
        elif sys.platform == "darwin":
            triple = "aarch64-apple-darwin" if "arm" in machine else "x86_64-apple-darwin"
        else:
            triple = "aarch64-unknown-linux-musl" if "arm" in machine else "x86_64-unknown-linux-musl"

        zip_name = f"qdrant-{triple}.zip"
        api_url  = "https://api.github.com/repos/qdrant/qdrant/releases/latest"

        try:
            with urllib.request.urlopen(api_url, timeout=15) as r:
                release = json.loads(r.read())
            asset = next(a for a in release["assets"] if a["name"] == zip_name)
            download_url = asset["browser_download_url"]
        except Exception as e:
            log(f"ERROR: Could not fetch Qdrant release info: {e}")
            log("       Start Qdrant manually or set ENABLE_VECTOR=false in services/memory-service/.env")
            return

        zip_path = QDRANT_DIR / zip_name
        log(f"Downloading {download_url} …")
        urllib.request.urlretrieve(download_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(QDRANT_DIR)
        zip_path.unlink()

        if not IS_WIN:
            QDRANT_EXE.chmod(0o755)

        log(f"Qdrant downloaded → {QDRANT_EXE}")

    log("Starting Qdrant …")
    run_bg([str(QDRANT_EXE)], cwd=QDRANT_DIR, log_file=QDRANT_LOG)

    if wait_for_port(6333, timeout=20):
        log("Qdrant started on :6333")
    else:
        log("WARNING: Qdrant did not come up in 20s — memory service will run in SQLite-only mode")

# ── 2. nomic-embed-text ───────────────────────────────────────────────────────

def ensure_embed_model():
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        if "nomic-embed-text" in result.stdout:
            log("nomic-embed-text already present in Ollama")
            return
        log("Pulling nomic-embed-text (one-time, ~274MB) …")
        subprocess.run(["ollama", "pull", "nomic-embed-text"], check=True, timeout=300)
        log("nomic-embed-text ready")
    except FileNotFoundError:
        log("WARNING: ollama not found in PATH — skipping embed model check")
    except subprocess.CalledProcessError as e:
        log(f"WARNING: ollama pull failed: {e}")

# ── 3. bridge-memory-service ─────────────────────────────────────────────────

def ensure_memory_service():
    if http_ok("http://localhost:3000/health"):
        log("Memory service already running on :3000")
        return

    if not MEMORY_SVC_DIR.exists():
        log("ERROR: services/memory-service not found — did you clone with --recurse-submodules?")
        log("       Run: git submodule update --init --recursive")
        return

    # npm install if node_modules missing
    nm = MEMORY_SVC_DIR / "node_modules"
    if not nm.exists():
        log("Running npm install in services/memory-service …")
        result = subprocess.run(
            ["npm", "install"], cwd=str(MEMORY_SVC_DIR),
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log(f"ERROR: npm install failed:\n{result.stderr[-500:]}")
            return
        log("npm install complete")

    # Build if dist missing
    dist = MEMORY_SVC_DIR / "dist"
    if not dist.exists():
        log("Building memory service (npm run build) …")
        result = subprocess.run(
            ["npm", "run", "build"], cwd=str(MEMORY_SVC_DIR),
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            log(f"ERROR: npm run build failed:\n{result.stderr[-500:]}")
            return

    # Write .env if missing (SQLite-only fallback if Qdrant not available)
    env_file = MEMORY_SVC_DIR / ".env"
    if not env_file.exists():
        qdrant_up = http_ok("http://localhost:6333/health") or http_ok("http://localhost:6333/")
        env_content = "PORT=3000\n"
        if not qdrant_up:
            env_content += "ENABLE_VECTOR=false\n"
            log("Qdrant not available — writing ENABLE_VECTOR=false to .env")
        env_file.write_text(env_content)

    log("Starting bridge-memory-service …")
    npm_cmd = ["npm.cmd", "start"] if IS_WIN else ["npm", "start"]
    run_bg(npm_cmd, cwd=MEMORY_SVC_DIR, log_file=MEMORY_LOG)

    if wait_for_port(3000, timeout=30):
        log("Memory service started on :3000")
    else:
        log("WARNING: Memory service did not come up in 30s — bridge will run without memory context")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log(f"Bridge root: {ROOT}")
    ensure_qdrant()
    ensure_embed_model()
    ensure_memory_service()
    log("Done.")

if __name__ == "__main__":
    main()
