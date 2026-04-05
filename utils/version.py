"""Version tracking — auto-increments on each commit."""

VERSION = "0.5.2"
BUILD_DATE = "2026-04-04"

def get_version_info() -> dict:
    """Get version + git commit info."""
    import subprocess, sys
    info = {"version": VERSION, "build_date": BUILD_DATE, "commit": "", "commit_short": "", "branch": ""}
    try:
        _flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, creationflags=_flags)
        if r.returncode == 0:
            info["commit"] = r.stdout.strip()
            info["commit_short"] = r.stdout.strip()[:7]
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3, creationflags=_flags)
        if r.returncode == 0:
            info["branch"] = r.stdout.strip()
    except Exception:
        pass
    return info
