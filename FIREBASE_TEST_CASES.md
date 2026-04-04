# Firebase Cloud Sync — Test Cases

> Covers all 6 milestones with unit, integration, edge case, and security tests
> Total: 78 test cases

---

## M1: Auth System (18 tests)

### M1.1 — Google OAuth Login Flow

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 1.1 | Login opens browser | Unit | Call `login_with_google()` | Browser opens Google consent URL with correct `client_id`, `redirect_uri=http://localhost:<port>/callback`, `scope=email profile` |
| 1.2 | Callback receives auth code | Unit | Simulate GET `http://localhost:<port>/callback?code=ABC123` | `OAuthCallbackHandler` captures code, responds 200 with "Login successful" HTML |
| 1.3 | Auth code exchanged for tokens | Unit | Call `_exchange_code("ABC123")` | POST to `identitytoolkit.googleapis.com` returns `id_token`, `refresh_token`, `local_id` (uid) |
| 1.4 | User profile extracted | Unit | Parse ID token payload | `uid`, `email`, `display_name` correctly extracted from JWT claims |
| 1.5 | Credentials saved to disk | Unit | After successful login, check file | `firebase_credentials.json` exists at `DATA_DIR` with `id_token`, `refresh_token`, `uid`, `email`, `token_expiry` |
| 1.6 | Credentials loaded on restart | Unit | Create sync instance with existing credentials file | `is_authenticated()` returns True, `_uid` and `_email` populated |
| 1.7 | Expired token auto-refreshes | Unit | Set `_token_expiry` to past, call `_get_token()` | New `id_token` obtained via refresh token API, `_token_expiry` updated |
| 1.8 | Refresh token failure | Unit | Set invalid `_refresh_token_str`, call `_get_token()` | Raises `AuthError`, `is_authenticated()` returns False |
| 1.9 | Logout clears credentials | Unit | Call `logout()` | `firebase_credentials.json` deleted, `is_authenticated()` returns False, `_uid` is None |
| 1.10 | API route `/api/auth/status` unauthenticated | Integration | GET `/api/auth/status` before login | Returns `{ loggedIn: false, email: null }` |
| 1.11 | API route `/api/auth/status` authenticated | Integration | Login, then GET `/api/auth/status` | Returns `{ loggedIn: true, email: "user@gmail.com", displayName: "..." }` |
| 1.12 | API route `/api/auth/logout` | Integration | POST `/api/auth/logout` | Returns `{ ok: true }`, subsequent `/api/auth/status` returns `loggedIn: false` |
| 1.13 | Login with no firebase_config.json | Edge | Remove config file, call login | Returns `{ error: "Firebase not configured..." }` with 400 |
| 1.14 | Login with invalid API key | Edge | Set wrong `apiKey` in config | Returns `{ error: "Invalid API key" }` with 401 |
| 1.15 | Concurrent login attempts | Edge | Call `login_with_google()` twice simultaneously | Second call returns `{ error: "Login already in progress" }` |
| 1.16 | Callback server timeout | Edge | Start login, don't complete OAuth within 60s | Callback server shuts down, returns `{ error: "Login timed out" }` |
| 1.17 | Credential file corruption | Edge | Write invalid JSON to credentials file | `_load_credentials()` handles gracefully, `is_authenticated()` returns False |
| 1.18 | Token refresh during sync | Edge | Start a sync, token expires mid-request | Sync retries with refreshed token automatically |

---

## M2: Data Sync Engine (22 tests)

### M2.1 — Firestore REST API

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 2.1 | Write document to Firestore | Unit | `_firestore_write("users/uid1/profile", {"email": "test@test.com"})` | PATCH request sent to correct URL with Bearer token, 200 response |
| 2.2 | Read document from Firestore | Unit | `_firestore_read("users/uid1/profile")` | GET request returns parsed document fields |
| 2.3 | Batch write (multiple docs) | Unit | `_firestore_batch_write([op1, op2, op3])` | Single batch commit request with 3 operations |
| 2.4 | String field conversion | Unit | `_to_firestore_fields({"name": "test"})` | Returns `{"name": {"stringValue": "test"}}` |
| 2.5 | Integer field conversion | Unit | `_to_firestore_fields({"count": 42})` | Returns `{"count": {"integerValue": "42"}}` |
| 2.6 | Boolean field conversion | Unit | `_to_firestore_fields({"active": True})` | Returns `{"active": {"booleanValue": true}}` |
| 2.7 | Float field conversion | Unit | `_to_firestore_fields({"rate": 85.5})` | Returns `{"rate": {"doubleValue": 85.5}}` |
| 2.8 | Array field conversion | Unit | `_to_firestore_fields({"tags": ["a","b"]})` | Returns proper arrayValue structure |
| 2.9 | Null field conversion | Unit | `_to_firestore_fields({"data": None})` | Returns `{"data": {"nullValue": null}}` |

### M2.2 — Data Sanitization

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 2.10 | Run data strips goal | Unit | `_sanitize_for_sync({"status":"ok","goal":"secret"}, RUN_ALLOWED)` | `"goal"` not in result, `"status"` present |
| 2.11 | Run data strips diff | Unit | Sanitize data with `"diff"` field | `"diff"` removed |
| 2.12 | Run data strips stderr | Unit | Sanitize data with `"stderr"` field | `"stderr"` removed |
| 2.13 | Run data strips repo_root | Unit | Sanitize data with `"repo_root"` | `"repo_root"` removed |
| 2.14 | Token data preserves numbers | Unit | Sanitize token report | `tokens_saved`, `savings_percent` preserved |
| 2.15 | Knowledge strips file paths | Unit | Sanitize knowledge data | File paths not included, only `file_count` |

### M2.3 — Push Functions

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 2.16 | push_run_data creates document | Integration | Complete a run with sync enabled | Document created at `users/{uid}/projects/{pid}/runs/{runId}` with correct fields |
| 2.17 | push_token_data creates document | Integration | Complete a run with sync enabled | Document created at `users/{uid}/projects/{pid}/tokens/{sessionId}` |
| 2.18 | push_project_meta creates document | Integration | Refresh knowledge with sync enabled | Document at `users/{uid}/projects/{pid}` updated with name, language, file_count |
| 2.19 | push_settings creates document | Integration | Save settings with sync enabled | Document at `users/{uid}/settings/global` updated |
| 2.20 | update_profile on login | Integration | Login successfully | Document at `users/{uid}/profile` created with email, os, gpu, app_version |

### M2.4 — Offline Queue

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 2.21 | Queue on network failure | Unit | Disconnect network, call `push_run_data()` | Operation added to `_offline_queue`, saved to `firebase_sync_queue.json` |
| 2.22 | Flush queue on reconnect | Unit | Queue 3 operations, reconnect, call `_flush_queue()` | All 3 operations executed, queue emptied |
| 2.23 | Queue persists across restart | Unit | Queue operation, restart app | Queue loaded from file, operation still present |
| 2.24 | Max retry limit (5) | Unit | Queue operation, fail 5 times | Operation removed from queue after 5th retry |
| 2.25 | Queue order preserved | Unit | Queue ops A, B, C, flush | Operations executed in order: A → B → C |

---

## M3: Firebase Setup & Security Rules (14 tests)

### M3.1 — Security Rules

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 3.1 | User reads own profile | Security | User A reads `users/A/profile` | Allowed (200) |
| 3.2 | User reads other's profile | Security | User A reads `users/B/profile` | Denied (403 Permission Denied) |
| 3.3 | User writes own data | Security | User A writes to `users/A/projects/p1` | Allowed (200) |
| 3.4 | User writes other's data | Security | User A writes to `users/B/projects/p1` | Denied (403) |
| 3.5 | User reads own runs | Security | User A reads `users/A/projects/p1/runs/r1` | Allowed |
| 3.6 | User reads other's runs | Security | User A reads `users/B/projects/p1/runs/r1` | Denied |
| 3.7 | Unauthenticated read | Security | No auth token, read any path | Denied (401) |
| 3.8 | Unauthenticated write | Security | No auth token, write any path | Denied (401) |
| 3.9 | Admin reads aggregates | Security | Admin email reads `admin/aggregates` | Allowed |
| 3.10 | Non-admin reads aggregates | Security | Regular user reads `admin/aggregates` | Denied |
| 3.11 | Client writes to admin | Security | Any client writes to `admin/aggregates` | Denied (only Cloud Functions can write) |
| 3.12 | User deletes own data | Security | User A deletes `users/A/projects/p1` | Allowed |
| 3.13 | User deletes other's data | Security | User A deletes `users/B/projects/p1` | Denied |
| 3.14 | Deep subcollection access | Security | User A reads `users/A/projects/p1/runs/r1` | Allowed (wildcard rule covers nested) |

### M3.2 — Cloud Functions

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 3.15 | onRunComplete increments counters | Integration | Create run document in Firestore | `admin/aggregates.total_runs` incremented by 1, `total_tasks_completed` incremented by task count |
| 3.16 | onRunComplete updates daily stats | Integration | Create run document | `admin/aggregates/daily_stats/{today}` updated with runs, tasks, tokens_saved |
| 3.17 | onUserCreate increments user count | Integration | New user signs up | `admin/aggregates.total_users` incremented by 1 |
| 3.18 | onUserCreate creates daily stat | Integration | New user signs up | `admin/aggregates/daily_stats/{today}.new_users` incremented |

---

## M4: Frontend Integration (12 tests)

### M4.1 — Auth UI

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 4.1 | Login button shows when logged out | UI | Open settings panel, not logged in | Button shows "Login", no email displayed |
| 4.2 | Login button triggers OAuth | UI | Click "Login" button | POST `/api/auth/login` called, browser opens |
| 4.3 | Email shows after login | UI | Login successfully | Email displayed next to button, button changes to "Logout" |
| 4.4 | Logout button clears state | UI | Click "Logout" | POST `/api/auth/logout` called, button reverts to "Login", email cleared |
| 4.5 | Auth state persists on page reload | UI | Login, reload page | `checkAuthStatus()` returns logged-in state, email still shown |

### M4.2 — Sync Toggle

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 4.6 | Sync toggle OFF by default | UI | Open settings panel (new user) | "Auto-sync after runs" toggle is OFF |
| 4.7 | Sync toggle requires login | UI | Toggle sync ON without login | Toast: "Login first to enable cloud sync" |
| 4.8 | Sync toggle enables sync | UI | Login, toggle sync ON | POST `/api/sync/enable` called, toggle stays ON |
| 4.9 | Sync toggle disables sync | UI | Toggle sync OFF | POST `/api/sync/disable` called |

### M4.3 — Status Bar

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 4.10 | Cloud status hidden when not logged in | UI | Load any page without login | `#sb-cloud-status` has `display:none` |
| 4.11 | Cloud status shows "Synced" after sync | UI | Complete a run with sync enabled | Status bar shows green dot + "Synced" |
| 4.12 | Cloud status shows "Syncing" during push | UI | Start sync, check immediately | Status bar shows animated dot + "Syncing..." |

---

## M5: Admin Dashboard (8 tests)

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 5.1 | Dashboard loads for admin | Integration | Admin navigates to `/admin` | Page loads, shows aggregate metrics |
| 5.2 | Dashboard blocked for non-admin | Integration | Regular user navigates to `/admin` | Access denied / redirect to login |
| 5.3 | Total users displayed | Integration | Check dashboard after 3 users signed up | Shows "Total users: 3" |
| 5.4 | Total runs displayed | Integration | Check after 10 runs across users | Shows "Total runs: 10" |
| 5.5 | Token savings calculated | Integration | Check after runs with savings data | Shows correct total_tokens_saved |
| 5.6 | Daily stats chart renders | Integration | Check dashboard with 7+ days of data | Chart shows daily active users for each day |
| 5.7 | No project content exposed | Security | Inspect all Firestore reads from dashboard | Only reads from `admin/aggregates`, never from `users/*/projects/*` |
| 5.8 | Project names visible, not content | Integration | Check project list on dashboard | Shows names like "my-app" but no file paths, goals, or code |

---

## M6: Polish & Security (4 tests)

### M6.1 — GDPR

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 6.1 | Data export | Integration | Call `GET /api/sync/export` | Returns JSON with all user's Firestore data |
| 6.2 | Account deletion | Integration | Call `POST /api/sync/delete-account` | All documents under `users/{uid}` deleted, credentials cleared, admin counter decremented |

### M6.2 — Error Handling

| # | Test Case | Type | Steps | Expected Result |
|---|---|---|---|---|
| 6.3 | Network timeout during sync | Edge | Set timeout to 1ms, call sync | Operation queued, no crash, toast "Sync failed, will retry" |
| 6.4 | Firestore quota exceeded | Edge | Simulate 429 response | Operation queued with backoff, log warning |

---

## Test Execution Matrix

### By Type

| Type | Count | How to Run |
|---|---|---|
| Unit | 31 | `python -m pytest tests/test_firebase_sync.py` |
| Integration | 23 | Requires Firebase project + test credentials |
| Security | 14 | Firebase emulator: `firebase emulators:start` + rules test SDK |
| UI | 6 | Manual or Playwright/Selenium |
| Edge | 4 | Unit test with mocked failures |
| **Total** | **78** | |

### By Milestone

| Milestone | Tests | Critical Path |
|---|---|---|
| M1: Auth | 18 | Login → token → refresh → credential storage |
| M2: Sync | 22 | Sanitize → write → queue → flush |
| M3: Rules | 18 | User isolation → admin access → Cloud Functions |
| M4: Frontend | 12 | Login UI → toggle → status indicator |
| M5: Admin | 8 | Dashboard loads → metrics correct → no data leak |
| M6: Polish | 4 | Export → delete → error handling |

### Priority Order

```
P0 (Block release):  3.1-3.8 (security rules), 2.10-2.15 (sanitization)
P1 (Must pass):      1.1-1.9 (auth flow), 2.16-2.20 (push functions)
P2 (Should pass):    2.21-2.25 (offline queue), 4.1-4.12 (UI)
P3 (Nice to have):   5.1-5.8 (admin), 6.1-6.4 (GDPR)
```

---

## Test Data Fixtures

### Mock User

```python
MOCK_USER = {
    "uid": "test_user_001",
    "email": "testuser@gmail.com",
    "display_name": "Test User",
}
```

### Mock Run Report

```python
MOCK_RUN_REPORT = {
    "status": "success",
    "goal": "SECRET GOAL TEXT — must be stripped",
    "tasks_planned": 5,
    "tasks_completed": 5,
    "tasks_failed": 0,
    "elapsed_seconds": 120,
    "supervisor": "claude",
    "model": "ollama/qwen2.5-coder:14b",
    "diff": "SECRET DIFF — must be stripped",
    "repo_root": "C:\\Users\\secret\\path — must be stripped",
    "savings": {
        "supervisor_tokens": 4200,
        "aider_tokens": 6000,
        "tokens_saved": 15800,
        "savings_percent": 79.0,
    },
}
```

### Mock Token Session

```python
MOCK_TOKEN_SESSION = {
    "session_id": "sess_001",
    "timestamp": "2026-04-04T12:00:00",
    "supervisor": {"plan_in": 800, "plan_out": 200, "review_in": 2400, "review_out": 600, "total": 4000},
    "aider": {"estimated_tokens": 6000, "tasks_executed": 5},
    "savings": {"estimated_direct_tokens": 25000, "tokens_saved": 15000, "savings_percent": 60.0},
}
```

---

## Firebase Emulator Setup (for local testing)

```bash
# Install emulators
firebase init emulators
# Select: Auth, Firestore, Functions

# Start emulators
firebase emulators:start

# Emulator URLs:
# Auth:      http://localhost:9099
# Firestore: http://localhost:8080
# Functions: http://localhost:5001

# In bridge app, set environment variable to use emulator:
export FIRESTORE_EMULATOR_HOST=localhost:8080
export FIREBASE_AUTH_EMULATOR_HOST=localhost:9099
```

### Emulator Test Script

```python
# tests/test_firebase_integration.py

import os
os.environ["FIRESTORE_EMULATOR_HOST"] = "localhost:8080"
os.environ["FIREBASE_AUTH_EMULATOR_HOST"] = "localhost:9099"

def test_full_sync_flow():
    """End-to-end: login → push data → verify in Firestore."""
    sync = FirebaseSync(config_path)

    # 1. Create test user in emulator
    sync._uid = "test_user"
    sync._id_token = "test_token"
    sync._authenticated = True

    # 2. Push run data
    sync.push_run_data("test-project", MOCK_RUN_REPORT)

    # 3. Read back from Firestore
    doc = sync._firestore_read("users/test_user/projects/test-project/runs/latest")

    # 4. Verify sensitive data stripped
    assert "goal" not in doc
    assert "diff" not in doc
    assert "repo_root" not in doc
    assert doc["tasks_completed"] == 5
    assert doc["savings_percent"] == 79.0
```
