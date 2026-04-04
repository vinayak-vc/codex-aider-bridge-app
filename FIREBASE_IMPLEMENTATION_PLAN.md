# Firebase Cloud Sync — Complete Implementation Plan

> Version: 1.0 | Date: 2026-04-04
> Status: DESIGN COMPLETE — Implementation pending

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Firebase Project Setup](#2-firebase-project-setup)
3. [Authentication System](#3-authentication-system)
4. [Firestore Data Model](#4-firestore-data-model)
5. [Security Rules](#5-security-rules)
6. [Backend Implementation](#6-backend-implementation)
7. [Frontend Implementation](#7-frontend-implementation)
8. [Data Sync Engine](#8-data-sync-engine)
9. [Admin Dashboard](#9-admin-dashboard)
10. [Privacy & Data Protection](#10-privacy--data-protection)
11. [Deployment & Configuration](#11-deployment--configuration)
12. [Migration Plan](#12-migration-plan)
13. [Testing Strategy](#13-testing-strategy)
14. [File Inventory](#14-file-inventory)
15. [Milestone Breakdown](#15-milestone-breakdown)

---

## 1. System Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER'S MACHINE                                  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Bridge App (Flask — localhost:7823)                              │    │
│  │                                                                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │    │
│  │  │ Run Page │  │ Tokens   │  │Knowledge │  │ Settings      │  │    │
│  │  │          │  │ Page     │  │ Page     │  │ + Cloud Sync  │  │    │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │    │
│  │       │              │              │               │           │    │
│  │  ┌────▼──────────────▼──────────────▼───────────────▼────────┐ │    │
│  │  │              Sync Engine (utils/firebase_sync.py)          │ │    │
│  │  │                                                           │ │    │
│  │  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐ │ │    │
│  │  │  │ Auth Module │  │ Data Pusher  │  │ Offline Queue   │ │ │    │
│  │  │  │ (Google     │  │ (Firestore   │  │ (retry failed   │ │ │    │
│  │  │  │  OAuth2)    │  │  REST API)   │  │  syncs later)   │ │ │    │
│  │  │  └──────┬──────┘  └──────┬───────┘  └────────┬────────┘ │ │    │
│  │  └─────────┼────────────────┼───────────────────┼──────────┘ │    │
│  └────────────┼────────────────┼───────────────────┼────────────┘    │
│               │                │                   │                  │
└───────────────┼────────────────┼───────────────────┼──────────────────┘
                │                │                   │
                ▼                ▼                   ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        FIREBASE (Google Cloud)                         │
│                                                                       │
│  ┌─────────────────┐  ┌─────────────────────┐  ┌──────────────────┐  │
│  │ Firebase Auth    │  │ Firestore Database  │  │ Cloud Functions  │  │
│  │                  │  │                     │  │                  │  │
│  │ Google Sign-In   │  │ users/{uid}/        │  │ onRunComplete()  │  │
│  │ ID Token verify  │  │   profile           │  │   → update admin │  │
│  │ Session mgmt     │  │   projects/         │  │      aggregates  │  │
│  │                  │  │     runs/            │  │                  │  │
│  │                  │  │     tokens/          │  │ onUserCreate()   │  │
│  │                  │  │     knowledge/       │  │   → init profile │  │
│  │                  │  │                     │  │                  │  │
│  │                  │  │ admin/              │  │ scheduledCleanup │  │
│  │                  │  │   aggregates        │  │   → daily rollup │  │
│  └─────────────────┘  └─────────────────────┘  └──────────────────┘  │
│                                                                       │
│  ┌─────────────────┐  ┌─────────────────────┐                        │
│  │ Firebase Hosting │  │ Firestore Rules     │                        │
│  │ (Admin Dashboard)│  │ (User isolation)    │                        │
│  └─────────────────┘  └─────────────────────┘                        │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Technology |
|---|---|---|
| **Auth Module** | Google OAuth2 login, token refresh, session management | Firebase Auth REST API |
| **Data Pusher** | Transform local data → Firestore documents, batch writes | Firestore REST API |
| **Offline Queue** | Buffer sync operations when offline, retry on reconnect | Local JSON file |
| **Cloud Functions** | Aggregate metrics, user init, scheduled cleanup | Node.js (Firebase Functions) |
| **Admin Dashboard** | Super admin view of aggregate metrics | Firebase Hosting + Firestore |
| **Security Rules** | Enforce user-level data isolation | Firestore Security Rules |

---

## 2. Firebase Project Setup

### Prerequisites

```
1. Google account (for Firebase Console)
2. Node.js 18+ (for Cloud Functions deployment)
3. Firebase CLI: npm install -g firebase-tools
```

### Setup Steps

```bash
# 1. Create Firebase project
firebase login
firebase projects:create codex-aider-bridge --display-name "Codex Aider Bridge"

# 2. Enable services
# Via Firebase Console (console.firebase.google.com):
#   - Authentication → Sign-in method → Google → Enable
#   - Firestore Database → Create database → Production mode
#   - Hosting → Set up (for admin dashboard)

# 3. Get web app config
# Firebase Console → Project Settings → General → Web App → Add app
# Copy the firebaseConfig object

# 4. Initialize project locally
mkdir firebase && cd firebase
firebase init firestore functions hosting
```

### Firebase Config File

The bridge app needs `firebase_config.json` in its root:

```json
{
  "apiKey": "AIzaSy...",
  "authDomain": "codex-aider-bridge.firebaseapp.com",
  "projectId": "codex-aider-bridge",
  "storageBucket": "codex-aider-bridge.appspot.com",
  "messagingSenderId": "123456789",
  "appId": "1:123456789:web:abc123"
}
```

---

## 3. Authentication System

### Flow: Google Sign-In from Desktop App

```
User clicks "Login with Google" in bridge UI
         │
         ▼
Bridge opens browser tab: Google OAuth consent screen
         │
         ▼
User approves → Google redirects to localhost callback
         │
         ▼
Bridge receives authorization code
         │
         ▼
Bridge exchanges code for Firebase ID token
(POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp)
         │
         ▼
Bridge stores:
  - ID token (expires in 1 hour)
  - Refresh token (long-lived)
  - User UID
  - User email
  - Display name
         │
         ▼
All Firestore API calls include:
  Authorization: Bearer <id_token>
```

### Token Refresh

```python
def _refresh_token(self) -> str:
    """Refresh the Firebase ID token using the stored refresh token."""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": self._refresh_token_str,
    }
    resp = urllib.request.urlopen(
        urllib.request.Request(
            f"https://securetoken.googleapis.com/v1/token?key={self._api_key}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        ),
        timeout=10,
    )
    data = json.loads(resp.read())
    self._id_token = data["id_token"]
    self._refresh_token_str = data["refresh_token"]
    self._token_expiry = time.time() + int(data["expires_in"])
    self._save_credentials()
    return self._id_token
```

### Credential Storage

```
Location: %LOCALAPPDATA%/AiderBridge/firebase_credentials.json
Contents: { id_token, refresh_token, uid, email, display_name, token_expiry }
Encryption: AES-256 using machine-specific key (DPAPI on Windows, keyring on Linux)
```

### OAuth Callback Server

```python
# Temporary localhost server to receive OAuth callback
class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Parse authorization code from callback URL
        code = parse_qs(urlparse(self.path).query).get("code", [None])[0]
        if code:
            self.server.auth_code = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Login successful! You can close this tab.</h1>")
        self.server.shutdown_requested = True
```

---

## 4. Firestore Data Model

### Collection Structure

```
firestore/
├── users/
│   └── {userId}/
│       ├── profile (document)
│       │   ├── email: string
│       │   ├── display_name: string
│       │   ├── created_at: timestamp
│       │   ├── last_active: timestamp
│       │   ├── app_version: string
│       │   ├── os: string
│       │   ├── gpu: string
│       │   └── sync_enabled: boolean
│       │
│       ├── projects/ (subcollection)
│       │   └── {projectId}/ (document)
│       │       ├── name: string                    # "my-react-app"
│       │       ├── language: string                # "TypeScript"
│       │       ├── type: string                    # "node"
│       │       ├── created_at: timestamp
│       │       ├── last_run_at: timestamp
│       │       ├── last_run_status: string         # "success"
│       │       ├── total_tasks_completed: number
│       │       ├── total_runs: number
│       │       ├── file_count: number
│       │       │
│       │       ├── runs/ (subcollection)
│       │       │   └── {runId}/ (document)
│       │       │       ├── started_at: timestamp
│       │       │       ├── status: string
│       │       │       ├── tasks_planned: number
│       │       │       ├── tasks_completed: number
│       │       │       ├── tasks_failed: number
│       │       │       ├── elapsed_seconds: number
│       │       │       ├── supervisor: string
│       │       │       ├── model: string
│       │       │       ├── supervisor_tokens: number
│       │       │       ├── aider_tokens: number
│       │       │       ├── tokens_saved: number
│       │       │       └── savings_percent: number
│       │       │
│       │       ├── tokens/ (subcollection, last 100 sessions)
│       │       │   └── {sessionId}/ (document)
│       │       │       ├── timestamp: timestamp
│       │       │       ├── plan_tokens: number
│       │       │       ├── review_tokens: number
│       │       │       ├── aider_tokens: number
│       │       │       ├── estimated_direct: number
│       │       │       ├── actual_ai: number
│       │       │       ├── tokens_saved: number
│       │       │       └── savings_percent: number
│       │       │
│       │       └── knowledge/ (subcollection)
│       │           └── latest (document)
│       │               ├── file_count: number
│       │               ├── patterns: array<string>
│       │               ├── features_done: array<string>
│       │               ├── language: string
│       │               ├── project_type: string
│       │               └── last_refreshed: timestamp
│       │
│       └── settings/ (subcollection)
│           └── global (document)
│               ├── default_model: string
│               ├── default_supervisor: string
│               ├── auto_commit: boolean
│               ├── task_timeout: number
│               └── max_retries: number
│
└── admin/
    └── aggregates (document)
        ├── total_users: number
        ├── total_projects: number
        ├── total_runs: number
        ├── total_tasks_completed: number
        ├── total_supervisor_tokens: number
        ├── total_aider_tokens: number
        ├── total_tokens_saved: number
        ├── avg_savings_percent: number
        ├── active_today: number
        ├── active_this_week: number
        ├── daily_stats/ (subcollection)
        │   └── {date}/ (document)
        │       ├── users_active: number
        │       ├── runs: number
        │       ├── tasks: number
        │       ├── tokens_saved: number
        │       └── new_users: number
        └── last_updated: timestamp
```

### Document Size Estimates

| Collection | Avg doc size | Max docs/user | Total per user |
|---|---|---|---|
| profile | 500 bytes | 1 | 500 B |
| projects | 300 bytes | ~20 | 6 KB |
| runs | 400 bytes | ~50/project | 400 KB |
| tokens | 300 bytes | ~100/project | 600 KB |
| knowledge | 1 KB | 1/project | 20 KB |
| **Total per user** | | | **~1 MB** |

Firestore free tier: 1 GB storage, 50K reads/day, 20K writes/day.
At 1 MB/user, free tier supports ~1,000 users.

---

## 5. Security Rules

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // ── User data: only the owning user can read/write ──
    match /users/{userId} {
      allow read, write: if isOwner(userId);

      match /profile {
        allow read, write: if isOwner(userId);
      }

      match /projects/{projectId} {
        allow read, write: if isOwner(userId);

        match /{subcol}/{docId} {
          allow read, write: if isOwner(userId);
        }
      }

      match /settings/{settingId} {
        allow read, write: if isOwner(userId);
      }
    }

    // ── Admin aggregates: read-only for admin, write-only for Cloud Functions ──
    match /admin/{document=**} {
      allow read: if isAdmin();
      allow write: if false;  // Only Cloud Functions (service account) can write
    }

    // ── Helper functions ──
    function isOwner(userId) {
      return request.auth != null && request.auth.uid == userId;
    }

    function isAdmin() {
      return request.auth != null &&
        request.auth.token.email in ['YOUR_ADMIN_EMAIL@gmail.com'];
    }
  }
}
```

### Security Guarantees

| Rule | Effect |
|---|---|
| `isOwner(userId)` | User A cannot read User B's data under any circumstance |
| `allow write: if false` on admin | No client can modify admin aggregates directly |
| No wildcard reads | Cannot list all users or iterate collections |
| Auth required everywhere | Unauthenticated requests are blocked |

---

## 6. Backend Implementation

### File: `utils/firebase_sync.py`

```python
"""Firebase Cloud Sync — pushes bridge data to user's Firestore partition.

Uses Firestore REST API (no firebase-admin dependency).
All data stays in the user's own collection — no cross-user access.
"""

class FirebaseSync:
    """Manages authentication and data sync with Firestore."""

    def __init__(self, config_path: Path):
        self._config = json.loads(config_path.read_text())
        self._api_key = self._config["apiKey"]
        self._project_id = self._config["projectId"]
        self._credentials_path = DATA_DIR / "firebase_credentials.json"
        self._id_token = None
        self._refresh_token_str = None
        self._uid = None
        self._email = None
        self._token_expiry = 0
        self._offline_queue: list[dict] = []
        self._load_credentials()

    # ── Auth ──────────────────────────────────────────────────────
    def is_authenticated(self) -> bool
    def login_with_google(self) -> dict          # Opens browser, returns user info
    def logout(self) -> None                     # Clears credentials
    def _get_token(self) -> str                  # Returns valid ID token (auto-refresh)
    def _refresh_token(self) -> str              # Exchanges refresh token
    def _save_credentials(self) -> None          # Persist to disk
    def _load_credentials(self) -> None          # Load from disk

    # ── Sync ──────────────────────────────────────────────────────
    def push_run_data(self, project_name, run_report) -> None
    def push_token_data(self, project_name, token_report) -> None
    def push_project_meta(self, project_name, knowledge) -> None
    def push_settings(self, settings) -> None
    def update_profile(self) -> None

    # ── Firestore REST API ────────────────────────────────────────
    def _firestore_write(self, path, data) -> None
    def _firestore_read(self, path) -> dict
    def _firestore_batch_write(self, operations) -> None

    # ── Offline queue ─────────────────────────────────────────────
    def _queue_for_later(self, operation) -> None
    def _flush_queue(self) -> None
    def _load_queue(self) -> None
    def _save_queue(self) -> None
```

### Firestore REST API Usage

```python
def _firestore_write(self, path: str, data: dict) -> None:
    """Write a document to Firestore via REST API."""
    token = self._get_token()
    url = (
        f"https://firestore.googleapis.com/v1/"
        f"projects/{self._project_id}/databases/(default)/documents/{path}"
    )
    body = json.dumps({
        "fields": self._to_firestore_fields(data)
    }).encode()

    req = urllib.request.Request(
        url, data=body, method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req, timeout=10)

def _to_firestore_fields(self, data: dict) -> dict:
    """Convert Python dict to Firestore field format."""
    fields = {}
    for key, value in data.items():
        if isinstance(value, str):
            fields[key] = {"stringValue": value}
        elif isinstance(value, bool):
            fields[key] = {"booleanValue": value}
        elif isinstance(value, int):
            fields[key] = {"integerValue": str(value)}
        elif isinstance(value, float):
            fields[key] = {"doubleValue": value}
        elif isinstance(value, list):
            fields[key] = {"arrayValue": {"values": [
                self._to_firestore_value(v) for v in value
            ]}}
        elif value is None:
            fields[key] = {"nullValue": None}
    return fields
```

### Data Sanitization

```python
def _sanitize_for_sync(self, data: dict, allowed_keys: set) -> dict:
    """Strip sensitive fields before syncing to Firestore."""
    return {k: v for k, v in data.items() if k in allowed_keys}

# What gets synced vs stripped:
RUN_ALLOWED = {"status", "tasks_planned", "tasks_completed", "tasks_failed",
               "elapsed_seconds", "supervisor", "model",
               "supervisor_tokens", "aider_tokens", "tokens_saved", "savings_percent"}

RUN_STRIPPED = {"goal", "instruction", "diff", "stdout", "stderr",
               "error_message", "plan_file", "repo_root"}
```

---

## 7. Frontend Implementation

### File: `ui/static/js/core/firebase-auth.js`

```javascript
/**
 * Firebase Auth module — handles Google Sign-In via the bridge backend.
 * The actual OAuth flow is handled server-side; the frontend just triggers it
 * and displays the auth state.
 */

let _authState = { loggedIn: false, email: null, displayName: null };

export async function checkAuthStatus() {
    const data = await fetch('/api/auth/status').then(r => r.json());
    _authState = data;
    updateAuthUI();
    return data;
}

export async function login() {
    const data = await fetch('/api/auth/login', { method: 'POST' }).then(r => r.json());
    if (data.ok) {
        _authState = { loggedIn: true, email: data.email, displayName: data.display_name };
        updateAuthUI();
    }
    return data;
}

export async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    _authState = { loggedIn: false, email: null, displayName: null };
    updateAuthUI();
}

function updateAuthUI() {
    const btn = document.getElementById('auth-btn');
    const label = document.getElementById('auth-label');
    if (!btn) return;

    if (_authState.loggedIn) {
        btn.textContent = 'Logout';
        btn.onclick = logout;
        if (label) label.textContent = _authState.email || '';
    } else {
        btn.textContent = 'Login';
        btn.onclick = login;
        if (label) label.textContent = '';
    }
}
```

### UI Integration Points

**Settings slide-out panel** (run.html):
```html
<div class="settings-section">
  <h4>Cloud Sync</h4>
  <div id="cloud-sync-status">
    <span id="auth-label"></span>
    <button id="auth-btn" class="btn btn--secondary btn--sm">Login</button>
  </div>
  <div class="toggle-row">
    <div><div class="toggle-label">Auto-sync after runs</div></div>
    <label class="toggle">
      <input type="checkbox" id="f-cloud-sync">
      <span class="toggle-track"></span>
    </label>
  </div>
</div>
```

**Status bar** (base.html):
```html
<span class="status-item" id="sb-cloud-status" style="display:none">
  <span class="status-dot" id="sb-cloud-dot" data-status="synced"></span>
  <span id="sb-cloud-label">Synced</span>
</span>
```

---

## 8. Data Sync Engine

### Sync Triggers

| Event | What gets synced | Trigger location |
|---|---|---|
| Run completes | Run metrics + token data | `main.py` after `save_session_to_log()` |
| Knowledge refreshed | Project metadata | `ui/app.py` in `api_knowledge_refresh()` |
| Settings saved | User preferences | `ui/app.py` in `api_settings_save()` |
| App startup | Profile + last active | `ui/app.py` in app initialization |

### Sync Flow

```python
# In main.py, after a run completes:
def _sync_to_firebase(token_report, repo_root, config):
    """Non-blocking sync to Firestore after run completion."""
    sync = get_firebase_sync()
    if not sync or not sync.is_authenticated():
        return

    project_name = Path(repo_root).name
    threading.Thread(
        target=_do_sync,
        args=(sync, project_name, token_report),
        daemon=True,
    ).start()

def _do_sync(sync, project_name, token_report):
    try:
        sync.push_run_data(project_name, token_report)
        sync.push_token_data(project_name, token_report)
    except Exception:
        sync._queue_for_later({
            "type": "run_data",
            "project": project_name,
            "data": token_report,
        })
```

### Offline Queue

```python
# Queue failed syncs for later retry
SYNC_QUEUE_FILE = DATA_DIR / "firebase_sync_queue.json"

def _queue_for_later(self, operation: dict) -> None:
    self._offline_queue.append({
        **operation,
        "queued_at": datetime.now().isoformat(),
        "retry_count": 0,
    })
    self._save_queue()

def _flush_queue(self) -> None:
    """Called on successful auth or periodic check."""
    remaining = []
    for op in self._offline_queue:
        try:
            self._execute_operation(op)
        except Exception:
            op["retry_count"] += 1
            if op["retry_count"] < 5:  # Max 5 retries
                remaining.append(op)
    self._offline_queue = remaining
    self._save_queue()
```

---

## 9. Admin Dashboard

### Cloud Function: `onRunComplete`

```javascript
// firebase/functions/index.js

const functions = require('firebase-functions');
const admin = require('firebase-admin');
admin.initializeApp();

exports.onRunComplete = functions.firestore
  .document('users/{userId}/projects/{projectId}/runs/{runId}')
  .onCreate(async (snap, context) => {
    const data = snap.data();
    const { userId } = context.params;

    const adminRef = admin.firestore().doc('admin/aggregates');

    await adminRef.update({
      total_runs: admin.firestore.FieldValue.increment(1),
      total_tasks_completed: admin.firestore.FieldValue.increment(data.tasks_completed || 0),
      total_supervisor_tokens: admin.firestore.FieldValue.increment(data.supervisor_tokens || 0),
      total_aider_tokens: admin.firestore.FieldValue.increment(data.aider_tokens || 0),
      total_tokens_saved: admin.firestore.FieldValue.increment(data.tokens_saved || 0),
      last_updated: admin.firestore.FieldValue.serverTimestamp(),
    });

    // Daily stats
    const today = new Date().toISOString().split('T')[0];
    const dailyRef = adminRef.collection('daily_stats').doc(today);
    await dailyRef.set({
      runs: admin.firestore.FieldValue.increment(1),
      tasks: admin.firestore.FieldValue.increment(data.tasks_completed || 0),
      tokens_saved: admin.firestore.FieldValue.increment(data.tokens_saved || 0),
    }, { merge: true });
  });

exports.onUserCreate = functions.auth.user().onCreate(async (user) => {
  const adminRef = admin.firestore().doc('admin/aggregates');
  await adminRef.update({
    total_users: admin.firestore.FieldValue.increment(1),
  });

  // Daily stats
  const today = new Date().toISOString().split('T')[0];
  await adminRef.collection('daily_stats').doc(today).set({
    new_users: admin.firestore.FieldValue.increment(1),
  }, { merge: true });
});
```

### Admin Dashboard HTML

```
firebase/hosting/admin/index.html

Hosted at: https://codex-aider-bridge.web.app/admin

Protected by Firebase Auth — only admin email can access.

Displays:
- Total users, projects, runs, tasks
- Token savings (supervisor, aider, total saved, avg %)
- Daily active users chart (last 30 days)
- New users trend
- Token savings trend
- Project count by language
- NO user data, NO project content
```

---

## 10. Privacy & Data Protection

### Data Classification

| Classification | Examples | Synced? | Storage |
|---|---|---|---|
| **Public metadata** | Project name, language, file count | Yes | Firestore |
| **Usage metrics** | Task count, token usage, savings | Yes | Firestore |
| **System info** | OS, GPU, app version | Yes | Firestore |
| **Private content** | Source code, file paths, goals | **NEVER** | Local only |
| **Secrets** | API keys, tokens, passwords | **NEVER** | Local only |
| **Sensitive** | Error messages, stack traces | **NEVER** | Local only |

### GDPR Compliance

- **Right to access:** User can read all their Firestore data via the app
- **Right to deletion:** User can delete their account → all Firestore data deleted
- **Data portability:** User can export their Firestore data as JSON
- **Consent:** Cloud sync is OFF by default, requires explicit opt-in
- **Data minimization:** Only metrics synced, no content

### Data Deletion

```python
def delete_all_user_data(self) -> None:
    """Delete all user data from Firestore. Called on account deletion."""
    # Delete all subcollections recursively
    self._firestore_delete(f"users/{self._uid}")
    self._clear_credentials()
```

---

## 11. Deployment & Configuration

### Environment Variables

```bash
# Required for Cloud Functions
FIREBASE_PROJECT_ID=codex-aider-bridge
ADMIN_EMAIL=your.email@gmail.com

# Optional for bridge app
FIREBASE_CONFIG_PATH=/path/to/firebase_config.json
```

### Deploy Cloud Functions

```bash
cd firebase
firebase deploy --only functions
firebase deploy --only firestore:rules
firebase deploy --only hosting
```

### Bridge App Configuration

```json
// %LOCALAPPDATA%/AiderBridge/firebase_config.json
{
  "apiKey": "...",
  "authDomain": "...",
  "projectId": "...",
  "enabled": false,      // User toggles this ON
  "auto_sync": true,     // Sync after each run
  "sync_settings": true, // Sync settings across devices
  "sync_knowledge": true  // Sync project metadata
}
```

---

## 12. Migration Plan

### For existing users (no Firebase)

```
1. App updates to new version with Firebase support
2. Nothing changes by default — cloud sync is OFF
3. User can opt-in at any time via Settings → Cloud Sync → Enable
4. On enable: all existing local data (token history, knowledge) is
   batch-synced to Firestore
5. Future runs auto-sync if auto_sync is enabled
```

### Initial sync (one-time)

```python
async def initial_sync(self) -> None:
    """Sync all existing local data to Firestore on first enable."""
    # 1. Push profile
    self.update_profile()

    # 2. Push all projects
    for project_path in state_store.load_projects():
        knowledge = load_knowledge(Path(project_path))
        self.push_project_meta(Path(project_path).name, knowledge)

        # Push token history
        token_log = load_token_log(Path(project_path) / "bridge_progress" / "token_log.json")
        for session in token_log.get("sessions", [])[:100]:
            self.push_token_data(Path(project_path).name, session)

    # 3. Push settings
    self.push_settings(state_store.load_settings())
```

---

## 13. Testing Strategy

### Unit Tests

```python
# test_firebase_sync.py

def test_sanitize_strips_sensitive_data():
    data = {"status": "success", "goal": "secret goal", "tokens_saved": 5000}
    sanitized = sync._sanitize_for_sync(data, RUN_ALLOWED)
    assert "goal" not in sanitized
    assert sanitized["tokens_saved"] == 5000

def test_firestore_field_conversion():
    fields = sync._to_firestore_fields({"name": "test", "count": 42, "active": True})
    assert fields["name"] == {"stringValue": "test"}
    assert fields["count"] == {"integerValue": "42"}

def test_offline_queue_persists():
    sync._queue_for_later({"type": "run_data", "data": {}})
    assert len(sync._offline_queue) == 1
    # Reload from disk
    sync._load_queue()
    assert len(sync._offline_queue) == 1
```

### Integration Tests

```
1. Login flow: Google OAuth → token stored → authenticated
2. Push run data: verify document exists in Firestore
3. Offline queue: disconnect network → sync → reconnect → flush
4. Security: User A tries to read User B's data → rejected
5. Admin aggregates: run completes → Cloud Function updates counters
6. Data deletion: delete account → verify all Firestore data gone
```

---

## 14. File Inventory

### New Files

| File | Lines (est.) | Purpose |
|---|---|---|
| `utils/firebase_sync.py` | ~400 | Core sync engine (auth + Firestore REST) |
| `ui/static/js/core/firebase-auth.js` | ~80 | Frontend auth state management |
| `firebase/functions/index.js` | ~100 | Cloud Functions for admin aggregates |
| `firebase/firestore.rules` | ~40 | Security rules |
| `firebase/hosting/admin/index.html` | ~200 | Admin dashboard |
| `firebase_config.json.example` | ~10 | Template config |
| **Total** | **~830** | |

### Modified Files

| File | Changes |
|---|---|
| `ui/app.py` | Add auth routes, sync trigger routes (~80 lines) |
| `ui/templates/base.html` | Add cloud status indicator in status bar |
| `ui/templates/run.html` | Add cloud sync toggle in settings panel |
| `ui/static/js/pages/run.js` | Import firebase-auth, add sync toggle handler |
| `main.py` | Add post-run sync call (~15 lines) |
| `ui/state_store.py` | Add firebase config persistence (~20 lines) |
| `bridge.spec` | Add firebase_sync to hiddenimports |
| `requirements.txt` | No new deps (uses stdlib urllib) |

---

## 15. Milestone Breakdown

### M1: Auth System (2-3 hours)
- [ ] `utils/firebase_sync.py` — auth module (login, logout, token refresh)
- [ ] `ui/app.py` — `/api/auth/login`, `/api/auth/logout`, `/api/auth/status` routes
- [ ] `ui/static/js/core/firebase-auth.js` — frontend auth state
- [ ] OAuth callback server for Google Sign-In
- [ ] Credential storage (encrypted)
- [ ] Test: login → store token → refresh → logout

### M2: Data Sync Engine (3-4 hours)
- [ ] `utils/firebase_sync.py` — Firestore REST write/read
- [ ] Data sanitization (strip sensitive fields)
- [ ] Push functions: run_data, token_data, project_meta, settings
- [ ] Offline queue with retry
- [ ] Integration into main.py (post-run sync)
- [ ] Test: complete run → data appears in Firestore

### M3: Firebase Setup & Rules (1-2 hours)
- [ ] `firebase/firestore.rules` — security rules
- [ ] Cloud Functions: onRunComplete, onUserCreate
- [ ] Deploy rules + functions
- [ ] Test: User A can't read User B's data

### M4: Frontend Integration (2-3 hours)
- [ ] Cloud sync toggle in settings panel
- [ ] Status bar cloud indicator (synced/syncing/offline)
- [ ] Login/logout button
- [ ] Initial sync progress indicator
- [ ] Test: enable sync → see data in Firestore Console

### M5: Admin Dashboard (2-3 hours)
- [ ] `firebase/hosting/admin/index.html` — dashboard UI
- [ ] Read from admin/aggregates collection
- [ ] Charts: daily active users, token savings trend
- [ ] Deploy to Firebase Hosting
- [ ] Test: admin sees aggregate metrics

### M6: Polish & Security (1-2 hours)
- [ ] GDPR: data export, account deletion
- [ ] Credential encryption (DPAPI/keyring)
- [ ] Error handling for network failures
- [ ] Rate limiting on sync operations
- [ ] Documentation update

**Total estimated: 11-17 hours**

---

## API Routes Summary

| Route | Method | Purpose |
|---|---|---|
| `/api/auth/login` | POST | Start Google OAuth flow |
| `/api/auth/logout` | POST | Clear credentials |
| `/api/auth/status` | GET | Check login state |
| `/api/sync/enable` | POST | Enable cloud sync |
| `/api/sync/disable` | POST | Disable cloud sync |
| `/api/sync/status` | GET | Sync state (last sync, queue size) |
| `/api/sync/push` | POST | Force manual sync |
| `/api/sync/export` | GET | Export all synced data as JSON |
| `/api/sync/delete-account` | POST | Delete all cloud data |

---

*This document is the complete implementation reference. Each section is self-contained and can be implemented independently following the milestone order.*
