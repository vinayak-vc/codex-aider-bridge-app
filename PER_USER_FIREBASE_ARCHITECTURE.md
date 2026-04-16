# Per-User Firebase Architecture — Implementation Plan

> Each user gets their own Firebase project. Their data lives in their own Google Cloud account.
> The super admin (you) only receives anonymous aggregate metrics.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│ USER 1's MACHINE                                                        │
│                                                                         │
│  Bridge App                                                             │
│  ├── Project E, Project F                                               │
│  ├── Logs in with personal Google account                               │
│  ├── App auto-creates Firebase project in USER 1's Google Cloud         │
│  ├── All project data → USER 1's Firestore                             │
│  ├── Personal dashboard URL: user1-aiderbridge.web.app                 │
│  └── Sends ONLY aggregate metrics to Super Admin's Firebase             │
│       (task count, token count, project names — NO content)             │
│                                                                         │
└─────────────────┬───────────────────────────────┬───────────────────────┘
                  │                               │
                  ▼                               ▼
┌─────────────────────────────┐   ┌──────────────────────────────────────┐
│ USER 1's Firebase Project   │   │ SUPER ADMIN's Firebase Project       │
│ (user1-aiderbridge)         │   │ (aiderbridge-admin)                  │
│                             │   │                                      │
│ Firestore:                  │   │ Firestore:                           │
│ ├── projects/               │   │ ├── users/                           │
│ │   ├── project-e/          │   │ │   └── user1_hash/                  │
│ │   │   ├── runs/           │   │ │       ├── project_count: 2         │
│ │   │   ├── tokens/         │   │ │       ├── total_tasks: 47          │
│ │   │   └── knowledge/      │   │ │       ├── total_tokens_saved: 120K │
│ │   └── project-f/          │   │ │       ├── last_active: 2026-04-04  │
│ │       └── ...             │   │ │       └── projects: ["E","F"]      │
│ ├── settings/               │   │ │         (names only, no content)   │
│ └── profile/                │   │ ├── aggregates/                      │
│                             │   │ │   ├── total_users: 127             │
│ Auth: Owner's Google acct   │   │ │   ├── total_tasks: 18421          │
│ Hosting: personal dashboard │   │ │   └── total_tokens_saved: 42M     │
│ Rules: owner-only access    │   │ └── daily_stats/                     │
│                             │   │                                      │
│ Cost: FREE tier (per user)  │   │ Auth: Admin Google account only      │
└─────────────────────────────┘   └──────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ USER 2's MACHINE                                                        │
│                                                                         │
│  Bridge App                                                             │
│  ├── Project C, Project D                                               │
│  ├── Logs in with personal Google account                               │
│  ├── App auto-creates Firebase project in USER 2's Google Cloud         │
│  ├── All project data → USER 2's Firestore                             │
│  ├── Personal dashboard URL: user2-aiderbridge.web.app                 │
│  └── Sends ONLY aggregate metrics to Super Admin's Firebase             │
│                                                                         │
└─────────────────┬───────────────────────────────┬───────────────────────┘
                  │                               │
                  ▼                               ▼
┌─────────────────────────────┐   ┌──────────────────────────────────────┐
│ USER 2's Firebase Project   │   │ SUPER ADMIN's Firebase Project       │
│ (user2-aiderbridge)         │   │ (same as above)                      │
│ Firestore: their data only  │   │ Receives only: task count, tokens    │
└─────────────────────────────┘   └──────────────────────────────────────┘
```

---

## Key Differences from Shared Architecture

| Aspect | Shared (current) | Per-User (proposed) |
|---|---|---|
| **Firebase projects** | 1 (yours) | N+1 (1 per user + 1 admin) |
| **Data ownership** | Your project, rules-isolated | User's own Google Cloud project |
| **User setup** | Click Login — done | Auto-setup wizard (5 steps automated) |
| **Data location** | Your Firestore | User's Firestore |
| **Dashboard** | One shared URL | Per-user unique URL |
| **Privacy** | Rules-based isolation | Physical database separation |
| **User can delete** | Request via API | Delete their entire Firebase project |
| **Cost to you** | You pay all Firestore usage | Zero — each user on free tier |
| **Cost to user** | Zero | Zero (free tier: 1GB, 50K reads/day) |
| **Admin analytics** | Read from same Firestore | Separate ingest via REST push |

---

## The Hard Problem: Auto-Creating Firebase Projects

Firebase doesn't have a simple "create project via API" for end users. The options:

### Option A: Firebase CLI Automation (Recommended)

```
User clicks "Setup Cloud Sync" in the app
  → App checks if Firebase CLI is installed
  → If not: guides user to install (npm install -g firebase-tools)
  → App runs: firebase login (opens browser)
  → App runs: firebase projects:create aiderbridge-{userid}
  → App runs: firebase deploy (rules + hosting)
  → App stores the project config locally
  → Done — all automated, user approves once
```

**Pros:** Fully automated after CLI install
**Cons:** Requires Node.js + Firebase CLI

### Option B: Google Cloud REST API

```
User clicks "Setup Cloud Sync"
  → App uses Google OAuth to get Cloud Resource Manager token
  → App calls: POST https://cloudresourcemanager.googleapis.com/v1/projects
    { projectId: "aiderbridge-{hash}", name: "Aider Bridge" }
  → App calls: Firestore API to create database
  → App calls: Auth API to enable Google Sign-In
  → App deploys security rules via REST
  → Done — no CLI needed
```

**Pros:** No CLI dependency, pure REST API
**Cons:** Requires additional OAuth scopes (cloudresourcemanager, firebase), complex API dance

### Option C: Manual Setup with Guided Wizard (Simplest)

```
User clicks "Setup Cloud Sync"
  → App shows step-by-step wizard:
    1. "Go to console.firebase.google.com → Create Project"
    2. "Enable Authentication → Google Sign-In"
    3. "Create Firestore Database → Production Mode"
    4. "Go to Project Settings → Web App → Copy config"
    5. "Paste the config here: [textarea]"
  → App parses the pasted config
  → App deploys security rules via REST API
  → Done
```

**Pros:** No dependencies, works everywhere
**Cons:** User does 5 manual steps (but only once)

---

## Recommended Approach: Option C (Guided Wizard) + Auto-Deploy

The wizard handles the one-time setup. After the user pastes their Firebase config, the app automatically:
1. Saves the config locally
2. Deploys Firestore security rules via REST API
3. Tests the connection
4. Starts syncing

---

## Data Flow

### What stays in the user's Firebase:

```
User's Firestore (user's Google Cloud):
├── projects/
│   └── {projectName}/
│       ├── meta: { name, language, type, file_count, last_run }
│       ├── runs/{runId}: { status, tasks, elapsed, tokens, savings }
│       ├── tokens/{sessionId}: { plan, review, aider, saved, percent }
│       └── knowledge/latest: { patterns, features_done, file_count }
├── settings/global: { model, supervisor, auto_commit, timeout }
└── profile: { email, os, gpu, app_version, last_active }
```

### What gets sent to the super admin:

```
POST to admin's Firebase (your project):

{
  "user_hash": "a1b2c3",           // anonymized, no email
  "project_names": ["Project E", "Project F"],  // names only
  "total_tasks": 47,
  "total_runs": 12,
  "total_supervisor_tokens": 42000,
  "total_aider_tokens": 68000,
  "total_tokens_saved": 120000,
  "avg_savings_percent": 85.2,
  "last_active": "2026-04-04",
  "os": "Windows 11",
  "gpu": "RTX 3060",
  "app_version": "0.1"
}
```

**What is NEVER sent to admin:**
- Email address (only anonymized hash)
- Project file paths or content
- Goals, instructions, diffs
- Task details or error messages
- API keys or settings values

---

## Implementation Plan

### Phase 1: User Firebase Setup Wizard

**Files:**
- `utils/firebase_user_setup.py` — setup automation
- `ui/app.py` — setup wizard API routes
- `ui/templates/run.html` — wizard UI in settings panel

**Steps:**

```python
class FirebaseUserSetup:
    """Guides user through creating their own Firebase project."""

    def check_status(self) -> dict:
        """Check if user's Firebase is configured."""
        # Returns: { configured, project_id, has_auth, has_firestore }

    def save_user_config(self, config: dict) -> dict:
        """Save user's pasted Firebase config and validate it."""
        # 1. Parse the config JSON
        # 2. Test Firestore connection
        # 3. Save to %LOCALAPPDATA%/AiderBridge/user_firebase_config.json
        # 4. Return { ok, project_id }

    def deploy_rules(self) -> dict:
        """Deploy Firestore security rules to user's project via REST."""
        # PUT https://firestore.googleapis.com/v1/{project}/databases/(default)/documents
        # With rules that allow only the owner
```

**API Routes:**
```
POST /api/firebase/setup    — save user's config + validate
GET  /api/firebase/status   — check if configured
POST /api/firebase/test     — test Firestore read/write
```

**Wizard UI:**
```
┌── Cloud Sync Setup ──────────────────────────────────────────┐
│                                                               │
│  Step 1: Create a Firebase project                            │
│  → Go to console.firebase.google.com                         │
│  → Click "Add project" → name it anything                    │
│  → Enable Google Authentication                              │
│  → Create Firestore Database (Production mode)               │
│                                                               │
│  Step 2: Get your config                                      │
│  → Project Settings → General → Your apps → Web app          │
│  → Click "Add app" (web) → Copy the firebaseConfig object    │
│                                                               │
│  Step 3: Paste it here                                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ {                                                        │ │
│  │   "apiKey": "...",                                       │ │
│  │   "authDomain": "...",                                   │ │
│  │   "projectId": "...",                                    │ │
│  │   ...                                                    │ │
│  │ }                                                        │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  [Validate & Connect]                                         │
│                                                               │
│  Step 4: Enable OAuth (one-time)                              │
│  → Google Cloud Console → APIs & Credentials                 │
│  → Create OAuth Client ID (Web Application)                  │
│  → Copy Client ID and Secret, paste below:                   │
│                                                               │
│  Client ID:     [________________________]                    │
│  Client Secret: [________________________]                    │
│                                                               │
│  [Save & Complete Setup]                                      │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### Phase 2: Dual-Sync Engine

**Modified `utils/firebase_sync.py`:**

The sync engine writes to TWO destinations:
1. **User's Firestore** — full project data (runs, tokens, knowledge)
2. **Admin's Firestore** — aggregate metrics only (anonymized)

```python
class FirebaseSync:
    def __init__(self):
        self._user_config = None    # User's Firebase config
        self._admin_config = None   # Bundled admin Firebase config
        self._user_token = None     # User's Firebase auth token
        self._admin_token = None    # Admin project API key (no auth needed for writes)

    def push_run_data(self, project_name, run_report):
        # 1. Write to USER's Firestore (full data)
        self._user_firestore_write(
            f"projects/{project_name}/runs/{run_id}",
            sanitized_run_data
        )

        # 2. Write to ADMIN's Firestore (aggregates only)
        self._admin_firestore_write(
            f"users/{user_hash}",
            {
                "project_names": [project_name],
                "total_tasks": increment(tasks_completed),
                "total_tokens_saved": increment(tokens_saved),
                "last_active": now(),
            }
        )
```

### Phase 3: Personal Dashboard Deployment

After setup, deploy a static dashboard to the user's Firebase Hosting:

```python
def deploy_personal_dashboard(self) -> str:
    """Deploy the dashboard HTML to user's Firebase Hosting.
    Returns the dashboard URL."""

    # 1. Generate dashboard HTML with user's Firebase config embedded
    # 2. Upload to Firebase Hosting via REST API
    # 3. Return URL: https://{user_project_id}.web.app
```

The dashboard HTML is the same template but reads from the user's own Firestore.

### Phase 4: Admin Analytics Ingest

The admin project receives anonymous metrics via a public REST endpoint:

```python
# In the user's app, after each run:
def _push_to_admin(self, metrics: dict):
    """Push anonymous aggregate metrics to admin's Firestore."""
    admin_api_key = self._admin_config["apiKey"]
    user_hash = hashlib.sha256(self._user_email.encode()).hexdigest()[:12]

    data = {
        "user_hash": user_hash,  # No email, just hash
        "project_names": [m["name"] for m in metrics["projects"]],
        "total_tasks": metrics["total_tasks"],
        "total_tokens_saved": metrics["total_tokens_saved"],
        "os": platform.system(),
        "app_version": "0.1",
        "timestamp": datetime.now().isoformat(),
    }

    # Write to admin's Firestore using admin's API key
    # (Firestore rules allow anyone to write to users/{user_hash} with the API key)
    self._firestore_write_with_key(
        admin_api_key,
        self._admin_config["projectId"],
        f"users/{user_hash}",
        data
    )
```

---

## Firestore Security Rules

### User's Project Rules:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Only the project owner can read/write anything
    match /{document=**} {
      allow read, write: if request.auth != null;
    }
  }
}
```
Simple — since it's their own project, anyone authenticated is the owner.

### Admin Project Rules:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Users can write their own aggregate doc (anonymized)
    match /users/{userHash} {
      allow write: if true;  // Public write — data is anonymous anyway
      allow read: if false;  // No public read
    }

    // Admin can read everything
    match /{document=**} {
      allow read: if request.auth != null &&
        request.auth.token.email == 'chitrang@viitorcloud.com';
    }

    // Aggregates updated by Cloud Functions only
    match /aggregates/{document=**} {
      allow write: if false;
    }
  }
}
```

---

## File Inventory

| File | Lines (est.) | Purpose |
|---|---|---|
| `utils/firebase_user_setup.py` | ~200 | Setup wizard backend — validate config, test connection |
| `utils/firebase_sync.py` | ~500 (modify) | Dual-sync to user + admin Firestore |
| `ui/app.py` | ~60 (add) | Setup wizard API routes |
| `ui/templates/run.html` | ~50 (add) | Setup wizard UI in settings panel |
| `firebase/admin_rules.json` | ~20 | Admin project security rules |
| `firebase/user_rules_template.json` | ~10 | Template rules for user's project |
| `firebase/hosting/dashboard_template.html` | ~200 | Dashboard template (injected with user's config) |
| **Total new** | **~1,040** | |

---

## Milestone Breakdown

### P1: Setup Wizard (3-4 hours)
- [ ] `utils/firebase_user_setup.py` — config validation, connection test
- [ ] Setup wizard API routes (3 routes)
- [ ] Settings panel wizard UI (4 steps)
- [ ] Test: paste config → validate → connect

### P2: Dual-Sync Engine (2-3 hours)
- [ ] Modify `firebase_sync.py` — write to user's + admin's Firestore
- [ ] Admin push: anonymized metrics only (hash email, strip content)
- [ ] Test: run completes → data in user's Firestore + admin's Firestore

### P3: Personal Dashboard (2-3 hours)
- [ ] Dashboard template with injected config
- [ ] Deploy to user's Firebase Hosting via REST API
- [ ] Generate + display personal dashboard URL
- [ ] Test: dashboard shows user's own data

### P4: Admin Analytics (1-2 hours)
- [ ] Admin project Firestore rules
- [ ] Cloud Function for aggregating user pushes
- [ ] Admin dashboard reads from admin project
- [ ] Test: admin sees totals across all users

**Total estimated: 8-12 hours**

---

## User Experience Flow

```
FIRST TIME:

1. User opens bridge app → Settings → Cloud Sync
2. Sees: "Set up your personal cloud dashboard"
3. Clicks "Start Setup"
4. Wizard guides through:
   a. Create Firebase project (link to console)
   b. Enable Auth + Firestore (screenshots)
   c. Create OAuth credentials
   d. Paste config into the app
5. App validates config, tests connection
6. App deploys security rules to user's project
7. App shows: "Your dashboard: https://myproject.web.app"
8. Done — sync starts automatically

ONGOING:

- Every run → data pushed to user's Firestore
- Every run → anonymous metrics pushed to admin's Firestore
- User visits their personal URL → sees their data
- Admin visits admin URL → sees aggregate metrics

DATA OWNERSHIP:

- User owns their Firebase project completely
- User can delete the entire project at any time
- User can revoke the app's access at any time
- Admin never has access to user's Firestore
- Admin only sees: task count, token count, project names
```

---

## Comparison: What Admin Can See

| Data | Shared Architecture | Per-User Architecture |
|---|---|---|
| User email | Yes (in their profile) | No (only anonymized hash) |
| Project names | Yes | Yes (names only) |
| Project content | No (rules block) | No (separate database) |
| Task count | Yes | Yes |
| Token usage | Yes | Yes |
| Run history | Yes (in their partition) | No (stays in user's DB) |
| Goals/instructions | No | No |
| Code/diffs | No | No |
| Settings | Yes (in their partition) | No |
| Error messages | No | No |

The per-user architecture gives stronger privacy guarantees because the admin literally cannot access the user's database even if the rules were misconfigured.
