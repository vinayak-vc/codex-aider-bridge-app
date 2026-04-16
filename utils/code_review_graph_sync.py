from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_CRG_INSTALL_REF: str = "git+https://github.com/tirth8205/code-review-graph.git"
_CRG_DB_DIRNAME: str = ".code-review-graph"
_CRG_DB_FILENAME: str = "graph.db"
_CRG_SYNC_META_FILENAME: str = "crg_sync_meta.json"


def _run_command(
    args: list[str],
    cwd: Optional[Path],
    timeout_seconds: int,
    extra_env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        creationflags=_WIN_NO_WINDOW,
        env=env,
    )


def ensure_code_review_graph_installed(logger: logging.Logger) -> tuple[bool, str]:
    check = _run_command(
        [sys.executable, "-m", "code_review_graph.cli", "--version"],
        cwd=None,
        timeout_seconds=30,
    )
    if check.returncode == 0:
        return True, "already_installed"

    logger.info("Installing code-review-graph from GitHub: %s", _CRG_INSTALL_REF)
    install = _run_command(
        [sys.executable, "-m", "pip", "install", "--upgrade", _CRG_INSTALL_REF],
        cwd=None,
        timeout_seconds=600,
    )
    if install.returncode != 0:
        reason = (install.stderr or install.stdout or "pip install failed").strip()[:500]
        logger.warning("code-review-graph install failed: %s", reason)
        return False, reason

    recheck = _run_command(
        [sys.executable, "-m", "code_review_graph.cli", "--version"],
        cwd=None,
        timeout_seconds=30,
    )
    if recheck.returncode != 0:
        reason = (recheck.stderr or recheck.stdout or "verify failed").strip()[:500]
        logger.warning("code-review-graph install verification failed: %s", reason)
        return False, reason

    return True, "installed"


def _graph_db_path(repo_root: Path) -> Path:
    return repo_root / _CRG_DB_DIRNAME / _CRG_DB_FILENAME


def _sync_meta_path(repo_root: Path) -> Path:
    return repo_root / "bridge_progress" / _CRG_SYNC_META_FILENAME


def _load_sync_meta(repo_root: Path) -> dict:
    path = _sync_meta_path(repo_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sync_meta(repo_root: Path, payload: dict) -> None:
    path = _sync_meta_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _should_refresh(repo_root: Path, min_interval_seconds: int) -> bool:
    db_path = _graph_db_path(repo_root)
    if not db_path.exists():
        return True

    meta = _load_sync_meta(repo_root)
    last_success_epoch = float(meta.get("last_success_epoch", 0.0) or 0.0)
    if last_success_epoch <= 0:
        return True

    return (time.time() - last_success_epoch) >= max(60, min_interval_seconds)


def _run_graph_build_cli(repo_root: Path, full_rebuild: bool) -> subprocess.CompletedProcess:
    command = [sys.executable, "-m", "code_review_graph.cli", "build", "--repo", str(repo_root)]
    if not full_rebuild:
        command = [sys.executable, "-m", "code_review_graph.cli", "update", "--repo", str(repo_root)]
    return _run_command(
        command,
        cwd=repo_root,
        timeout_seconds=1200 if full_rebuild else 600,
    )


def _run_graph_build_fallback(repo_root: Path, full_rebuild: bool) -> tuple[bool, dict]:
    mode = "full" if full_rebuild else "incremental"
    script = (
        "import json, os\n"
        "from code_review_graph.tools.build import _get_store, _run_postprocess\n"
        "from code_review_graph.incremental import full_build, incremental_update\n"
        f"repo = r'''{str(repo_root)}'''\n"
        "os.environ['CRG_SERIAL_PARSE'] = '1'\n"
        "store, root = _get_store(repo)\n"
        "try:\n"
        "    store._conn.isolation_level = None\n"
        f"    if '{mode}' == 'full':\n"
        "        result = full_build(root, store)\n"
        "        changed = None\n"
        "    else:\n"
        "        result = incremental_update(root, store, base='HEAD~1')\n"
        "        changed = result.get('changed_files')\n"
        "    output = {'ok': True, 'mode': '" + mode + "', 'result': result}\n"
        "    _run_postprocess(store, output, 'full' if '" + mode + "' == 'full' else 'minimal', full_rebuild=('" + mode + "' == 'full'), changed_files=changed)\n"
        "    print(json.dumps(output))\n"
        "finally:\n"
        "    store.close()\n"
    )
    proc = _run_command(
        [sys.executable, "-c", script],
        cwd=repo_root,
        timeout_seconds=1800 if full_rebuild else 900,
    )
    if proc.returncode != 0:
        return False, {"error": (proc.stderr or proc.stdout or "").strip()[:1000]}
    try:
        parsed = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        parsed = {"raw": (proc.stdout or "").strip()[:1000]}
    return True, parsed


def _collect_graph_snapshot(repo_root: Path) -> dict:
    db_path = _graph_db_path(repo_root)
    if not db_path.exists():
        return {}

    snapshot: dict = {
        "db_path": str(db_path),
        "nodes": 0,
        "edges": 0,
        "files": 0,
        "flows": 0,
        "communities": 0,
        "top_communities": [],
        "last_updated": "",
        "git_branch": "",
        "git_head_sha": "",
        "last_build_type": "",
    }

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        snapshot["nodes"] = int(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        snapshot["edges"] = int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        snapshot["files"] = int(
            conn.execute("SELECT COUNT(DISTINCT file_path) FROM nodes WHERE kind='File'").fetchone()[0]
        )
        try:
            snapshot["flows"] = int(conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0])
        except sqlite3.Error:
            snapshot["flows"] = 0
        try:
            snapshot["communities"] = int(conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0])
        except sqlite3.Error:
            snapshot["communities"] = 0

        meta_rows = conn.execute(
            "SELECT key, value FROM metadata WHERE key IN ('last_updated', 'git_branch', 'git_head_sha', 'last_build_type')"
        ).fetchall()
        for row in meta_rows:
            key = str(row["key"])
            snapshot[key] = str(row["value"])

        try:
            comm_rows = conn.execute(
                "SELECT name, size, cohesion FROM communities ORDER BY size DESC, cohesion DESC LIMIT 8"
            ).fetchall()
            snapshot["top_communities"] = [
                {
                    "name": str(row["name"] or "").strip(),
                    "size": int(row["size"] or 0),
                    "cohesion": float(row["cohesion"] or 0.0),
                }
                for row in comm_rows
                if str(row["name"] or "").strip()
            ]
        except sqlite3.Error:
            snapshot["top_communities"] = []
    finally:
        conn.close()

    return snapshot


def merge_graph_snapshot_into_knowledge(knowledge: dict, snapshot: dict) -> dict:
    if not snapshot:
        return knowledge

    project = knowledge.setdefault("project", {})
    scanners = knowledge.setdefault("external_scanners", {})
    scanners["code_review_graph"] = snapshot

    project["external_scanner"] = "code-review-graph"
    project["external_scanner_last_refreshed"] = datetime.now().isoformat(timespec="seconds")

    patterns = knowledge.setdefault("patterns", [])
    existing = set(str(item) for item in patterns)
    headline_patterns = [
        f"Code-review-graph nodes={snapshot.get('nodes', 0)}, edges={snapshot.get('edges', 0)}, files={snapshot.get('files', 0)}",
        f"Code-review-graph flows={snapshot.get('flows', 0)}, communities={snapshot.get('communities', 0)}",
    ]
    for pattern in headline_patterns:
        if pattern not in existing:
            patterns.append(pattern)
            existing.add(pattern)

    for community in snapshot.get("top_communities", [])[:5]:
        community_pattern = (
            f"CRG community: {community.get('name', 'unknown')} "
            f"(size={community.get('size', 0)}, cohesion={community.get('cohesion', 0.0):.2f})"
        )
        if community_pattern not in existing:
            patterns.append(community_pattern)
            existing.add(community_pattern)

    return knowledge


def refresh_project_knowledge_with_code_review_graph(
    repo_root: Path,
    knowledge: dict,
    logger: logging.Logger,
    *,
    force_full_rebuild: bool = False,
    min_interval_seconds: int = 1800,
) -> dict:
    if not repo_root.exists() or not repo_root.is_dir():
        return {"ok": False, "reason": "invalid_repo_root", "changed": False}

    if not force_full_rebuild and not _should_refresh(repo_root, min_interval_seconds):
        return {"ok": True, "reason": "interval_not_elapsed", "changed": False}

    installed, install_reason = ensure_code_review_graph_installed(logger)
    if not installed:
        return {
            "ok": False,
            "reason": f"install_failed:{install_reason}",
            "changed": False,
        }

    db_exists_before = _graph_db_path(repo_root).exists()
    full_rebuild = force_full_rebuild or not db_exists_before

    cli_proc = _run_graph_build_cli(repo_root, full_rebuild)
    cli_output = (cli_proc.stdout or "") + "\n" + (cli_proc.stderr or "")
    if cli_proc.returncode != 0:
        logger.warning(
            "code-review-graph CLI %s failed (falling back): %s",
            "build" if full_rebuild else "update",
            cli_output.strip()[:600],
        )
        fallback_ok, fallback_payload = _run_graph_build_fallback(repo_root, full_rebuild)
        if not fallback_ok:
            _save_sync_meta(
                repo_root,
                {
                    "last_attempt_epoch": time.time(),
                    "last_status": "failed",
                    "last_error": str(fallback_payload.get("error", "fallback_failed")),
                    "last_mode": "full" if full_rebuild else "incremental",
                },
            )
            return {
                "ok": False,
                "reason": "build_failed",
                "changed": False,
                "error": str(fallback_payload.get("error", "")),
            }

    snapshot = _collect_graph_snapshot(repo_root)
    if not snapshot:
        return {"ok": False, "reason": "snapshot_empty", "changed": False}

    merge_graph_snapshot_into_knowledge(knowledge, snapshot)

    _save_sync_meta(
        repo_root,
        {
            "last_success_epoch": time.time(),
            "last_attempt_epoch": time.time(),
            "last_status": "success",
            "last_mode": "full" if full_rebuild else "incremental",
            "snapshot": {
                "nodes": snapshot.get("nodes", 0),
                "edges": snapshot.get("edges", 0),
                "files": snapshot.get("files", 0),
                "flows": snapshot.get("flows", 0),
                "communities": snapshot.get("communities", 0),
                "last_updated": snapshot.get("last_updated", ""),
            },
        },
    )

    return {
        "ok": True,
        "reason": "refreshed",
        "changed": True,
        "mode": "full" if full_rebuild else "incremental",
        "snapshot": snapshot,
    }
