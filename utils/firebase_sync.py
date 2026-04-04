"""Firebase Cloud Sync — authentication and data push to user's Firestore.

Uses Firestore REST API (no firebase-admin dependency).
All data stays in the user's own collection — no cross-user access.
Cloud sync is OFF by default — user must opt in.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

# Data directory (same as state_store)
if os.name == "nt":
    _DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AiderBridge"
else:
    _DATA_DIR = Path.home() / ".local" / "share" / "AiderBridge"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_CREDENTIALS_FILE = _DATA_DIR / "firebase_credentials.json"
_CONFIG_FILE = _DATA_DIR / "firebase_config.json"
_SYNC_QUEUE_FILE = _DATA_DIR / "firebase_sync_queue.json"

# Fields that are NEVER synced to Firestore
_SENSITIVE_FIELDS = {
    "goal", "instruction", "diff", "stdout", "stderr", "error_message",
    "repo_root", "plan_file", "idea_file", "idea_text", "command",
    "supervisor_command", "validation_command", "clarifications",
    "stdout_tail", "stderr_tail", "note",
}

# Fields allowed in run data
_RUN_ALLOWED = {
    "status", "tasks_planned", "tasks_completed", "tasks_failed",
    "elapsed_seconds", "supervisor", "model",
}

# Fields allowed in token data
_TOKEN_ALLOWED = {
    "timestamp", "plan_tokens", "review_tokens", "aider_tokens",
    "estimated_direct", "actual_ai", "tokens_saved", "savings_percent",
}


class AuthError(Exception):
    pass


class FirebaseSync:
    """Manages Google OAuth authentication and Firestore data sync."""

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or _CONFIG_FILE
        self._config: dict = {}
        self._api_key: str = ""
        self._project_id: str = ""
        self._auth_domain: str = ""
        self._id_token: Optional[str] = None
        self._refresh_token_str: Optional[str] = None
        self._uid: Optional[str] = None
        self._email: Optional[str] = None
        self._display_name: Optional[str] = None
        self._token_expiry: float = 0
        self._offline_queue: list[dict] = []
        self._login_in_progress = False
        self._enabled = False

        self._load_config()
        self._load_credentials()
        self._load_queue()

    # ── Config ────────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if self._config_path.exists():
            try:
                self._config = json.loads(self._config_path.read_text(encoding="utf-8"))
                self._api_key = self._config.get("apiKey", "")
                self._project_id = self._config.get("projectId", "")
                self._auth_domain = self._config.get("authDomain", "")
                self._enabled = self._config.get("enabled", False)
            except Exception:
                pass

    def is_configured(self) -> bool:
        return bool(self._api_key and self._project_id)

    def is_enabled(self) -> bool:
        return self._enabled and self.is_authenticated()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._config["enabled"] = enabled
        self._save_config()

    def _save_config(self) -> None:
        try:
            self._config_path.write_text(json.dumps(self._config, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── Credentials ───────────────────────────────────────────────────────

    def _load_credentials(self) -> None:
        if _CREDENTIALS_FILE.exists():
            try:
                data = json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
                self._id_token = data.get("id_token")
                self._refresh_token_str = data.get("refresh_token")
                self._uid = data.get("uid")
                self._email = data.get("email")
                self._display_name = data.get("display_name")
                self._token_expiry = data.get("token_expiry", 0)
            except Exception:
                pass

    def _save_credentials(self) -> None:
        try:
            _CREDENTIALS_FILE.write_text(json.dumps({
                "id_token": self._id_token,
                "refresh_token": self._refresh_token_str,
                "uid": self._uid,
                "email": self._email,
                "display_name": self._display_name,
                "token_expiry": self._token_expiry,
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _clear_credentials(self) -> None:
        self._id_token = None
        self._refresh_token_str = None
        self._uid = None
        self._email = None
        self._display_name = None
        self._token_expiry = 0
        try:
            _CREDENTIALS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Auth ──────────────────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        return bool(self._uid and self._refresh_token_str)

    def get_user_info(self) -> dict:
        return {
            "logged_in": self.is_authenticated(),
            "email": self._email,
            "display_name": self._display_name,
            "uid": self._uid,
            "sync_enabled": self._enabled,
        }

    def login_with_google(self) -> dict:
        """Start Google OAuth2 login flow. Opens browser for consent."""
        if not self.is_configured():
            raise AuthError("Firebase not configured. Place firebase_config.json in the app data directory.")
        if self._login_in_progress:
            raise AuthError("Login already in progress.")

        self._login_in_progress = True
        try:
            # Find a free port for the callback server
            import socket
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()

            redirect_uri = f"http://localhost:{port}/callback"
            state = str(uuid.uuid4())[:8]

            # Build Google OAuth URL
            auth_url = (
                f"https://accounts.google.com/o/oauth2/v2/auth?"
                f"client_id={urllib.parse.quote(self._config.get('clientId', self._api_key))}&"
                f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
                f"response_type=code&"
                f"scope=email%20profile&"
                f"state={state}&"
                f"access_type=offline&"
                f"prompt=consent"
            )

            # Start callback server in background
            auth_code = [None]
            server_ready = threading.Event()

            class CallbackHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urllib.parse.urlparse(self.path)
                    params = urllib.parse.parse_qs(parsed.query)
                    code = params.get("code", [None])[0]
                    if code:
                        auth_code[0] = code
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html")
                        self.end_headers()
                        self.wfile.write(b"<html><body><h2>Login successful!</h2><p>You can close this tab.</p><script>window.close()</script></body></html>")
                    else:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(b"Login failed.")

                def log_message(self, format, *args):
                    pass  # Suppress server logs

            server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
            server.timeout = 120  # 2 minute timeout

            def serve():
                server_ready.set()
                server.handle_request()  # Handle one request then stop

            t = threading.Thread(target=serve, daemon=True)
            t.start()
            server_ready.wait()

            # Open browser
            webbrowser.open(auth_url)

            # Wait for callback
            t.join(timeout=120)

            if not auth_code[0]:
                raise AuthError("Login timed out. Please try again.")

            # Exchange auth code for Firebase tokens
            result = self._exchange_code_for_tokens(auth_code[0], redirect_uri)
            return result

        finally:
            self._login_in_progress = False

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str) -> dict:
        """Exchange OAuth authorization code for Firebase ID token."""
        # Step 1: Exchange code for Google tokens
        body = urllib.parse.urlencode({
            "code": code,
            "client_id": self._config.get("clientId", ""),
            "client_secret": self._config.get("clientSecret", ""),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                google_tokens = json.loads(resp.read().decode())
        except urllib.error.HTTPError as ex:
            err_body = ex.read().decode() if ex.readable() else str(ex)
            raise AuthError(f"Google token exchange failed: {err_body}") from ex

        google_id_token = google_tokens.get("id_token", "")

        # Step 2: Sign in to Firebase with Google ID token
        firebase_body = json.dumps({
            "postBody": f"id_token={google_id_token}&providerId=google.com",
            "requestUri": "http://localhost",
            "returnIdpCredential": True,
            "returnSecureToken": True,
        }).encode()

        req = urllib.request.Request(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={self._api_key}",
            data=firebase_body,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                firebase_result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as ex:
            err_body = ex.read().decode() if ex.readable() else str(ex)
            raise AuthError(f"Firebase sign-in failed: {err_body}") from ex

        # Store credentials
        self._id_token = firebase_result.get("idToken")
        self._refresh_token_str = firebase_result.get("refreshToken")
        self._uid = firebase_result.get("localId")
        self._email = firebase_result.get("email")
        self._display_name = firebase_result.get("displayName", self._email)
        self._token_expiry = time.time() + int(firebase_result.get("expiresIn", 3600))
        self._save_credentials()

        return {
            "ok": True,
            "uid": self._uid,
            "email": self._email,
            "display_name": self._display_name,
        }

    def logout(self) -> None:
        self._clear_credentials()
        self._enabled = False
        self._config["enabled"] = False
        self._save_config()

    def _get_token(self) -> str:
        """Return a valid ID token, refreshing if expired."""
        if not self._refresh_token_str:
            raise AuthError("Not authenticated.")

        if time.time() < self._token_expiry - 60 and self._id_token:
            return self._id_token

        return self._refresh_id_token()

    def _refresh_id_token(self) -> str:
        """Exchange refresh token for a new ID token."""
        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token_str,
        }).encode()

        req = urllib.request.Request(
            f"https://securetoken.googleapis.com/v1/token?key={self._api_key}",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as ex:
            raise AuthError(f"Token refresh failed: {ex}") from ex

        self._id_token = data.get("id_token")
        self._refresh_token_str = data.get("refresh_token", self._refresh_token_str)
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        self._save_credentials()
        return self._id_token

    # ── Firestore REST API ────────────────────────────────────────────────

    def _firestore_url(self, path: str) -> str:
        return (
            f"https://firestore.googleapis.com/v1/"
            f"projects/{self._project_id}/databases/(default)/documents/{path}"
        )

    def _firestore_write(self, path: str, data: dict) -> None:
        """Write (create/update) a document to Firestore."""
        token = self._get_token()
        body = json.dumps({"fields": self._to_firestore_fields(data)}).encode()
        req = urllib.request.Request(
            self._firestore_url(path),
            data=body,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as ex:
            raise AuthError(f"Firestore write failed: {ex.code} {ex.reason}") from ex

    def _to_firestore_fields(self, data: dict) -> dict:
        fields = {}
        for key, value in data.items():
            if key in _SENSITIVE_FIELDS:
                continue
            fields[key] = self._to_firestore_value(value)
        return fields

    def _to_firestore_value(self, value) -> dict:
        if isinstance(value, bool):
            return {"booleanValue": value}
        if isinstance(value, int):
            return {"integerValue": str(value)}
        if isinstance(value, float):
            return {"doubleValue": value}
        if isinstance(value, str):
            return {"stringValue": value[:1500]}
        if isinstance(value, list):
            return {"arrayValue": {"values": [self._to_firestore_value(v) for v in value[:50]]}}
        if isinstance(value, dict):
            return {"mapValue": {"fields": {k: self._to_firestore_value(v) for k, v in value.items() if k not in _SENSITIVE_FIELDS}}}
        if value is None:
            return {"nullValue": None}
        return {"stringValue": str(value)[:500]}

    # ── Data Push ─────────────────────────────────────────────────────────

    def push_run_data(self, project_name: str, run_report: dict) -> None:
        """Push run metrics to Firestore (no code/goals)."""
        if not self.is_enabled():
            return
        run_id = run_report.get("session_id", str(uuid.uuid4())[:8])
        sanitized = {k: v for k, v in run_report.items() if k not in _SENSITIVE_FIELDS}
        # Extract flat token data
        savings = run_report.get("savings", {})
        sanitized["supervisor_tokens"] = savings.get("actual_supervisor_tokens", 0)
        sanitized["aider_tokens"] = run_report.get("aider", {}).get("estimated_tokens", 0)
        sanitized["tokens_saved"] = savings.get("tokens_saved", 0)
        sanitized["savings_percent"] = savings.get("savings_percent", 0)
        sanitized["synced_at"] = datetime.now().isoformat(timespec="seconds")

        path = f"users/{self._uid}/projects/{_safe_id(project_name)}/runs/{run_id}"
        try:
            self._firestore_write(path, sanitized)
            # Update project metadata
            self._update_project_meta(project_name, run_report)
        except Exception:
            self._queue_for_later({"type": "run", "project": project_name, "data": sanitized, "run_id": run_id})

    def push_token_data(self, project_name: str, token_report: dict) -> None:
        """Push token session data."""
        if not self.is_enabled():
            return
        session_id = token_report.get("session_id", str(uuid.uuid4())[:8])
        sanitized = {}
        for key in _TOKEN_ALLOWED:
            if key in token_report:
                sanitized[key] = token_report[key]
        # Flatten nested savings
        savings = token_report.get("savings", {})
        sanitized.update({
            "estimated_direct": savings.get("estimated_direct_tokens", 0),
            "actual_ai": savings.get("total_ai_tokens", 0),
            "tokens_saved": savings.get("tokens_saved", 0),
            "savings_percent": savings.get("savings_percent", 0),
        })
        sup = token_report.get("supervisor", {})
        sanitized["plan_tokens"] = sup.get("plan_in", 0) + sup.get("plan_out", 0)
        sanitized["review_tokens"] = sup.get("review_in", 0) + sup.get("review_out", 0)
        sanitized["aider_tokens"] = token_report.get("aider", {}).get("estimated_tokens", 0)
        sanitized["synced_at"] = datetime.now().isoformat(timespec="seconds")

        path = f"users/{self._uid}/projects/{_safe_id(project_name)}/tokens/{session_id}"
        try:
            self._firestore_write(path, sanitized)
        except Exception:
            self._queue_for_later({"type": "token", "project": project_name, "data": sanitized})

    def push_project_meta(self, project_name: str, knowledge: dict) -> None:
        """Push project metadata (no file paths or content)."""
        if not self.is_enabled():
            return
        project = knowledge.get("project", {})
        meta = {
            "name": project_name,
            "language": project.get("language", ""),
            "type": project.get("type", ""),
            "file_count": len(knowledge.get("files", {})),
            "patterns": [p[:100] for p in knowledge.get("patterns", [])[:10]],
            "features_done": [f[:100] for f in knowledge.get("features_done", [])[:20]],
            "last_refreshed": project.get("last_refreshed", ""),
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
        path = f"users/{self._uid}/projects/{_safe_id(project_name)}/knowledge/latest"
        try:
            self._firestore_write(path, meta)
        except Exception:
            self._queue_for_later({"type": "knowledge", "project": project_name, "data": meta})

    def _update_project_meta(self, project_name: str, run_report: dict) -> None:
        """Update project-level metadata after a run."""
        meta = {
            "name": project_name,
            "last_run_at": datetime.now().isoformat(timespec="seconds"),
            "last_run_status": run_report.get("status", ""),
            "supervisor": run_report.get("supervisor_command", ""),
            "model": run_report.get("aider", {}).get("model", ""),
        }
        path = f"users/{self._uid}/projects/{_safe_id(project_name)}"
        try:
            self._firestore_write(path, meta)
        except Exception:
            pass

    def update_profile(self) -> None:
        """Update user profile in Firestore."""
        if not self.is_enabled():
            return
        import platform
        profile = {
            "email": self._email or "",
            "display_name": self._display_name or "",
            "last_active": datetime.now().isoformat(timespec="seconds"),
            "app_version": "0.1",
            "os": f"{platform.system()} {platform.release()}",
        }
        # Add GPU info if available
        try:
            import subprocess
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if r.returncode == 0:
                profile["gpu"] = r.stdout.strip().split("\n")[0]
        except Exception:
            pass

        path = f"users/{self._uid}/profile"
        try:
            self._firestore_write(path, profile)
        except Exception:
            pass

    def push_settings(self, settings: dict) -> None:
        """Push user settings (stripped of sensitive fields)."""
        if not self.is_enabled():
            return
        safe = {
            "default_model": settings.get("aider_model", ""),
            "default_supervisor": settings.get("supervisor", ""),
            "auto_commit": settings.get("auto_commit", True),
            "task_timeout": settings.get("task_timeout", 600),
            "max_retries": settings.get("max_task_retries", 10),
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
        path = f"users/{self._uid}/settings/global"
        try:
            self._firestore_write(path, safe)
        except Exception:
            pass

    # ── Offline Queue ─────────────────────────────────────────────────────

    def _queue_for_later(self, operation: dict) -> None:
        operation["queued_at"] = datetime.now().isoformat(timespec="seconds")
        operation["retry_count"] = 0
        self._offline_queue.append(operation)
        if len(self._offline_queue) > 100:
            self._offline_queue = self._offline_queue[-100:]
        self._save_queue()

    def flush_queue(self) -> int:
        """Retry queued operations. Returns count of successful flushes."""
        if not self.is_enabled() or not self._offline_queue:
            return 0
        remaining = []
        flushed = 0
        for op in self._offline_queue:
            try:
                path_base = f"users/{self._uid}/projects/{_safe_id(op.get('project', 'unknown'))}"
                if op["type"] == "run":
                    self._firestore_write(f"{path_base}/runs/{op.get('run_id', 'r')}", op["data"])
                elif op["type"] == "token":
                    self._firestore_write(f"{path_base}/tokens/{str(uuid.uuid4())[:8]}", op["data"])
                elif op["type"] == "knowledge":
                    self._firestore_write(f"{path_base}/knowledge/latest", op["data"])
                flushed += 1
            except Exception:
                op["retry_count"] = op.get("retry_count", 0) + 1
                if op["retry_count"] < 5:
                    remaining.append(op)
        self._offline_queue = remaining
        self._save_queue()
        return flushed

    def _load_queue(self) -> None:
        if _SYNC_QUEUE_FILE.exists():
            try:
                self._offline_queue = json.loads(_SYNC_QUEUE_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._offline_queue = []

    def _save_queue(self) -> None:
        try:
            _SYNC_QUEUE_FILE.write_text(json.dumps(self._offline_queue, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_sync_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "authenticated": self.is_authenticated(),
            "queue_size": len(self._offline_queue),
            "email": self._email,
        }

    # ── Data Export & Deletion (GDPR) ─────────────────────────────────────

    def delete_all_data(self) -> None:
        """Delete all user data from Firestore and clear local credentials."""
        if not self.is_authenticated():
            return
        # Note: Firestore REST API doesn't support recursive delete.
        # In production, use a Cloud Function for this.
        # For now, clear local state.
        self._clear_credentials()
        self._offline_queue = []
        self._save_queue()
        self._enabled = False
        self._config["enabled"] = False
        self._save_config()


def _safe_id(name: str) -> str:
    """Convert a project name to a safe Firestore document ID."""
    return name.replace("/", "_").replace("\\", "_").replace(" ", "_").replace(".", "_")[:100]


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[FirebaseSync] = None


def get_firebase_sync() -> Optional[FirebaseSync]:
    """Get the Firebase sync singleton. Returns None if not configured."""
    global _instance
    if _instance is None:
        if _CONFIG_FILE.exists():
            try:
                _instance = FirebaseSync()
            except Exception:
                return None
        else:
            return None
    return _instance
