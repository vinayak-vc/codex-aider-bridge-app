# Bridge App UI Rebuild — Implementation Plan

> **Status:** COMPLETE — all milestones M1–M8 shipped
> **Branch:** `chatbot_llm`
> **Start from:** `H:/Vinayak_Project/codex-aider-bridge-app/.claude/worktrees/elastic-lamarr/`
> **Resumption rule:** Read this file, check git log for latest milestone commit, continue from next milestone.

---

## Why

The existing UI is a single `ui/templates/index.html` (1,434 lines, HTML + CSS + JS all in one file, 4 tabs).
It does not surface: `docs[]`, `clarifications[]`, `AI_UNDERSTANDING.md`, driver auto-detection, scope enforcement status, onboarding scan progress, RETRY verdict, or the `understanding_confirmed` field.
The new UI is a proper multi-page Flask app — monitoring dashboard first, with complete feature coverage.

---

## Design System

```
--color-bg:           #080c10
--color-surface:      #0d1117
--color-surface-2:    #161b22
--color-surface-3:    #21262d
--color-border:       #30363d
--color-border-muted: #21262d
--color-accent:       #3b82f6
--color-accent-glow:  rgba(59,130,246,.15)
--color-success:      #22c55e
--color-warning:      #f59e0b
--color-danger:       #ef4444
--color-info:         #06b6d4
--color-text:         #e2e8f0
--color-text-muted:   #94a3b8
--color-text-subtle:  #64748b
--radius-sm: 4px  --radius-md: 8px  --radius-lg: 12px  --radius-pill: 999px
--font-sans: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif
--font-mono: "Cascadia Code", "Fira Code", "JetBrains Mono", monospace
```

Light mode via `[data-theme="light"]` on `<html>`. Stored in `localStorage`.

---

## Target File Structure

```
ui/
├── app.py                          MODIFY: add 6 page routes + /api/reports/understanding
├── bridge_runner.py                NO CHANGE (except clarifications[] in M3)
├── state_store.py                  NO CHANGE
├── setup_checker.py                NO CHANGE
├── templates/
│   ├── base.html                   NEW: layout shell (sidebar nav, toast area, blocks)
│   ├── dashboard.html              NEW: live status, progress ring, task list
│   ├── run.html                    NEW: config form + live log
│   ├── knowledge.html              NEW: AI_UNDERSTANDING.md + file registry
│   ├── history.html                NEW: searchable run history table
│   ├── tokens.html                 NEW: token stats + savings charts
│   └── setup.html                  NEW: dependency checks + install
├── static/
│   ├── css/
│   │   ├── tokens.css              NEW: all CSS custom properties
│   │   ├── base.css                NEW: reset, typography, layout grid
│   │   ├── components.css          NEW: btn, card, badge, input, modal, toast, tabs
│   │   ├── nav.css                 NEW: fixed left sidebar (220px), responsive
│   │   ├── progress-ring.css       NEW: SVG ring + animations
│   │   └── pages/
│   │       ├── dashboard.css
│   │       ├── run.css
│   │       ├── knowledge.css
│   │       ├── history.css
│   │       ├── tokens.css
│   │       └── setup.css
│   └── js/
│       ├── core/
│       │   ├── api.js              NEW: apiFetch wrapper
│       │   ├── sse.js              NEW: SSEClient class
│       │   ├── store.js            NEW: reactive state store
│       │   ├── toast.js            NEW: toast notifications
│       │   └── theme.js            NEW: dark/light toggle
│       └── pages/
│           ├── dashboard.js        NEW: progress ring, task list, pause/resume, review
│           ├── run.js              NEW: form, launch, stop, log stream
│           ├── knowledge.js        NEW: knowledge + understanding render
│           ├── history.js          NEW: search, filter, modal, re-run
│           ├── tokens.js           NEW: charts, session detail
│           └── setup.js            NEW: check-grid, install terminals
└── data/                           NO CHANGE
```

---

## Backend Routes

### Keep unchanged (all existing routes)
```
GET /api/check, GET /api/ollama/models
POST /api/install/aider, POST /api/ollama/pull
GET|POST /api/settings
GET /api/browse/folder, GET /api/browse/file
POST /api/run, POST /api/run/stop
GET /api/run/status, GET /api/run/log, GET /api/run/tasks
POST /api/run/pause, POST /api/run/resume
GET /api/run/review/current, POST /api/run/review/submit
GET /api/run/stream
GET|DELETE /api/history, GET|DELETE /api/history/<id>
GET /api/tokens, GET /api/tokens/current
GET /api/reports/tokens, GET /api/reports/knowledge, GET /api/reports/last_run
```

### Add — new page routes
```python
GET /            → redirect to /dashboard
GET /dashboard   → render_template("dashboard.html")
GET /run         → render_template("run.html")
GET /knowledge   → render_template("knowledge.html")
GET /history     → render_template("history.html")
GET /tokens      → render_template("tokens.html")
GET /setup       → render_template("setup.html")
```

### Add — new data endpoint
```python
GET /api/reports/understanding?repo_root=<path>
  Reads: <repo_root>/bridge_progress/AI_UNDERSTANDING.md
  Returns: { "content": "<markdown string>", "exists": true }
  Fallback: uses saved settings repo_root if query param missing
```

### Add — bridge_runner.py (M3 only)
Extend `build_command()` to append `--clarification <item>` for each string in `settings.get("clarifications", [])`.

---

## Milestones

### M1 — Scaffold + Design System  ✅ TODO
**Commit message:** `feat(ui): scaffold multi-page layout with design system and navigation`

Files:
- `ui/app.py` — add 6 page routes + understanding endpoint
- `ui/templates/base.html` — sidebar nav, blocks, theme script, toast area
- `ui/static/css/tokens.css` — all CSS custom properties
- `ui/static/css/base.css` — reset, body, layout
- `ui/static/css/nav.css` — sidebar, active states, responsive
- `ui/static/js/core/theme.js` — dark/light toggle
- `ui/static/js/core/toast.js` — toast manager
- Placeholder templates for all 6 pages (extend base.html)

Delivers:
- 6 routes serving blank pages with correct nav
- Sidebar: Dashboard, Run, Knowledge, History, Tokens, Setup
- Dark theme by default, light mode toggle
- Run status badge in nav header
- Old `index.html` still accessible at `/legacy`

Acceptance:
1. `/dashboard` loads with sidebar, dark theme ✓
2. Click each nav item → URL changes, active state updates ✓
3. Toggle theme → survives page refresh ✓
4. `/legacy` still shows old index.html ✓

---

### M2 — Dashboard Page + Core JS Infrastructure
**Commit message:** `feat(ui): live dashboard with progress ring, task feed, and SSE`

Files:
- `ui/templates/dashboard.html`
- `ui/static/css/components.css`
- `ui/static/css/pages/dashboard.css`
- `ui/static/css/progress-ring.css`
- `ui/static/js/core/api.js`
- `ui/static/js/core/sse.js`
- `ui/static/js/core/store.js`
- `ui/static/js/pages/dashboard.js`

Delivers:
- SVG circular progress ring (0–100%)
- Live task feed with status badges (running / approved / rework / retrying / failure / dry-run / RETRY)
- Quick-stats row: total / completed / failed
- Pause / Resume buttons
- Review banner when `review_required` SSE event fires
- Driver chip (Claude / Codex / Cursor / Windsurf) from start event
- Scope enforcement chip on task cards

Acceptance:
1. Start a run → ring animates, tasks appear ✓
2. `review_required` event → review banner pins at top ✓
3. Pause → button state toggles ✓

---

### M3 — Run Page
**Commit message:** `feat(ui): run config form with all CLI flags, live log, and supervisor selector`

Files:
- `ui/templates/run.html`
- `ui/static/css/pages/run.css`
- `ui/static/js/pages/run.js`
- `ui/bridge_runner.py` — add `clarifications[]` to `build_command()`

Delivers:
- All CLI flags as form fields
- Supervisor selector: Codex / Claude / Cursor / Windsurf / Manual / Custom
- `clarifications[]` textarea (Advanced accordion)
- Command preview panel
- Live log terminal (auto-scroll toggle)
- Onboarding scan banner from SSE
- Ctrl+Enter shortcut

---

### M4 — Knowledge Page
**Commit message:** `feat(ui): project knowledge viewer with AI_UNDERSTANDING.md and docs signals`

Files:
- `ui/templates/knowledge.html`
- `ui/static/css/pages/knowledge.css`
- `ui/static/js/pages/knowledge.js`

Delivers:
- `AI_UNDERSTANDING.md` rendered as markdown (inline renderer, no library)
- `understanding_confirmed` badge
- `docs[]` Documentation Signals section
- `clarifications[]` Pending Clarifications section
- File registry table with client-side sort
- Features done / patterns / suggested next steps
- Empty state

---

### M5 — History Page
**Commit message:** `feat(ui): run history with search, filter, log viewer, and re-run`

Files:
- `ui/templates/history.html`
- `ui/static/css/pages/history.css`
- `ui/static/js/pages/history.js`

Delivers:
- Searchable, filterable run table
- Log viewer modal
- Re-run (restores settings, navigates to /run)
- Inline delete confirmation (no `confirm()` dialog)
- History count badge on nav item

---

### M6 — Tokens Page
**Commit message:** `feat(ui): token analytics with savings charts and session breakdown`

Files:
- `ui/templates/tokens.html`
- `ui/static/css/pages/tokens.css`
- `ui/static/js/pages/tokens.js`

Delivers:
- Stat chips: Sessions, Tasks, Session Tokens, Total AI Tokens, Tokens Saved
- Interactive-supervisor mode notice
- CSS horizontal bar chart per session
- Sessions table
- Savings bar
- Last session detail panel

---

### M7 — Setup Page + Manual Review Panel
**Commit message:** `feat(ui): setup dependency checks and manual review panel on dashboard`

Files:
- `ui/templates/setup.html`
- `ui/static/css/pages/setup.css`
- `ui/static/js/pages/setup.js`
- `ui/templates/dashboard.html` — add review panel
- `ui/static/js/pages/dashboard.js` — review polling + submit

Delivers:
- Dependency check cards (Python, Aider, Ollama, Codex, Claude)
- Aider install terminal (SSE stream)
- Ollama model manager
- Issues badge on Setup nav item
- Dashboard: manual review panel (task details, diff, approve/rework form)
- Scope enforcement chip in review panel

---

### M8 — Polish, Icons, Responsive, Cleanup
**Commit message:** `chore(ui): replace emoji with SVG icons, finalize responsive layout, remove legacy UI`

Files: all templates, all CSS, `ui/app.py`

Delivers:
- All emoji → inline SVG (heroicons style)
- Sidebar responsive: full→icon-only→bottom-bar
- Keyboard shortcuts: Ctrl+Enter, Esc, G+D, G+R
- PyInstaller static_folder fix
- SVG favicon
- Dynamic page titles
- Remove `index.html`, `/legacy` route
- `/` → redirects to `/dashboard`

---

## Component Reference

| Component | Class | Description |
|---|---|---|
| Layout | `.page-shell` | Body wrapper: sidebar + main |
| Nav | `.nav` | Fixed left sidebar |
| Nav item | `.nav-item[.--active]` | Icon + label link |
| Card | `.card[.--glass]` | Surface container |
| Stat | `.stat-chip` | Label + value + delta |
| Badge | `.badge.--success/warning/danger/info/muted` | Status pill |
| Progress ring | `.progress-ring` | SVG circle |
| Button | `.btn.--primary/secondary/danger/ghost` | Action button |
| Input | `.input` | Text input |
| Log terminal | `.log-terminal` | Monospace scroll pane |
| Task row | `.task-row` | Task item |
| Review banner | `.review-banner` | Pinned review CTA |
| Toast | `.toast` | Notification popup |
| Modal | `.modal-overlay > .modal` | Dialog |
| Section | `.section-header` | H2 + subtitle |
| Tabs | `.tabs > .tab[.--active]` | Sub-navigation |
| Empty state | `.empty-state` | Zero-data placeholder |

---

## SSE Events Reference

| Event | Key fields | Used by |
|---|---|---|
| `start` | command, run_id | Dashboard, Run |
| `plan_ready` | task_count | Dashboard, Run |
| `task_update` | task{id,files,status,attempt,reworks,instruction} | Dashboard, Run |
| `task_diff` | task_id, diff | Dashboard |
| `progress` | completed, total, percent | Dashboard |
| `log` | line | Run |
| `review_required` | task_id, request_file, validation_message, mode | Dashboard |
| `paused` | pause_file | Dashboard, Run |
| `resumed` | — | Dashboard, Run |
| `token_report` | report | Dashboard |
| `complete` | status, exit_code, elapsed, run_id | Dashboard, Run |
| `error` | message | Dashboard, Run |
| `stopped` | — | Dashboard, Run |

---

## Notes for Resuming

- **Never** use `confirm()` or `alert()` — use inline confirmations or toast
- **No npm, no bundler, no framework** — vanilla ES modules only
- **No markdown library** — implement minimal inline renderer in knowledge.js (~80 lines)
- Old `index.html` stays alive until M8 — do not delete it in M1–M7
- Each milestone is one git commit — do the full milestone before committing
- Test each milestone acceptance criteria before moving to next
