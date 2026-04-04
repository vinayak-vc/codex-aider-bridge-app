# Firebase Cloud Sync — Architecture Design

## Overview

Optional cloud sync that lets users access their bridge data from anywhere via a web dashboard. Each user's data is stored in their own Firestore partition — no data is shared between users. The super admin (you) can see aggregate metrics (user count, project count, token usage) but never project content.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER'S MACHINE                            │
│                                                                  │
│  Bridge App (Flask)                                              │
│  ├── Local data (bridge_progress/, settings, history)            │
│  ├── Firebase SDK (firebase-admin or REST API)                   │
│  │   └── Pushes: project metadata, token usage, task status      │
│  └── Google Auth (OAuth2 login via browser popup)                │
│                                                                  │
└──────────────────────┬───────────────────────────────────────────┘
                       │ Firestore writes (user's partition)
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                     FIREBASE (Google Cloud)                       │
│                                                                  │
│  Firestore Database                                              │
│  ├── users/{userId}/                                             │
│  │   ├── profile: { email, name, created_at, last_active }      │
│  │   ├── projects/{projectId}/                                   │
│  │   │   ├── meta: { name, path, language, last_run, status }   │
│  │   │   ├── runs/{runId}/                                       │
│  │   │   │   └── { goal, status, tasks_done, elapsed, tokens }  │
│  │   │   ├── tokens/                                             │
│  │   │   │   └── { session reports, savings data }               │
│  │   │   └── knowledge/                                          │
│  │   │       └── { file_count, patterns, features_done }         │
│  │   └── settings/                                               │
│  │       └── { default_model, supervisor, auto_commit }          │
│  │                                                               │
│  ├── admin/                                                      │
│  │   └── aggregates: { total_users, total_projects,              │
│  │       total_tasks, total_tokens_saved, active_today }         │
│  │                                                               │
│  Firebase Auth                                                   │
│  └── Google Sign-In (OAuth2)                                     │
│                                                                  │
│  Firestore Security Rules                                        │
│  └── Users can ONLY read/write their own data                    │
│      Admin can read aggregates only (no user project data)       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   SUPER ADMIN DASHBOARD                           │
│                                                                  │
│  Web page (Firebase Hosting or separate Flask route)             │
│  Shows:                                                          │
│  - Total users: 127                                              │
│  - Total projects: 342 (names only, no content)                  │
│  - Total tasks executed: 8,421                                   │
│  - Total tokens saved: 12.4M (85% avg savings)                  │
│  - Active today: 23 users                                        │
│  - Token usage by day/week/month chart                           │
│                                                                  │
│  Does NOT show:                                                  │
│  - Project code or file contents                                 │
│  - User goals or instructions                                    │
│  - Diffs or task details                                         │
│  - User settings or API keys                                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Firestore Data Schema

### `users/{userId}/profile`
```json
{
  "email": "user@gmail.com",
  "display_name": "John Doe",
  "created_at": "2026-04-04T12:00:00Z",
  "last_active": "2026-04-04T15:30:00Z",
  "app_version": "0.1",
  "os": "Windows 11",
  "gpu": "RTX 3060"
}
```

### `users/{userId}/projects/{projectId}`
```json
{
  "name": "shorts-uploader-engine",
  "language": "JavaScript",
  "type": "node",
  "created_at": "2026-03-15T10:00:00Z",
  "last_run_at": "2026-04-04T02:38:00Z",
  "last_run_status": "success",
  "total_tasks_completed": 47,
  "total_runs": 12,
  "file_count": 89
}
```
**Note:** No file paths, no code content, no goals. Just metadata.

### `users/{userId}/projects/{projectId}/runs/{runId}`
```json
{
  "started_at": "2026-04-04T02:30:00Z",
  "status": "success",
  "tasks_planned": 8,
  "tasks_completed": 8,
  "tasks_failed": 0,
  "elapsed_seconds": 340,
  "supervisor": "claude",
  "model": "ollama/qwen2.5-coder:14b",
  "tokens": {
    "supervisor_total": 8400,
    "aider_estimated": 12000,
    "savings_percent": 84.2
  }
}
```
**Note:** No goal text, no instructions, no diffs. Just metrics.

### `users/{userId}/projects/{projectId}/tokens/{sessionId}`
```json
{
  "timestamp": "2026-04-04T02:38:00Z",
  "plan_tokens": 2400,
  "review_tokens": 6000,
  "aider_tokens": 12000,
  "estimated_direct": 40000,
  "actual_ai": 8400,
  "tokens_saved": 31600,
  "savings_percent": 79.0
}
```

### `admin/aggregates`
```json
{
  "total_users": 127,
  "total_projects": 342,
  "total_runs": 2841,
  "total_tasks_completed": 18421,
  "total_supervisor_tokens": 4200000,
  "total_aider_tokens": 6800000,
  "total_tokens_saved": 42000000,
  "avg_savings_percent": 85.2,
  "active_today": 23,
  "active_this_week": 67,
  "last_updated": "2026-04-04T15:00:00Z"
}
```

---

## Firestore Security Rules

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Users can ONLY access their own data
    match /users/{userId}/{document=**} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }

    // Admin can read aggregates only
    match /admin/{document=**} {
      allow read: if request.auth != null &&
        request.auth.token.email == 'YOUR_ADMIN_EMAIL@gmail.com';
      allow write: if false; // Only Cloud Functions can write
    }
  }
}
```

---

## Implementation Files

| File | Purpose |
|---|---|
| `utils/firebase_sync.py` | Firebase SDK integration — push data to Firestore |
| `ui/app.py` | Add `/api/auth/login`, `/api/auth/logout`, `/api/auth/status` routes |
| `ui/app.py` | Add `/api/sync/enable`, `/api/sync/disable`, `/api/sync/status` routes |
| `ui/templates/base.html` | Add login button in top bar |
| `ui/static/js/core/auth.js` | Google Sign-In flow (OAuth popup) |
| `firebase_config.json` | Firebase project config (user creates this) |
| `admin_dashboard.html` | Super admin view (optional, can be a Firebase Hosting page) |

### `utils/firebase_sync.py` — Core sync module

```python
class FirebaseSync:
    def __init__(self, config_path: Path):
        """Initialize with firebase_config.json."""
        # Uses firebase-admin SDK or REST API

    def push_run_data(self, user_id, project_name, run_report):
        """Push run metrics (no code/goals) after each run."""

    def push_token_data(self, user_id, project_name, token_report):
        """Push token usage data."""

    def push_project_meta(self, user_id, project_name, knowledge):
        """Push project metadata (name, language, file count)."""

    def update_admin_aggregates(self, user_id):
        """Increment global counters (Cloud Function preferred)."""
```

### Integration points in existing code

1. **After each run** (`main.py` after `save_session_to_log`):
   ```python
   if firebase_sync.is_enabled():
       firebase_sync.push_run_data(user_id, project_name, token_report)
   ```

2. **On settings save** (`ui/app.py` in `api_settings_save`):
   ```python
   if firebase_sync.is_enabled():
       firebase_sync.push_settings(user_id, settings)
   ```

3. **On knowledge refresh** (`ui/app.py` in `api_knowledge_refresh`):
   ```python
   if firebase_sync.is_enabled():
       firebase_sync.push_project_meta(user_id, project_name, knowledge)
   ```

---

## User Flow

### First-time setup
```
1. User opens Settings → Cloud Sync section
2. Clicks "Enable Cloud Sync"
3. Prompt: "Login with Google to sync your data"
4. Google OAuth popup → user signs in
5. Bridge stores auth token locally
6. Data starts syncing automatically after each run
```

### Ongoing use
```
- Every run completion → metrics pushed to Firestore
- Every token report → pushed to Firestore
- Settings → synced across devices
- User can access data from any browser via Firebase web dashboard
```

### Super admin
```
- Logs in at admin.yourdomain.com
- Sees: 127 users, 342 projects, 18K tasks, 42M tokens saved
- Sees: daily active users chart, token usage trends
- Does NOT see: any user's code, goals, files, or project details
```

---

## What data is NEVER synced

| Data | Why not |
|---|---|
| Source code / file contents | Privacy — user's IP |
| Goal text / instructions | Could contain business logic |
| Diffs / code changes | Contains actual code |
| API keys / tokens | Security |
| File paths (full) | Could reveal system structure |
| Task instructions | Could contain sensitive requirements |
| Chat history | Private conversations |
| .env / config files | Credentials |

---

## Dependencies

```
pip install firebase-admin  # or use REST API (no dependency)
```

The REST API approach (using `urllib.request`) avoids adding `firebase-admin` (heavy dependency). Since we only need Firestore writes and Auth, REST is sufficient.

---

## Setup Steps (for the user)

1. Go to [Firebase Console](https://console.firebase.google.com)
2. Create a new project (or use existing)
3. Enable Authentication → Google Sign-In
4. Enable Firestore Database
5. Deploy security rules (provided above)
6. Download `firebase_config.json` from Project Settings → General → Web App
7. Place `firebase_config.json` in the bridge app root
8. Open bridge UI → Settings → Enable Cloud Sync → Login with Google

---

## Admin Dashboard Data

The super admin sees ONLY aggregate metrics:

```
┌── Admin Dashboard ──────────────────────────────────────────┐
│                                                              │
│  Users: 127          Projects: 342       Active today: 23   │
│                                                              │
│  Total Runs: 2,841   Tasks Done: 18,421  Avg Savings: 85%  │
│                                                              │
│  Token Usage                                                 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Supervisor: 4.2M tokens                                │  │
│  │ Aider:      6.8M tokens (local/free)                   │  │
│  │ Saved:     42.0M tokens vs direct coding               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  Projects (names only)                                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ shorts-uploader-engine  │ 12 runs │ 47 tasks │ JS     │  │
│  │ my-react-app            │  8 runs │ 32 tasks │ TS     │  │
│  │ api-service             │  5 runs │ 18 tasks │ Python │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ⚠ No access to: code, goals, files, diffs, settings       │
└──────────────────────────────────────────────────────────────┘
```
