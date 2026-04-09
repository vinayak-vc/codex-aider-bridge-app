// pages/monitor.js — Read-only live monitor dashboard

import { SSEClient } from '/static/js/core/sse.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

const $   = id => document.getElementById(id);
const esc = s  => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function fmtNum(n) {
  if (n == null || n === '') return '—';
  return Number(n).toLocaleString();
}

function fmtPct(n) {
  if (n == null) return '—';
  return Number(n).toFixed(1) + '%';
}

function fmtElapsed(sec) {
  if (!sec && sec !== 0) return '';
  if (sec < 60)   return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

function fmtWhen(ts) {
  if (!ts) return '—';
  try {
    const d    = new Date(ts.replace ? ts.replace(' ', 'T') : ts);
    if (isNaN(d)) return ts;
    const diff = Math.floor((Date.now() - d) / 1000);
    if (diff < 60)    return 'just now';
    if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch (_) { return String(ts); }
}

// ── State ─────────────────────────────────────────────────────────────────────

let _sse          = null;
let _tasks        = {};       // task_id → task object
let _totalTasks   = 0;
let _doneTasks    = 0;
let _runStatus    = 'idle';
let _runGoal      = '';
let _elapsedTimer = null;
let _runStart     = null;
let _logCount     = 0;
const MAX_LOG     = 200;

// ── Live dot / connection state ───────────────────────────────────────────────

function setLiveState(connected) {
  const dot   = $('live-dot');
  const label = $('live-label');
  if (!dot) return;
  dot.dataset.state  = connected ? 'live' : 'off';
  label.textContent  = connected ? 'Live' : 'Disconnected';
}

// ── Run banner ────────────────────────────────────────────────────────────────

const STATUS_LABEL = {
  idle: 'Idle', running: 'Running', paused: 'Paused',
  success: 'Complete', failure: 'Failed', stopped: 'Stopped',
  waiting_review: 'Waiting Review',
};

function updateBanner(status, goal, currentTask, done, total) {
  const banner = $('run-banner');
  if (banner) banner.dataset.status = status || 'idle';

  const badge = $('run-status-badge');
  if (badge) badge.textContent = STATUS_LABEL[status] || status;

  if (goal !== undefined) {
    const goalEl = $('run-goal');
    if (goalEl) goalEl.textContent = goal || 'No active run';
    _runGoal = goal || '';
  }

  if (currentTask !== undefined) {
    const taskEl = $('run-current-task');
    if (taskEl) taskEl.textContent = currentTask || '';
  }

  if (done !== undefined && total !== undefined) {
    const fill  = $('run-progress-fill');
    const label = $('run-progress-label');
    const pct   = total > 0 ? Math.round((done / total) * 100) : 0;
    if (fill)  fill.style.width = pct + '%';
    if (label) label.textContent = `${done} / ${total}`;
    _doneTasks  = done;
    _totalTasks = total;
  }
}

function startElapsedTimer() {
  _runStart = Date.now();
  clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    const el = $('run-elapsed');
    if (el && _runStart) el.textContent = fmtElapsed((Date.now() - _runStart) / 1000);
  }, 1000);
}

function stopElapsedTimer() {
  clearInterval(_elapsedTimer);
  _elapsedTimer = null;
}

// ── Task list ─────────────────────────────────────────────────────────────────

const TASK_STATUS_ICON = {
  approved: '✓', success: '✓',
  running:  '▶', retrying: '↺', rework: '↺',
  failure:  '✗', pending:  '○', skipped: '—',
};

const TASK_STATUS_CLASS = {
  approved: 'task-item--done',  success:  'task-item--done',
  running:  'task-item--active', retrying: 'task-item--active',
  rework:   'task-item--rework', failure:  'task-item--failed',
  pending:  'task-item--pending', skipped: 'task-item--muted',
};

function renderTasks() {
  const list  = $('task-list');
  const empty = $('tasks-empty');
  const badge = $('tasks-count-badge');
  if (!list) return;

  const items = Object.values(_tasks).sort((a, b) => a.id - b.id);

  if (!items.length) {
    list.style.display  = 'none';
    empty.style.display = '';
    if (badge) badge.textContent = '0';
    return;
  }

  list.style.display  = '';
  empty.style.display = 'none';
  if (badge) badge.textContent = String(items.length);

  list.innerHTML = items.map(t => {
    const status = t.status || 'pending';
    const icon   = TASK_STATUS_ICON[status] || '○';
    const cls    = TASK_STATUS_CLASS[status] || '';
    const file   = (t.files || [])[0] || '';
    const reworks = t.reworks > 0 ? `<span class="task-rework-badge">${t.reworks}×</span>` : '';
    const attempt = t.attempt > 1 ? `<span class="task-attempt">attempt ${t.attempt}</span>` : '';
    return `<li class="task-item ${cls}">
      <span class="task-icon">${esc(icon)}</span>
      <span class="task-id">${esc(t.id)}</span>
      <span class="task-file" title="${esc(file)}">${esc(file)}</span>
      ${reworks}${attempt}
    </li>`;
  }).join('');
}

function applyTaskEvent(data) {
  if (!data || !data.task) return;
  const t = data.task;
  _tasks[t.id] = { ..._tasks[t.id], ...t };
  renderTasks();
  updateBanner(_runStatus, undefined,
    `Task ${t.id} — ${(t.files || [])[0] || ''}`,
    _doneTasks, _totalTasks);
}

// ── Token panel ───────────────────────────────────────────────────────────────

function renderTokens(report) {
  if (!report) return;
  const sup  = report.supervisor || {};
  const aider= report.aider      || {};
  const sess = report.session    || {};
  const sav  = report.savings    || {};

  $('tokens-empty').style.display   = 'none';
  $('tokens-content').style.display = '';

  $('tok-plan-in').textContent  = fmtNum(sup.plan_in);
  $('tok-plan-out').textContent = fmtNum(sup.plan_out);
  $('tok-rev-in').textContent   = fmtNum(sup.review_in);
  $('tok-rev-out').textContent  = fmtNum(sup.review_out);
  $('tok-sup-total').textContent= fmtNum(sup.total);
  $('tok-aider').textContent    = '~' + fmtNum(aider.estimated_tokens);
  $('tok-reworks').textContent  = fmtNum(aider.reworks);
  $('tok-session').textContent  = fmtNum(sess.tokens);
  $('tok-total-ai').textContent = fmtNum(sess.total_ai_tokens);

  const pct = sav.savings_percent;
  if (pct != null) {
    $('savings-pct').textContent    = fmtPct(pct);
    $('savings-detail').textContent =
      `${fmtNum(sav.tokens_saved)} tokens saved vs direct`;
    $('savings-box').dataset.good   = pct >= 30 ? '1' : '0';
  }

  const updated = $('tokens-updated');
  if (updated) updated.textContent = 'just now';
}

async function loadTokens() {
  try {
    const res = await fetch('/api/tokens');
    if (!res.ok) return;
    const data = await res.json();
    // api/tokens returns { sessions, totals } — use latest session if available
    const sessions = data.sessions || [];
    if (sessions.length) renderTokens(sessions[sessions.length - 1]);
  } catch (_) {}
}

// ── Live log ──────────────────────────────────────────────────────────────────

function appendLog(line) {
  const container = $('log-lines');
  const empty     = $('log-empty');
  if (!container) return;

  if (empty) empty.style.display = 'none';

  _logCount++;
  // trim oldest lines to stay within MAX_LOG
  while (container.children.length >= MAX_LOG) {
    container.removeChild(container.firstChild);
  }

  const el = document.createElement('div');
  el.className   = 'log-line';
  el.textContent = line;
  container.appendChild(el);

  const autoscroll = $('log-autoscroll');
  if (autoscroll?.checked) {
    const viewport = $('log-viewport');
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }
}

// ── Blocking patterns ─────────────────────────────────────────────────────────

const PATTERN_SUGGESTIONS = {
  interactive_prompt:       'Add those files to context_files in your next plan.',
  timeout:                  'Increase --task-timeout or switch to a faster model.',
  silent_failure:           'Make instructions more specific — name the exact symbol.',
  repeated_validation_failure: 'Rewrite the instruction or use a larger model.',
  supervisor_rework_loop:   'Clarify acceptance criteria in the task instruction.',
  model_capability_gap:     'Consider using a larger/more capable model.',
  missing_dependency:       'Add the dependency as a prior task or to context_files.',
};

async function loadDiagnostics() {
  try {
    const res = await fetch('/api/reports/diagnostics');
    if (!res.ok) return;
    const data = await res.json();
    const patterns = data.blocking_patterns || [];
    const card  = $('patterns-card');
    const list  = $('pattern-list');
    const badge = $('patterns-count');
    if (!card || !list) return;

    const active = patterns.filter(p => (p.count || 0) > 0);
    if (!active.length) { card.style.display = 'none'; return; }

    card.style.display = '';
    if (badge) badge.textContent = String(active.length);
    list.innerHTML = active.map(p => `
      <li class="pattern-item">
        <div class="pattern-name">${esc(p.pattern || p.type || '?')}</div>
        <div class="pattern-tasks">Affected tasks: ${(p.tasks || []).join(', ') || '—'}</div>
        <div class="pattern-suggest">${esc(PATTERN_SUGGESTIONS[p.pattern] || p.suggestion || '')}</div>
      </li>`).join('');
  } catch (_) {}
}

// ── History ───────────────────────────────────────────────────────────────────

const STATUS_BADGE_CLS = {
  success: 'badge--success', failure: 'badge--danger',
  stopped: 'badge--warning', running: 'badge--accent',
};

async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    if (!res.ok) return;
    const entries = await res.json();
    const tbody = $('history-tbody');
    const table = $('history-table');
    const empty = $('history-empty');
    const badge = $('history-count');
    if (!tbody) return;

    if (!entries.length) {
      if (table) table.style.display = 'none';
      if (empty) empty.style.display = '';
      return;
    }

    if (table) table.style.display = '';
    if (empty) empty.style.display = 'none';
    if (badge) badge.textContent   = `${entries.length} run${entries.length !== 1 ? 's' : ''}`;

    tbody.innerHTML = entries.slice(0, 30).map(e => {
      const st  = e.status || 'unknown';
      const cls = STATUS_BADGE_CLS[st] || 'badge--muted';
      const done  = e.tasks_completed ?? e.completed_tasks ?? '—';
      const total = e.tasks_planned   ?? e.planned_tasks   ?? '—';
      return `<tr>
        <td class="text-subtle">${esc(fmtWhen(e.started_at || e.timestamp || ''))}</td>
        <td class="history-goal" title="${esc(e.goal || '')}">${esc(e.goal || '—')}</td>
        <td>${esc(done)} / ${esc(total)}</td>
        <td><span class="badge ${cls}">${esc(st)}</span></td>
        <td class="text-subtle">${fmtElapsed(e.elapsed_seconds)}</td>
      </tr>`;
    }).join('');
  } catch (_) {}
}

// ── SSE wiring ────────────────────────────────────────────────────────────────

function connectSSE() {
  _sse = new SSEClient('/api/run/stream');

  _sse.on('connect',      ()   => setLiveState(true));
  _sse.on('disconnect',   ()   => setLiveState(false));

  _sse.on('log',          data => appendLog(data.line || ''));

  _sse.on('start', data => {
    _tasks = {};
    _logCount = 0;
    const logLines = $('log-lines');
    if (logLines) logLines.innerHTML = '';
    const logEmpty = $('log-empty');
    if (logEmpty) logEmpty.style.display = 'none';
    _runStatus = 'running';
    startElapsedTimer();
    updateBanner('running', data.goal || '', '', 0, data.total_tasks || 0);
    _totalTasks = data.total_tasks || 0;
  });

  _sse.on('progress', data => {
    _doneTasks  = data.completed || 0;
    _totalTasks = data.total     || _totalTasks;
    updateBanner(_runStatus, undefined, undefined, _doneTasks, _totalTasks);
  });

  _sse.on('task_update', data => applyTaskEvent(data));

  _sse.on('token_report', data => {
    renderTokens(data);
  });

  _sse.on('complete', data => {
    _runStatus = data.status || 'success';
    stopElapsedTimer();
    updateBanner(_runStatus, undefined, '', _doneTasks, _totalTasks);
    // reload persistent data after run finishes
    setTimeout(() => { loadTokens(); loadHistory(); loadDiagnostics(); }, 1500);
  });

  _sse.on('error', data => {
    _runStatus = 'failure';
    stopElapsedTimer();
    updateBanner('failure', undefined, data.message || 'Run failed', _doneTasks, _totalTasks);
  });

  _sse.on('stopped', () => {
    _runStatus = 'stopped';
    stopElapsedTimer();
    updateBanner('stopped', undefined, '', _doneTasks, _totalTasks);
  });

  _sse.on('paused', () => {
    _runStatus = 'paused';
    updateBanner('paused', undefined, 'Run paused…', _doneTasks, _totalTasks);
  });

  _sse.on('resumed', () => {
    _runStatus = 'running';
    updateBanner('running', undefined, '', _doneTasks, _totalTasks);
  });

  _sse.connect();
}

// ── Bootstrap current run state ───────────────────────────────────────────────

async function loadCurrentRun() {
  try {
    const [statusRes, tasksRes] = await Promise.all([
      fetch('/api/run/status'),
      fetch('/api/run/tasks'),
    ]);
    if (statusRes.ok) {
      const s = await statusRes.json();
      _runStatus  = s.status  || 'idle';
      _doneTasks  = s.completed_tasks || 0;
      _totalTasks = s.total_tasks     || 0;
      updateBanner(_runStatus, s.goal || '', '', _doneTasks, _totalTasks);
      if (_runStatus === 'running') startElapsedTimer();
    }
    if (tasksRes.ok) {
      const list = await tasksRes.json();
      _tasks = {};
      (list || []).forEach(t => { _tasks[t.id] = t; });
      renderTasks();
    }
  } catch (_) {}
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  // Load all data in parallel
  await Promise.all([
    loadCurrentRun(),
    loadTokens(),
    loadHistory(),
    loadDiagnostics(),
  ]);

  // Connect live stream
  connectSSE();

  // Refresh history + tokens every 60s while page is open
  setInterval(() => { loadHistory(); loadTokens(); }, 60_000);
}

init();
