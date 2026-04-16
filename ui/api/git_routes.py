"""Git API blueprint — status, branches, checkout, log, diff, gitignore.

Extracted from ui/app.py for maintainability.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from ui import state_store

_WIN_CREATE_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

git_bp = Blueprint("git", __name__)


def _git(repo_root: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a git command in repo_root."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_WIN_CREATE_FLAGS,
    )


def _get_repo(req_data=None) -> str:
    """Get repo_root from request args, JSON body, or settings."""
    if req_data:
        repo = (req_data.get("repo_root") or "").strip()
    else:
        repo = (request.args.get("repo_root") or "").strip()
    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    return repo


@git_bp.route("/api/git/status")
def api_git_status():
    repo = _get_repo()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        r = _git(repo, "branch", "--show-current")
        branch = r.stdout.strip() or "(detached)"

        r = _git(repo, "status", "--porcelain")
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        staged = sum(1 for l in lines if l[0] in "MADRC")
        unstaged = sum(1 for l in lines if len(l) > 1 and l[1] in "MADRC")
        untracked = sum(1 for l in lines if l.startswith("??"))
        is_clean = len(lines) == 0

        return jsonify({
            "branch": branch,
            "is_clean": is_clean,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        })
    except FileNotFoundError:
        return jsonify({"error": "git not found"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "git command timed out"}), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/git/branches")
def api_git_branches():
    repo = _get_repo()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        r = _git(repo, "branch", "--no-color")
        branches = []
        current = ""
        for line in r.stdout.splitlines():
            name = line.lstrip("* ").strip()
            if not name:
                continue
            branches.append(name)
            if line.startswith("*"):
                current = name
        return jsonify({"current": current, "branches": branches})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/git/checkout", methods=["POST"])
def api_git_checkout():
    data = request.json or {}
    repo = _get_repo(data)
    branch = (data.get("branch") or "").strip()
    create = data.get("create", False)

    if not repo or not branch:
        return jsonify({"error": "repo_root and branch are required"}), 400

    if ".." in branch or branch.startswith("-"):
        return jsonify({"error": "Invalid branch name"}), 400

    try:
        if create:
            r = _git(repo, "checkout", "-b", branch)
        else:
            r = _git(repo, "checkout", branch)

        if r.returncode != 0:
            return jsonify({"error": r.stderr.strip() or f"Failed to checkout {branch}"}), 400

        return jsonify({"ok": True, "branch": branch})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/git/log")
def api_git_log():
    repo = _get_repo()
    limit = request.args.get("limit", 20, type=int)
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        r = _git(repo, "log", f"--max-count={min(limit, 100)}",
                 "--format=%H%n%h%n%s%n%an%n%aI", "--no-color")
        if r.returncode != 0:
            return jsonify({"commits": []})

        lines = r.stdout.strip().splitlines()
        commits = []
        for i in range(0, len(lines), 5):
            if i + 4 >= len(lines):
                break
            commits.append({
                "sha": lines[i],
                "short_sha": lines[i + 1],
                "message": lines[i + 2],
                "author": lines[i + 3],
                "timestamp": lines[i + 4],
                "is_bridge_task": lines[i + 2].startswith("bridge: task"),
            })

        return jsonify({"commits": commits})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/git/diff")
def api_git_diff():
    repo = _get_repo()
    sha = (request.args.get("sha") or "").strip()
    staged = request.args.get("staged", "false").lower() == "true"
    file_path = (request.args.get("file") or "").strip()

    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        if sha:
            r = _git(repo, "diff", f"{sha}~1..{sha}", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff", f"{sha}~1..{sha}", timeout=30)
            diff_text = r.stdout[:8000]
        elif staged:
            r = _git(repo, "diff", "--cached", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff", "--cached")
            diff_text = r.stdout[:8000]
        elif file_path:
            r = _git(repo, "diff", "--", file_path)
            stat = ""
            diff_text = r.stdout[:8000]
        else:
            r = _git(repo, "diff", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff")
            diff_text = r.stdout[:8000]

        files = []
        for line in stat.splitlines():
            parts = line.strip().split("|")
            if len(parts) == 2:
                fname = parts[0].strip()
                changes = parts[1].strip()
                ins = changes.count("+")
                dels = changes.count("-")
                files.append({"path": fname, "insertions": ins, "deletions": dels})

        return jsonify({"diff": diff_text, "files": files, "stat": stat})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/git/gitignore", methods=["POST"])
def api_git_gitignore():
    """Add a pattern to .gitignore."""
    data = request.json or {}
    repo = _get_repo(data)
    pattern = (data.get("pattern") or "").strip()

    if not repo or not pattern:
        return jsonify({"error": "repo_root and pattern are required"}), 400

    gitignore_path = Path(repo) / ".gitignore"
    try:
        existing = ""
        if gitignore_path.exists():
            existing = gitignore_path.read_text(encoding="utf-8")

        lines = existing.splitlines()
        if pattern in lines:
            return jsonify({"ok": True, "message": "Already in .gitignore"})

        separator = "\n" if existing and not existing.endswith("\n") else ""
        gitignore_path.write_text(existing + separator + pattern + "\n", encoding="utf-8")
        return jsonify({"ok": True, "pattern": pattern})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@git_bp.route("/api/vscode/open", methods=["POST"])
def api_vscode_open():
    """Open a file or folder in VS Code."""
    data = request.json or {}
    target = (data.get("path") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()

    if not target and not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()

    open_path = target or repo_root
    if not open_path:
        return jsonify({"error": "No path specified"}), 400

    p = Path(open_path)
    if not p.is_absolute() and repo_root:
        p = Path(repo_root) / p

    try:
        cmd = ["code"]
        if p.is_file():
            cmd.append("--goto")
        cmd.append(str(p))
        subprocess.Popen(cmd, creationflags=_WIN_CREATE_FLAGS)
        return jsonify({"ok": True, "path": str(p)})
    except FileNotFoundError:
        return jsonify({"error": "VS Code ('code' command) not found."}), 404
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
