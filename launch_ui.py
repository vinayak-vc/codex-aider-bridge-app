"""launch_ui.py — Codex-Aider Bridge desktop application entry point.

Modes
-----
Normal   : starts Flask on localhost then opens an embedded pywebview window
           (no external browser, no CMD window)
Headless : --headless  — starts Flask only (for scripting / CI)
Bridge   : --_bridge-run  — runs the bridge CLI directly (used by bridge_runner
           when the app is packaged as a PyInstaller onefile exe)
"""
from __future__ import annotations

import os
import sys

# ── Bridge subprocess mode ─────────────────────────────────────────────────────
# This MUST be the very first check — before any other imports.
# bridge_runner re-invokes the same exe with --_bridge-run prepended so that
# the bridge CLI (main.main) runs without starting Flask or a window.
if "--_bridge-run" in sys.argv:
    sys.argv.remove("--_bridge-run")

    # PyInstaller noconsole (windowed) exe sets sys.stdout / sys.stderr to None.
    # When called via subprocess.PIPE the OS pipe IS connected to fd 1 & 2 —
    # we just need to reattach Python's text wrappers to those fds.
    import io
    if sys.stdout is None:
        try:
            sys.stdout = io.TextIOWrapper(
                os.fdopen(1, "wb"), encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = io.TextIOWrapper(
                os.fdopen(2, "wb"), encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            pass

    from main import main as _bridge_main  # noqa: E402
    sys.exit(_bridge_main())
# ──────────────────────────────────────────────────────────────────────────────

import argparse
import socket
import subprocess
import threading
import time

PORT = 7823
HOST = "127.0.0.1"


def _ensure_deps() -> None:
    """Auto-install Flask and pywebview when running from source (not frozen)."""
    missing: list[str] = []
    try:
        import flask  # noqa: F401
    except ImportError:
        missing.append("flask>=3.0")
    try:
        import webview  # noqa: F401
    except ImportError:
        missing.append("pywebview>=4.0")
    if missing:
        print(f"[bridge] Installing: {', '.join(missing)} …")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _show_error(title: str, message: str) -> None:
    """Native error dialog — works even with no console / no window yet."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        dest = sys.stderr or sys.__stderr__
        if dest:
            print(f"[ERROR] {title}: {message}", file=dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex-Aider Bridge UI")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument(
        "--headless", action="store_true",
        help="Start server only — no window (open the URL in any browser)",
    )
    args = parser.parse_args()

    # Only auto-install when running from plain Python source
    if not getattr(sys, "frozen", False):
        _ensure_deps()

    try:
        from ui.app import app  # noqa: E402
    except Exception as ex:
        _show_error("Startup Error", f"Failed to import the UI package:\n{ex}")
        sys.exit(1)

    url = f"http://{args.host}:{args.port}"

    # ── Start Flask in a background daemon thread ──────────────────────────────
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host=args.host, port=args.port, debug=False, use_reloader=False
        ),
        daemon=True,
    )
    flask_thread.start()

    # ── Headless mode — server only ────────────────────────────────────────────
    if args.headless:
        print(f"[bridge] Running at {url}  (headless — open in browser manually)")
        print("[bridge] Press Ctrl+C to stop.")
        try:
            flask_thread.join()
        except KeyboardInterrupt:
            pass
        return

    # ── Wait for Flask to be ready ─────────────────────────────────────────────
    if not _wait_for_port(args.host, args.port, timeout=15):
        _show_error(
            "Startup Error",
            f"Server did not respond on port {args.port} within 15 seconds.",
        )
        sys.exit(1)

    # ── Open embedded window (pywebview → Edge WebView2 on Windows 10/11) ─────
    try:
        import webview  # noqa: E402  (installed above if needed)

        webview.create_window(
            "Codex-Aider Bridge",
            url,
            width=1280,
            height=860,
            min_size=(960, 640),
            text_select=True,
        )
        webview.start()  # blocks until the window is closed

    except Exception as ex:
        _show_error(
            "Window Error",
            f"Could not open the embedded window:\n{ex}\n\n"
            f"Fallback: re-run with --headless and open {url} in your browser.",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
