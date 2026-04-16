"""Firebase Per-User Setup — guides users through creating their own Firebase project.

Each user gets their own Firebase project. Their data lives in their own
Google Cloud account. The super admin only receives anonymized aggregate metrics.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# Data directory
if os.name == "nt":
    _DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AiderBridge"
else:
    _DATA_DIR = Path.home() / ".local" / "share" / "AiderBridge"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_USER_CONFIG_FILE = _DATA_DIR / "user_firebase_config.json"
_USER_CREDENTIALS_FILE = _DATA_DIR / "user_firebase_credentials.json"
_ADMIN_CONFIG_FILE = Path(__file__).parent.parent / "firebase_config.json"

# Admin project config (bundled with the app — your project for aggregate metrics)
_ADMIN_CONFIG: dict = {}
if _ADMIN_CONFIG_FILE.exists():
    try:
        _ADMIN_CONFIG = json.loads(_ADMIN_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass


class SetupError(Exception):
    pass


class FirebaseUserSetup:
    """Manages per-user Firebase project setup and validation."""

    def __init__(self):
        self._config: dict = {}
        self._credentials: dict = {}
        self._load()

    def _load(self):
        if _USER_CONFIG_FILE.exists():
            try:
                self._config = json.loads(_USER_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._config = {}
        if _USER_CREDENTIALS_FILE.exists():
            try:
                self._credentials = json.loads(_USER_CREDENTIALS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._credentials = {}

    # ── Status ────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(self._config.get("apiKey") and self._config.get("projectId"))

    def is_authenticated(self) -> bool:
        return bool(self._credentials.get("uid") and self._credentials.get("refresh_token"))

    def get_status(self) -> dict:
        return {
            "configured": self.is_configured(),
            "authenticated": self.is_authenticated(),
            "project_id": self._config.get("projectId", ""),
            "email": self._credentials.get("email", ""),
            "dashboard_url": f"https://{self._config.get('projectId', '')}.web.app" if self.is_configured() else "",
            "has_admin_config": bool(_ADMIN_CONFIG),
        }

    # ── Config Management ─────────────────────────────────────────────────

    def save_config(self, config: dict) -> dict:
        """Validate and save the user's Firebase config."""
        required = ["apiKey", "projectId"]
        for key in required:
            if not config.get(key):
                raise SetupError(f"Missing required field: {key}")

        # Normalize field names
        normalized = {
            "apiKey": config.get("apiKey", ""),
            "authDomain": config.get("authDomain", ""),
            "projectId": config.get("projectId", ""),
            "storageBucket": config.get("storageBucket", ""),
            "messagingSenderId": config.get("messagingSenderId", ""),
            "appId": config.get("appId", ""),
            "client_id": config.get("client_id") or config.get("clientId", ""),
            "client_secret": config.get("client_secret") or config.get("clientSecret", ""),
        }

        self._config = normalized
        _USER_CONFIG_FILE.write_text(json.dumps(normalized, indent=2), encoding="utf-8")

        return {"ok": True, "project_id": normalized["projectId"]}

    def clear_config(self):
        self._config = {}
        self._credentials = {}
        try:
            _USER_CONFIG_FILE.unlink(missing_ok=True)
            _USER_CREDENTIALS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Connection Test ───────────────────────────────────────────────────

    def test_connection(self) -> dict:
        """Test that the user's Firestore is accessible."""
        if not self.is_configured():
            return {"ok": False, "error": "Not configured"}

        project_id = self._config["projectId"]
        api_key = self._config["apiKey"]

        # Test 1: Can we reach Firestore?
        try:
            url = (
                f"https://firestore.googleapis.com/v1/"
                f"projects/{project_id}/databases/(default)/documents"
                f"?key={api_key}&pageSize=1"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            firestore_ok = True
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                return {"ok": False, "error": "Firestore database not created. Enable Firestore in Firebase Console."}
            elif ex.code == 403:
                return {"ok": False, "error": "Firestore access denied. Check your API key."}
            firestore_ok = False
        except Exception as ex:
            return {"ok": False, "error": f"Cannot reach Firestore: {ex}"}

        # Test 2: Can we write a test document?
        try:
            test_path = f"_bridge_test/connection_test"
            test_data = {"timestamp": datetime.now().isoformat(), "test": True}
            self._write_doc(project_id, api_key, test_path, test_data)
            write_ok = True
        except Exception as ex:
            write_ok = False

        return {
            "ok": firestore_ok,
            "firestore_accessible": firestore_ok,
            "write_test": write_ok,
            "project_id": project_id,
        }

    # ── Authentication ────────────────────────────────────────────────────

    def login(self) -> dict:
        """Google OAuth login for the user's Firebase project."""
        if not self.is_configured():
            raise SetupError("Configure your Firebase project first.")

        client_id = self._config.get("client_id", "")
        client_secret = self._config.get("client_secret", "")

        if not client_id:
            raise SetupError("client_id not set in your Firebase config. Add OAuth credentials from Google Cloud Console.")

        import http.server
        import socket
        import threading
        import uuid
        import webbrowser

        # Find free port
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        redirect_uri = f"http://localhost:{port}/callback"
        auth_code = [None]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
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

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        server.timeout = 120

        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        auth_url = (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={urllib.parse.quote(client_id)}&"
            f"redirect_uri={urllib.parse.quote(redirect_uri)}&"
            f"response_type=code&scope=email%20profile&"
            f"access_type=offline&prompt=consent"
        )
        webbrowser.open(auth_url)
        t.join(timeout=120)

        if not auth_code[0]:
            raise SetupError("Login timed out.")

        # Exchange code for tokens
        body = urllib.parse.urlencode({
            "code": auth_code[0],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            google_tokens = json.loads(resp.read().decode())

        google_id_token = google_tokens.get("id_token", "")

        # Sign in to user's Firebase
        firebase_body = json.dumps({
            "postBody": f"id_token={google_id_token}&providerId=google.com",
            "requestUri": "http://localhost",
            "returnIdpCredential": True,
            "returnSecureToken": True,
        }).encode()

        req = urllib.request.Request(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={self._config['apiKey']}",
            data=firebase_body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            fb_result = json.loads(resp.read().decode())

        self._credentials = {
            "id_token": fb_result.get("idToken"),
            "refresh_token": fb_result.get("refreshToken"),
            "uid": fb_result.get("localId"),
            "email": fb_result.get("email"),
            "display_name": fb_result.get("displayName", ""),
            "token_expiry": time.time() + int(fb_result.get("expiresIn", 3600)),
        }
        _USER_CREDENTIALS_FILE.write_text(json.dumps(self._credentials, indent=2), encoding="utf-8")

        return {"ok": True, "email": self._credentials["email"], "uid": self._credentials["uid"]}

    def logout(self):
        self._credentials = {}
        try:
            _USER_CREDENTIALS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def get_token(self) -> str:
        """Get a valid Firebase ID token, refreshing if needed."""
        if time.time() < self._credentials.get("token_expiry", 0) - 60:
            return self._credentials.get("id_token", "")

        # Refresh
        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": self._credentials.get("refresh_token", ""),
        }).encode()
        req = urllib.request.Request(
            f"https://securetoken.googleapis.com/v1/token?key={self._config['apiKey']}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        self._credentials["id_token"] = data.get("id_token")
        self._credentials["refresh_token"] = data.get("refresh_token", self._credentials.get("refresh_token"))
        self._credentials["token_expiry"] = time.time() + int(data.get("expires_in", 3600))
        _USER_CREDENTIALS_FILE.write_text(json.dumps(self._credentials, indent=2), encoding="utf-8")
        return self._credentials["id_token"]

    # ── Firestore Write (to user's project) ───────────────────────────────

    def write_to_user_firestore(self, path: str, data: dict) -> None:
        """Write a document to the user's Firestore."""
        if not self.is_configured() or not self.is_authenticated():
            return
        token = self.get_token()
        project_id = self._config["projectId"]
        url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/{path}"
        body = json.dumps({"fields": _to_fields(data)}).encode()
        req = urllib.request.Request(
            url, data=body, method="PATCH",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15)

    # ── Admin Metrics Push (anonymized) ───────────────────────────────────

    def push_admin_metrics(self, metrics: dict) -> None:
        """Push anonymized aggregate metrics to the admin's Firebase project."""
        if not _ADMIN_CONFIG or not self._credentials.get("email"):
            return
        user_hash = hashlib.sha256(self._credentials["email"].encode()).hexdigest()[:12]

        admin_data = {
            "user_hash": user_hash,
            "project_names": metrics.get("project_names", []),
            "total_tasks": metrics.get("total_tasks", 0),
            "total_runs": metrics.get("total_runs", 0),
            "total_supervisor_tokens": metrics.get("total_supervisor_tokens", 0),
            "total_aider_tokens": metrics.get("total_aider_tokens", 0),
            "total_tokens_saved": metrics.get("total_tokens_saved", 0),
            "avg_savings_percent": metrics.get("avg_savings_percent", 0),
            "os": f"{platform.system()} {platform.release()}",
            "app_version": "0.1",
            "last_active": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            self._write_doc(
                _ADMIN_CONFIG["projectId"],
                _ADMIN_CONFIG["apiKey"],
                f"users/{user_hash}",
                admin_data,
            )
        except Exception:
            pass  # Non-critical — admin metrics are best-effort

    # ── Helpers ───────────────────────────────────────────────────────────

    def _write_doc(self, project_id: str, api_key: str, path: str, data: dict) -> None:
        """Write a Firestore document using API key auth (no user token needed)."""
        url = (
            f"https://firestore.googleapis.com/v1/"
            f"projects/{project_id}/databases/(default)/documents/{path}"
            f"?key={api_key}"
        )
        body = json.dumps({"fields": _to_fields(data)}).encode()
        req = urllib.request.Request(
            url, data=body, method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)


def _to_fields(data: dict) -> dict:
    """Convert Python dict to Firestore field format."""
    # Sensitive fields never synced
    _STRIP = {
        "goal", "instruction", "diff", "stdout", "stderr", "error_message",
        "repo_root", "plan_file", "idea_file", "command", "supervisor_command",
    }
    fields = {}
    for k, v in data.items():
        if k in _STRIP:
            continue
        fields[k] = _to_value(v)
    return fields


def _to_value(v) -> dict:
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v[:1500]}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_to_value(i) for i in v[:50]]}}
    if v is None:
        return {"nullValue": None}
    return {"stringValue": str(v)[:500]}


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[FirebaseUserSetup] = None


def get_user_setup() -> FirebaseUserSetup:
    global _instance
    if _instance is None:
        _instance = FirebaseUserSetup()
    return _instance
