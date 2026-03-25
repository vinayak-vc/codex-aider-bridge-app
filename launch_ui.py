"""launch_ui.py — Start the Codex-Aider Bridge web UI.

Usage:
    python launch_ui.py              # opens browser automatically
    python launch_ui.py --no-browser # skip opening browser
    python launch_ui.py --port 8080  # use a different port (default 7823)

When bundled as a PyInstaller exe, bridge_runner re-invokes this exe with
--_bridge-run prepended.  In that mode we bypass the UI and run the bridge
CLI (main.main()) directly, using the remaining argv as CLI arguments.
"""
from __future__ import annotations

import sys

# ── PyInstaller bridge-subprocess mode ────────────────────────────────────────
# Must be checked before any other imports so the bridge process starts cleanly
# without importing Flask or tkinter.
if "--_bridge-run" in sys.argv:
    sys.argv.remove("--_bridge-run")
    # sys.argv[0] is the exe / script; sys.argv[1:] are the bridge CLI args.
    # main.main() calls argparse.parse_args() which reads sys.argv[1:].
    from main import main as _bridge_main  # noqa: E402
    sys.exit(_bridge_main())
# ──────────────────────────────────────────────────────────────────────────────

import argparse
import subprocess
import threading
import time
import webbrowser
from pathlib import Path


def _ensure_flask() -> None:
    """Install Flask if it's not already available."""
    try:
        import flask  # noqa: F401
    except ImportError:
        print("[launch_ui] Flask not found — installing…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "flask>=3.0"]
        )
        print("[launch_ui] Flask installed.")


def _open_browser(url: str, delay: float = 1.5) -> None:
    """Open the browser after a short delay to let Flask start."""
    time.sleep(delay)
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the Codex-Aider Bridge UI")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    parser.add_argument("--port", type=int, default=7823, help="Port to listen on (default 7823)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default 127.0.0.1)")
    args = parser.parse_args()

    _ensure_flask()

    # Must import after ensuring Flask is installed
    from ui.app import app  # type: ignore[import]

    url = f"http://{args.host}:{args.port}"
    print(f"[launch_ui] Starting UI at {url}")
    print("[launch_ui] Press Ctrl+C to stop.")

    if not args.no_browser:
        t = threading.Thread(target=_open_browser, args=(url,), daemon=True)
        t.start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
