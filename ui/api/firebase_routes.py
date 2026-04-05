"""Firebase & Auth API blueprint — login, sync, user setup, cloud dashboard.

Extracted from ui/app.py for maintainability.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from ui import state_store

firebase_bp = Blueprint("firebase", __name__)


# ── Shared Firebase Auth (firebase_sync.py) ──────────────────────────────────

@firebase_bp.route("/api/auth/status")
def api_auth_status():
    try:
        from utils.firebase_sync import get_firebase_sync
        sync = get_firebase_sync()
        if not sync:
            return jsonify({"logged_in": False, "configured": False})
        return jsonify({**sync.get_user_info(), "configured": True})
    except Exception:
        return jsonify({"logged_in": False, "configured": False})


@firebase_bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    try:
        from utils.firebase_sync import get_firebase_sync, AuthError
        sync = get_firebase_sync()
        if not sync:
            return jsonify({"error": "Firebase not configured."}), 400
        result = sync.login_with_google()
        if result.get("ok"):
            sync.update_profile()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@firebase_bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    try:
        from utils.firebase_sync import get_firebase_sync
        sync = get_firebase_sync()
        if sync:
            sync.logout()
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": True})


@firebase_bp.route("/api/sync/enable", methods=["POST"])
def api_sync_enable():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_authenticated():
        return jsonify({"error": "Login first to enable sync."}), 400
    sync.set_enabled(True)
    flushed = sync.flush_queue()
    return jsonify({"ok": True, "flushed": flushed})


@firebase_bp.route("/api/sync/disable", methods=["POST"])
def api_sync_disable():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if sync:
        sync.set_enabled(False)
    return jsonify({"ok": True})


@firebase_bp.route("/api/sync/status")
def api_sync_status():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync:
        return jsonify({"enabled": False, "authenticated": False, "configured": False})
    status = sync.get_sync_status()
    status["configured"] = True
    return jsonify(status)


@firebase_bp.route("/api/sync/push", methods=["POST"])
def api_sync_push():
    """Force manual sync of current project data."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_enabled():
        return jsonify({"error": "Sync not enabled."}), 400

    data = request.json or {}
    repo_root = (data.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()

    if repo_root:
        project_name = Path(repo_root).name
        try:
            from utils.project_knowledge import load_knowledge
            knowledge = load_knowledge(Path(repo_root))
            sync.push_project_meta(project_name, knowledge)
        except Exception:
            pass
        sync.push_settings(state_store.load_settings())
        flushed = sync.flush_queue()
        return jsonify({"ok": True, "project": project_name, "flushed": flushed})

    return jsonify({"ok": True, "flushed": sync.flush_queue()})


@firebase_bp.route("/api/sync/export")
def api_sync_export():
    """Export all user's cloud data (GDPR data portability)."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_authenticated():
        return jsonify({"error": "Not authenticated"}), 401
    try:
        data = sync.export_all_data()
        return jsonify(data)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@firebase_bp.route("/api/sync/delete-account", methods=["POST"])
def api_sync_delete_account():
    """Delete all cloud data and logout."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if sync:
        sync.delete_all_data()
    return jsonify({"ok": True})


# ── Per-User Firebase (firebase_user_setup.py) ───────────────────────────────

@firebase_bp.route("/api/firebase/status")
def api_firebase_status():
    try:
        from utils.firebase_user_setup import get_user_setup
        return jsonify(get_user_setup().get_status())
    except Exception as ex:
        return jsonify({"configured": False, "error": str(ex)})


@firebase_bp.route("/api/firebase/setup", methods=["POST"])
def api_firebase_setup():
    from utils.firebase_user_setup import get_user_setup, SetupError
    data = request.json or {}
    try:
        result = get_user_setup().save_config(data)
        return jsonify(result)
    except SetupError as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@firebase_bp.route("/api/firebase/test", methods=["POST"])
def api_firebase_test():
    from utils.firebase_user_setup import get_user_setup
    try:
        result = get_user_setup().test_connection()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@firebase_bp.route("/api/firebase/login", methods=["POST"])
def api_firebase_login():
    from utils.firebase_user_setup import get_user_setup, SetupError
    try:
        result = get_user_setup().login()
        return jsonify(result)
    except SetupError as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@firebase_bp.route("/api/firebase/logout", methods=["POST"])
def api_firebase_logout():
    from utils.firebase_user_setup import get_user_setup
    get_user_setup().logout()
    return jsonify({"ok": True})


@firebase_bp.route("/api/firebase/clear", methods=["POST"])
def api_firebase_clear():
    from utils.firebase_user_setup import get_user_setup
    get_user_setup().clear_config()
    return jsonify({"ok": True})
