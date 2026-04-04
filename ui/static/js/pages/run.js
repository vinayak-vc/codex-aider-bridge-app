// pages/run.js — Wizard-style Run page controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';
import { play }      from '/static/js/core/sounds.js';

const $ = id => document.getElementById(id);
const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// ── Goal Templates ──────────────────────────────────────────────────────────

const GOAL_TEMPLATES = [
  { label: 'Add Feature',     tpl: 'Add a {feature_name} feature to {module}. It should {description}.' },
  { label: 'Fix Bug',         tpl: 'Fix the bug in {file_path} where {bug_description}.' },
  { label: 'Refactor',        tpl: 'Refactor {module} to use {pattern}. Keep existing tests passing.' },
  { label: 'Add Tests',       tpl: 'Write unit tests for {module} covering {scenarios}.' },
  { label: 'API Endpoint',    tpl: 'Create a {method} endpoint at {path} that {description}.' },
  { label: 'Security Review', tpl: 'Review {file_path} for security vulnerabilities and fix any found.' },
  { label: 'Read / Analyze',  tpl: 'Read {file_path} and tell me {question}.' },
];

// ── State ────────────────────────────────────────────────────────────────────

let _sse = null;
let _isRunning = false;
let _currentStep = 0;
let _planTasks = [];
let _planFile = '';
let _progressTasks = [];
let _lineCount = 0;
let _autoScroll = true;
let _logView = 'parsed';
let _tagCounts = { task: 0, review: 0, error: 0, warning: 0, bridge: 0, proxy: 0, aider: 0, info: 0 };
let _hiddenTags = new Set();

// ── Wizard Navigation ────────────────────────────────────────────────────────

function goToStep(step) {
  _currentStep = step;
  document.querySelectorAll('.wizard-step').forEach(el => {
    el.classList.toggle('--active', parseInt(el.dataset.step) === step);
  });
}

// ── Step 0: Check for pending work ───────────────────────────────────────────

async function checkPending() {
  try {
    const data = await fetch('/api/run/progress').then(r => r.json());
    const total = data.total_tasks || 0;
    const done = (data.completed || []).length;
    _planFile = data.plan_file || '';

    if (total > 0 && done < total && _planFile) {
      // Unfinished work found
      _progressTasks = data.tasks || [];
      const pct = Math.round(done / total * 100);
      const settings = await fetch('/api/settings').then(r => r.json());

      $('wiz-resume-project').textContent = settings.repo_root || 'Unknown project';
      $('wiz-resume-bar').style.width = pct + '%';
      $('wiz-resume-stats').textContent = `${done} / ${total} tasks (${pct}%) — Last status: ${data.last_status || '?'}`;
      $('wiz-resume-card').style.display = '';
      $('wiz-no-pending').style.display = 'none';
    } else {
      $('wiz-resume-card').style.display = 'none';
      $('wiz-no-pending').style.display = '';
    }
  } catch (_) {
    $('wiz-resume-card').style.display = 'none';
    $('wiz-no-pending').style.display = '';
  }
}

// ── Step 1: Goal ─────────────────────────────────────────────────────────────

function _showPlanLoader(title, sub) {
  const ov = $('plan-loading-overlay');
  if (!ov) return;
  ov.style.display = 'flex';
  const t = $('plan-loading-title');
  const s = $('plan-loading-sub');
  if (t) t.textContent = title || 'Generating plan via supervisor...';
  if (s) s.textContent = sub || 'This may take 10–30 seconds. Do not close this window.';
}
function _hidePlanLoader() {
  const ov = $('plan-loading-overlay');
  if (ov) ov.style.display = 'none';
}

async function generatePlan() {
  const goal = ($('wiz-goal')?.value || '').trim();
  if (!goal) { toast('Please enter a goal.', 'warning'); return; }

  const btn = $('wiz-btn-generate');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
  _showPlanLoader('Saving settings...', 'Preparing to generate plan...');

  try {
    // Save settings first
    const settings = collectSettings();
    settings.goal = goal;
    await apiPost('/api/settings', settings);

    // Send goal directly to supervisor (Claude/Codex) for plan generation.
    _showPlanLoader(
      'Supervisor is generating tasks...',
      'Claude is analyzing your goal and creating Aider-grade tasks. This may take 10–60 seconds.'
    );
    const brief = { goal };
    const plan = await apiPost('/api/run/nl/plan', { repo_root: settings.repo_root, brief });
    _planTasks = plan.tasks || [];

    if (!_planTasks.length) {
      toast('No tasks generated. Try a more specific goal.', 'warning');
      return;
    }

    // Save plan to file
    _showPlanLoader(
      `Saving plan (${_planTasks.length} tasks)...`,
      'Writing plan file to disk...'
    );
    const confirmed = await apiPost('/api/run/nl/plan/confirm', {
      repo_root: settings.repo_root,
      tasks: _planTasks,
      plan_summary: plan.plan_summary || '',
      brief,
    });
    _planFile = confirmed.plan_file || '';

    toast(`Plan generated: ${_planTasks.length} task(s)`, 'success');
    renderPlanReview();
    goToStep(2);
  } catch (err) {
    toast(err.message || 'Plan generation failed.', 'error');
  } finally {
    _hidePlanLoader();
    if (btn) { btn.disabled = false; btn.textContent = 'Generate Plan →'; }
  }
}

function renderPlanReview() {
  $('wiz-plan-count').textContent = `${_planTasks.length} tasks`;
  const list = $('wiz-task-list');
  if (!list) return;

  list.innerHTML = _planTasks.map((t, i) => `
    <div class="wiz-task-item">
      <span class="wiz-task-num">${t.id || i + 1}</span>
      <div class="wiz-task-body">
        <div class="wiz-task-title">${_esc(t.instruction || t.title || 'Task ' + (i + 1))}</div>
        <div class="wiz-task-files">${_esc((t.files || []).join(', '))}</div>
      </div>
      <span class="wiz-task-type">${_esc(t.type || '?')}</span>
    </div>
  `).join('');

  // Run preflight
  runPreflight();
}

async function runPreflight() {
  const wrap = $('wiz-preflight');
  const list = $('wiz-preflight-list');
  if (!wrap || !list) return;

  try {
    const settings = collectSettings();
    settings.plan_file = _planFile;
    const data = await apiPost('/api/run/preflight', settings);
    const checks = data.checks || [];
    if (!checks.length) { wrap.style.display = 'none'; return; }

    const icons = { pass: '&#x2713;', warn: '&#x26A0;', fail: '&#x2717;' };
    list.innerHTML = checks.map(c =>
      `<span class="preflight-item --${c.status}" title="${_esc(c.message)}">${icons[c.status] || '?'} ${_esc(c.name)}</span>`
    ).join('');

    // Cost estimate
    const est = data.estimate;
    if (est && est.task_count > 0) {
      list.innerHTML += `<span class="preflight-item --pass" style="margin-left:auto">
        ~${est.estimated_minutes}min | ~${(est.estimated_supervisor_tokens/1000).toFixed(0)}K sup | ~${(est.estimated_aider_tokens/1000).toFixed(0)}K aider
      </span>`;
    }
    wrap.style.display = '';
  } catch (_) { wrap.style.display = 'none'; }
}

// ── Step 3: Launch Run ───────────────────────────────────────────────────────

async function launchRun() {
  const settings = collectSettings();
  settings.plan_file = _planFile;
  if (!settings.repo_root) { toast('Set project folder in Settings.', 'warning'); return; }
  if (!_planFile) { toast('No plan file. Generate a plan first.', 'warning'); return; }

  // Populate progress panel from plan tasks
  _progressTasks = _planTasks.map(t => ({ ...t, status: 'pending' }));
  renderProgressPanel();

  _isRunning = true;
  goToStep(3);
  $('wiz-done-overlay').style.display = 'none';
  clearLog();
  connectSSE();

  try {
    play('launch');
    await apiPost('/api/settings', settings);
    await apiPost('/api/run', settings);
  } catch (err) {
    toast(err.message || 'Launch failed.', 'error');
    _isRunning = false;
  }
}

async function resumeRun() {
  const settings = collectSettings();
  settings.plan_file = _planFile;
  if (!_planFile) {
    try {
      const saved = await fetch('/api/settings').then(r => r.json());
      settings.plan_file = saved.plan_file || '';
      _planFile = settings.plan_file;
    } catch (_) {}
  }
  if (!settings.plan_file) { toast('No plan file found.', 'warning'); return; }

  renderProgressPanel();
  _isRunning = true;
  goToStep(3);
  $('wiz-done-overlay').style.display = 'none';
  clearLog();
  connectSSE();

  try {
    play('launch');
    await apiPost('/api/settings', settings);
    await apiPost('/api/run', settings);
  } catch (err) {
    toast(err.message || 'Resume failed.', 'error');
    _isRunning = false;
  }
}

// ── SSE ──────────────────────────────────────────────────────────────────────

function connectSSE() {
  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log', d => appendLog(d.line || ''))
    .on('start', () => {})
    .on('task_update', d => updateProgressFromSSE(d))
    .on('task_diff', d => {
      const t = _progressTasks.find(t => t.id === (d.task_id || 0));
      if (t) t.diff = d.diff;
    })
    .on('complete', d => {
      _isRunning = false;
      _sse.disconnect();
      const ok = d.status === 'success';
      showDone(ok, d.elapsed ? `${d.elapsed}s` : '');
      _sendNotification(ok ? 'Run Complete' : 'Run Failed', ok ? 'All tasks passed.' : 'Run had failures.');
    })
    .on('error', d => {
      _isRunning = false;
      _sse.disconnect();
      showDone(false, d.message || 'Error');
      _sendNotification('Run Error', d.message || 'Unknown error');
    })
    .on('stopped', () => {
      _isRunning = false;
      _sse.disconnect();
      showDone(false, 'Stopped by user');
    })
    .connect();
}

function showDone(success, detail) {
  const overlay = $('wiz-done-overlay');
  const icon = $('wiz-done-icon');
  const title = $('wiz-done-title');
  const sub = $('wiz-done-sub');
  if (!overlay) return;

  if (icon) icon.innerHTML = success
    ? '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-success)" width="48" height="48"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>'
    : '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-danger)" width="48" height="48"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>';
  if (title) title.textContent = success ? 'Run Complete' : 'Run Failed';
  if (sub) sub.textContent = detail;
  overlay.style.display = '';
}

// ── Log ──────────────────────────────────────────────────────────────────────

function appendLog(rawLine) {
  const terminal = $('log-terminal');
  if (terminal) {
    const span = document.createElement('span');
    let cls = '';
    if (/\|\s*(ERROR|CRITICAL)\s*\|/.test(rawLine)) cls = 'log-error';
    else if (/\|\s*WARNING\s*\|/.test(rawLine)) cls = 'log-warn';
    else if (/supervisor approved/.test(rawLine)) cls = 'log-ok';
    else if (/Bridge start|plan_ready/.test(rawLine)) cls = 'log-info';
    if (cls) span.className = cls;
    span.textContent = rawLine + '\n';
    terminal.appendChild(span);
    if (_logView === 'raw' && _autoScroll) terminal.scrollTop = terminal.scrollHeight;
  }

  _lineCount++;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = `${_lineCount} lines`;

  const parsed = _parseLine(rawLine);
  if (parsed) _appendParsedEvent(parsed);
}

function clearLog() {
  _lineCount = 0;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = '0 lines';
  const terminal = $('log-terminal');
  if (terminal) terminal.innerHTML = '';
  const parsed = $('log-parsed');
  if (parsed) { parsed.querySelectorAll('.parsed-event').forEach(e => e.remove()); const pe = $('log-parsed-empty'); if (pe) pe.style.display = ''; }
  _tagCounts = { task: 0, review: 0, error: 0, warning: 0, bridge: 0, proxy: 0, aider: 0, info: 0 };
  Object.keys(_tagCounts).forEach(tag => { const el = $(`tag-count-${tag}`); if (el) el.textContent = '0'; });
}

function _switchLogView(view) {
  _logView = view;
  $('btn-log-parsed')?.classList.toggle('--active', view === 'parsed');
  $('btn-log-raw')?.classList.toggle('--active', view === 'raw');
  const p = $('log-parsed'); if (p) p.style.display = view === 'parsed' ? '' : 'none';
  const r = $('log-terminal'); if (r) r.style.display = view === 'raw' ? '' : 'none';
  const t = $('log-tag-bar'); if (t) t.style.display = view === 'parsed' ? '' : 'none';
}

function _toggleTag(tag) {
  if (_hiddenTags.has(tag)) _hiddenTags.delete(tag); else _hiddenTags.add(tag);
  document.querySelectorAll('.log-tag').forEach(btn => btn.classList.toggle('--active', !_hiddenTags.has(btn.dataset.tag)));
  $('log-parsed')?.querySelectorAll('.parsed-event').forEach(ev => { ev.dataset.hidden = _hiddenTags.has(ev.dataset.tag) ? 'true' : 'false'; });
}

// ── Parsed Log ───────────────────────────────────────────────────────────────

function _parseLine(rawLine) {
  const tsMatch = rawLine.match(/(\d{2}:\d{2}:\d{2})/);
  const time = tsMatch ? tsMatch[1] : '';
  const line = rawLine;
  if (line.trim().startsWith('{"_bridge_event"')) return null;

  const I = (c) => `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="${c}" width="12" height="12">`;
  const icons = {
    play: `${I('var(--color-accent)')}<path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>`,
    check: `${I('var(--color-success)')}<path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>`,
    warn: `${I('var(--color-warning)')}<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>`,
    error: `${I('var(--color-danger)')}<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>`,
    info: `${I('var(--color-info)')}<path stroke-linecap="round" stroke-linejoin="round" d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/></svg>`,
  };

  let m;
  m = line.match(/Task\s+(\d+)\s*[—-]\s*attempt\s+(\d+)\/(\d+)\s*[—-]\s*files:\s*(.+)/);
  if (m) return { time, tag: 'task', cls: '--task', label: `Task ${m[1]}`, text: `Attempt ${m[2]}/${m[3]} — ${m[4].trim()}`, icon: icons.play };

  if (/supervisor approved|auto-approved/.test(line)) { m = line.match(/Task\s+(\d+)/); return { time, tag: 'review', cls: '--review', label: m ? `Task ${m[1]} Approved` : 'Approved', text: '', icon: icons.check }; }

  m = line.match(/supervisor requested rework[^:]*:\s*(.+)/);
  if (m) return { time, tag: 'review', cls: '--warning', label: 'Rework', text: m[1], icon: icons.warn };

  if (/\|\s*(ERROR|CRITICAL)\s*\|/.test(line)) return { time, tag: 'error', cls: '--error', label: 'Error', text: line.replace(/.*\|\s*(ERROR|CRITICAL)\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.error };
  if (/\|\s*WARNING\s*\|/.test(line)) return { time, tag: 'warning', cls: '--warning', label: 'Warning', text: line.replace(/.*\|\s*WARNING\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.warn };
  if (/Bridge start|Plan ready|Pre-flight|Loaded.*task|Project knowledge/.test(line)) return { time, tag: 'bridge', cls: '--info', label: 'Bridge', text: line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.info };
  if (/\[proxy\]/.test(line)) return { time, tag: 'proxy', cls: '--proxy', label: 'Proxy', text: line.replace(/.*\[proxy\]\s*/, ''), icon: icons.info };
  if (/\[bridge\]/.test(line)) return { time, tag: 'bridge', cls: '--info', label: 'Bridge', text: line.replace(/.*\[bridge\]\s*/, ''), icon: icons.info };
  if (/\[aider\]/.test(line)) return { time, tag: 'aider', cls: '--task', label: 'Aider', text: line.replace(/.*\[aider\]:\s*/, ''), icon: icons.play };
  if (/Git readiness|gitignore|Rollback point|undo all changes/.test(line)) return null;
  if (/\|\s*INFO\s*\|/.test(line)) return { time, tag: 'info', cls: '--info', label: '', text: line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), icon: '' };
  return null;
}

function _appendParsedEvent(p) {
  const container = $('log-parsed');
  if (!container) return;
  const emptyEl = $('log-parsed-empty');
  if (emptyEl) emptyEl.style.display = 'none';
  if (p.tag) { _tagCounts[p.tag] = (_tagCounts[p.tag] || 0) + 1; const el = $(`tag-count-${p.tag}`); if (el) el.textContent = _tagCounts[p.tag]; }
  const div = document.createElement('div');
  div.className = `parsed-event ${p.cls}`;
  div.dataset.tag = p.tag || '';
  if (_hiddenTags.has(p.tag)) div.dataset.hidden = 'true';
  div.innerHTML = `<span class="parsed-time">${_esc(p.time)}</span><span class="parsed-icon">${p.icon}</span><span class="parsed-content">${p.label ? `<span class="parsed-label">${_esc(p.label)}</span>` : ''}${_esc(p.text)}</span><span class="parsed-tag">${_esc(p.tag)}</span>`;
  container.appendChild(div);
  if (_autoScroll) container.scrollTop = container.scrollHeight;
}

// ── Task Progress ────────────────────────────────────────────────────────────

function renderProgressPanel() {
  const bar = $('task-progress-bar');
  const label = $('task-progress-label');
  const list = $('task-progress-list');
  if (!list) return;

  const done = _progressTasks.filter(t => t.status === 'done').length;
  const total = _progressTasks.length;
  const pct = total > 0 ? Math.round(done / total * 100) : 0;
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = `${done} / ${total} tasks (${pct}%)`;

  const statusIcons = {
    done: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-success)" width="12" height="12"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    running: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-accent)" width="12" height="12"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>',
    failed: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-danger)" width="12" height="12"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg>',
    pending: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="var(--color-text-subtle)" width="12" height="12"><circle cx="12" cy="12" r="9"/></svg>',
  };

  list.innerHTML = _progressTasks.map(t => {
    const s = t.status || 'pending';
    return `<div class="task-progress-item" data-task-id="${t.id}"><span class="task-status-icon --${s}">${statusIcons[s] || statusIcons.pending}</span><div class="task-progress-info"><div class="task-progress-title">${t.id}. ${_esc((t.instruction || '').slice(0, 40))}</div><div class="task-progress-files">${_esc((t.files || []).join(', '))}</div></div></div>`;
  }).join('');

  list.querySelectorAll('.task-progress-item').forEach(el => {
    el.addEventListener('click', () => selectTask(parseInt(el.dataset.taskId, 10)));
  });
}

function updateProgressFromSSE(data) {
  const taskId = data.task?.id || data.task_id;
  if (!taskId) return;
  const t = _progressTasks.find(t => t.id === taskId);
  if (t) {
    if (data.task?.status === 'approved') t.status = 'done';
    else if (data.task?.status === 'running') t.status = 'running';
  }
  renderProgressPanel();
}

function selectTask(taskId) {
  const task = _progressTasks.find(t => t.id === taskId);
  const pane = $('task-detail-pane');
  if (!pane || !task) { if (pane) pane.style.display = 'none'; return; }
  $('task-detail-title').textContent = `Task ${task.id} (${task.type || '?'}) — ${task.status || 'pending'}`;
  let html = `<p>${_esc(task.instruction || '')}</p>`;
  if (task.files?.length) html += `<p style="margin-top:4px;font-family:var(--font-mono);font-size:10px">Files: ${_esc(task.files.join(', '))}</p>`;
  if (task.diff) html += `<pre style="margin-top:8px;background:#020408;color:#c9d1d9;padding:6px;border-radius:4px;font-size:10px;overflow:auto;max-height:150px">${_esc(task.diff)}</pre>`;
  $('task-detail-body').innerHTML = html;
  pane.style.display = '';
}

// ── Notifications ────────────────────────────────────────────────────────────

function _sendNotification(title, body) {
  if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
    try { const n = new Notification(title, { body }); n.onclick = () => { window.focus(); n.close(); }; } catch (_) {}
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────

function openSettings() { $('settings-overlay').style.display = ''; refreshFirebaseUI(); }
function closeSettings() { $('settings-overlay').style.display = 'none'; saveSettings(); }

async function refreshFirebaseUI() {
  try {
    const status = await fetch('/api/firebase/status').then(r => r.json());
    const notSetup = $('firebase-not-setup');
    const wizard = $('firebase-wizard');
    const connected = $('firebase-connected');
    if (status.configured && status.authenticated) {
      if (notSetup) notSetup.style.display = 'none';
      if (wizard) wizard.style.display = 'none';
      if (connected) {
        connected.style.display = '';
        $('firebase-email').textContent = status.email || '';
        $('firebase-project-id').textContent = status.project_id || '';
      }
    } else if (status.configured) {
      if (notSetup) notSetup.style.display = 'none';
      if (wizard) wizard.style.display = '';
      if (connected) connected.style.display = 'none';
    } else {
      if (notSetup) notSetup.style.display = '';
      if (wizard) wizard.style.display = 'none';
      if (connected) connected.style.display = 'none';
    }
  } catch (_) {}
}

function _showSetupStatus(el, msg, success) {
  if (!el) return;
  el.style.display = '';
  el.style.background = success ? 'color-mix(in srgb, var(--color-success) 10%, transparent)' : 'color-mix(in srgb, var(--color-danger) 10%, transparent)';
  el.style.color = success ? 'var(--color-success)' : 'var(--color-danger)';
  el.textContent = msg;
}

function collectSettings() {
  const sup = document.querySelector('input[name="supervisor"]:checked')?.value || 'claude';
  return {
    repo_root: $('f-repo-root')?.value?.trim() || '',
    aider_model: $('f-aider-model')?.value?.trim() || '',
    supervisor: sup,
    manual_supervisor: true,
    supervisor_command: sup === 'custom' ? $('f-supervisor-command')?.value?.trim() || '' : '',
    validation_command: $('f-validation-cmd')?.value?.trim() || '',
    task_timeout: parseInt($('f-task-timeout')?.value || '600', 10),
    max_task_retries: parseInt($('f-max-retries')?.value || '10', 10),
    dry_run: $('f-dry-run')?.checked || false,
    model_lock: $('f-model-lock')?.checked || false,
    auto_commit: ($('sb-auto-commit')?.checked !== false),
    goal: $('wiz-goal')?.value?.trim() || '',
    plan_file: _planFile,
  };
}

function populateSettings(s) {
  if ($('f-repo-root')) $('f-repo-root').value = s.repo_root || '';
  if ($('f-aider-model')) $('f-aider-model').value = s.aider_model || '';
  const sup = s.supervisor || 'claude';
  const radio = document.querySelector(`input[name="supervisor"][value="${sup}"]`);
  if (radio) radio.checked = true;
  if ($('f-supervisor-command')) { $('f-supervisor-command').value = s.supervisor_command || ''; $('f-supervisor-command').style.display = sup === 'custom' ? '' : 'none'; }
  if ($('f-validation-cmd')) $('f-validation-cmd').value = s.validation_command || '';
  if ($('f-task-timeout')) $('f-task-timeout').value = s.task_timeout || 600;
  if ($('f-max-retries')) $('f-max-retries').value = s.max_task_retries || 10;
  if ($('f-dry-run')) $('f-dry-run').checked = !!s.dry_run;
  if ($('f-model-lock')) $('f-model-lock').checked = !!s.model_lock;
}

async function saveSettings() {
  try { await apiPost('/api/settings', collectSettings()); } catch (_) {}
}

// ── Init ─────────────────────────────────────────────────────────────────────

function bindControls() {
  // Step 0
  $('wiz-btn-resume')?.addEventListener('click', resumeRun);
  $('wiz-btn-start-new')?.addEventListener('click', () => goToStep(1));
  $('wiz-btn-new-run')?.addEventListener('click', () => goToStep(1));

  // Step 1
  $('wiz-btn-generate')?.addEventListener('click', generatePlan);
  $('wiz-btn-back-0')?.addEventListener('click', () => goToStep(0));
  $('wiz-btn-settings')?.addEventListener('click', openSettings);
  $('wiz-btn-load-plan')?.addEventListener('click', async () => {
    try {
      const d = await fetch('/api/browse/file?filter=json').then(r => r.json());
      if (!d.path) return;
      _planFile = d.path;
      const res = await apiPost('/api/run/import-plan', { plan_file: d.path });
      _planTasks = res.tasks || [];
      renderPlanReview();
      goToStep(2);
      toast(`Loaded ${_planTasks.length} tasks`, 'success');
    } catch (err) { toast(err.message || 'Failed to load plan.', 'error'); }
  });

  // Goal templates
  const bar = $('goal-template-bar');
  if (bar) {
    bar.innerHTML = GOAL_TEMPLATES.map(t => `<button class="goal-template-chip" data-tpl="${_esc(t.tpl)}">${_esc(t.label)}</button>`).join('');
    bar.addEventListener('click', e => {
      const btn = e.target.closest('.goal-template-chip');
      if (btn && $('wiz-goal')) { $('wiz-goal').value = btn.dataset.tpl; $('wiz-goal').focus(); }
    });
  }

  // Step 2
  $('wiz-btn-back-1')?.addEventListener('click', () => goToStep(1));
  $('wiz-btn-regenerate')?.addEventListener('click', generatePlan);
  $('wiz-btn-launch')?.addEventListener('click', launchRun);

  // Step 3
  $('wiz-btn-stop')?.addEventListener('click', async () => { try { await fetch('/api/run/stop', { method: 'POST' }); } catch (_) {} });
  $('wiz-btn-back-from-log')?.addEventListener('click', () => {
    if (_isRunning) {
      if (!confirm('A run is active. Stop it and go back?')) return;
      fetch('/api/run/stop', { method: 'POST' }).catch(() => {});
      _isRunning = false;
    }
    goToStep(1);
  });
  $('btn-log-parsed')?.addEventListener('click', () => _switchLogView('parsed'));
  $('btn-log-raw')?.addEventListener('click', () => _switchLogView('raw'));
  document.querySelectorAll('.log-tag').forEach(btn => btn.addEventListener('click', () => _toggleTag(btn.dataset.tag)));
  $('log-autoscroll')?.addEventListener('change', e => { _autoScroll = e.target.checked; });
  $('btn-log-send')?.addEventListener('click', async () => {
    const input = $('log-stdin-input'); if (!input?.value?.trim()) return;
    try { await apiPost('/api/run/input', { text: input.value.trim() }); input.value = ''; } catch (_) {}
  });
  $('log-stdin-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('btn-log-send')?.click(); } });

  // Done overlay
  $('wiz-btn-new-after-done')?.addEventListener('click', () => { $('wiz-done-overlay').style.display = 'none'; goToStep(1); });
  $('wiz-btn-view-log')?.addEventListener('click', () => { $('wiz-done-overlay').style.display = 'none'; });

  // Settings panel
  $('wiz-btn-close-settings')?.addEventListener('click', closeSettings);

  // Per-user Firebase setup
  $('btn-firebase-wizard')?.addEventListener('click', () => {
    $('firebase-not-setup').style.display = 'none';
    $('firebase-wizard').style.display = '';
  });
  $('btn-firebase-cancel')?.addEventListener('click', () => {
    $('firebase-wizard').style.display = 'none';
    $('firebase-not-setup').style.display = '';
  });
  $('btn-firebase-save')?.addEventListener('click', async () => {
    const raw = $('f-firebase-config')?.value?.trim();
    if (!raw) { toast('Paste your Firebase config JSON.', 'warning'); return; }
    const statusEl = $('firebase-setup-status');
    try {
      const config = JSON.parse(raw);
      // Save config
      const saveRes = await apiPost('/api/firebase/setup', config);
      if (saveRes.error) { _showSetupStatus(statusEl, saveRes.error, false); return; }
      // Test connection
      const testRes = await apiPost('/api/firebase/test', {});
      if (!testRes.ok) { _showSetupStatus(statusEl, testRes.error || 'Connection failed', false); return; }
      // Login
      _showSetupStatus(statusEl, 'Connecting... (browser will open for Google login)', true);
      const loginRes = await apiPost('/api/firebase/login', {});
      if (loginRes.error) { _showSetupStatus(statusEl, loginRes.error, false); return; }
      _showSetupStatus(statusEl, 'Connected! Email: ' + loginRes.email, true);
      setTimeout(refreshFirebaseUI, 1000);
    } catch (ex) {
      _showSetupStatus(statusEl, 'Invalid JSON: ' + ex.message, false);
    }
  });
  $('btn-firebase-logout')?.addEventListener('click', async () => {
    await fetch('/api/firebase/logout', { method: 'POST' });
    refreshFirebaseUI();
  });
  $('btn-firebase-export-dashboard')?.addEventListener('click', () => {
    window.open('/api/firebase/export-dashboard', '_blank');
  });
  $('btn-firebase-remove')?.addEventListener('click', async () => {
    if (!confirm('Remove Firebase config? This will stop cloud sync.')) return;
    await fetch('/api/firebase/clear', { method: 'POST' });
    refreshFirebaseUI();
  });
  $('settings-overlay')?.addEventListener('click', e => { if (e.target.id === 'settings-overlay') closeSettings(); });
  $('btn-browse-folder')?.addEventListener('click', async () => {
    try { const d = await fetch('/api/browse/folder').then(r => r.json()); if (d.path) $('f-repo-root').value = d.path; } catch (_) {}
  });
  document.querySelectorAll('input[name="supervisor"]').forEach(r => {
    r.addEventListener('change', () => { $('f-supervisor-command').style.display = r.value === 'custom' ? '' : 'none'; });
  });

  // Keyboard shortcuts
  $('wiz-goal')?.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generatePlan(); } });
}

async function init() {
  bindControls();

  // Load saved settings
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    populateSettings(settings);
  } catch (_) {}

  // Check if a run is active
  try {
    const status = await fetch('/api/run/status').then(r => r.json());
    if (status.is_running || status.status === 'running') {
      _isRunning = true;
      goToStep(3);
      connectSSE();
      const log = await fetch('/api/run/log').then(r => r.json());
      if (Array.isArray(log.lines)) log.lines.forEach(l => appendLog(l));
      return;
    }
  } catch (_) {}

  // Check for pending work
  await checkPending();

  // Request notification permission
  if ('Notification' in window) Notification.requestPermission().catch(() => {});
}

init();
