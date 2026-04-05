// pages/run.js — Wizard-style Run page controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';
import { play }      from '/static/js/core/sounds.js';
import { appendLog, clearLog, switchLogView, toggleTag, setAutoScroll, getAutoScroll } from '/static/js/pages/run-log.js';
import { openSettings, closeSettings, collectSettings, populateSettings, saveSettings, refreshFirebaseUI, showSetupStatus } from '/static/js/pages/run-settings.js';

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
// Log state + settings now in run-log.js and run-settings.js

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
    const settings = collectSettings(_planFile);
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
    const settings = collectSettings(_planFile);
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
  const settings = collectSettings(_planFile);
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
  const settings = collectSettings(_planFile);
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

// Log functions imported from run-log.js: appendLog, clearLog, switchLogView, toggleTag

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

// Settings functions imported from run-settings.js: openSettings, closeSettings, collectSettings, etc.

// ── Init ─────────────────────────────────────────────────────────────────────

function bindControls() {
  // Step 0
  $('wiz-btn-resume')?.addEventListener('click', resumeRun);
  $('wiz-btn-start-new')?.addEventListener('click', () => goToStep(1));
  $('wiz-btn-new-run')?.addEventListener('click', () => goToStep(1));

  // Step 1
  $('wiz-btn-generate')?.addEventListener('click', generatePlan);

  // Copy Prompt — fallback when Claude CLI subprocess fails
  $('wiz-btn-copy-prompt')?.addEventListener('click', async () => {
    const goal = ($('wiz-goal')?.value || '').trim();
    if (!goal) { toast('Enter a goal first.', 'warning'); return; }
    const settings = collectSettings(_planFile);
    try {
      const res = await apiPost('/api/run/nl/plan/prompt', {
        goal, repo_root: settings.repo_root, brief: { goal },
      });
      if (res.error) { toast(res.error, 'error'); return; }
      await navigator.clipboard.writeText(res.prompt);
      toast(
        `Prompt copied (${res.chars} chars). Paste into Claude, then import the JSON plan file.`,
        'success'
      );
    } catch (err) { toast(err.message || 'Failed to build prompt.', 'error'); }
  });
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
  $('btn-log-parsed')?.addEventListener('click', () => switchLogView('parsed'));
  $('btn-log-raw')?.addEventListener('click', () => switchLogView('raw'));
  document.querySelectorAll('.log-tag').forEach(btn => btn.addEventListener('click', () => toggleTag(btn.dataset.tag)));
  $('log-autoscroll')?.addEventListener('change', e => { setAutoScroll(e.target.checked); });
  $('btn-log-send')?.addEventListener('click', async () => {
    const input = $('log-stdin-input'); if (!input?.value?.trim()) return;
    try { await apiPost('/api/run/input', { text: input.value.trim() }); input.value = ''; } catch (_) {}
  });
  $('log-stdin-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('btn-log-send')?.click(); } });

  // Done overlay
  $('wiz-btn-new-after-done')?.addEventListener('click', () => { $('wiz-done-overlay').style.display = 'none'; goToStep(1); });
  $('wiz-btn-view-log')?.addEventListener('click', () => { $('wiz-done-overlay').style.display = 'none'; });

  // Settings panel
  $('wiz-btn-close-settings')?.addEventListener('click', () => closeSettings(() => _planFile));

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
      if (saveRes.error) { showSetupStatus(statusEl, saveRes.error, false); return; }
      // Test connection
      const testRes = await apiPost('/api/firebase/test', {});
      if (!testRes.ok) { showSetupStatus(statusEl, testRes.error || 'Connection failed', false); return; }
      // Login
      showSetupStatus(statusEl, 'Connecting... (browser will open for Google login)', true);
      const loginRes = await apiPost('/api/firebase/login', {});
      if (loginRes.error) { showSetupStatus(statusEl, loginRes.error, false); return; }
      showSetupStatus(statusEl, 'Connected! Email: ' + loginRes.email, true);
      setTimeout(refreshFirebaseUI, 1000);
    } catch (ex) {
      showSetupStatus(statusEl, 'Invalid JSON: ' + ex.message, false);
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
  $('settings-overlay')?.addEventListener('click', e => { if (e.target.id === 'settings-overlay') closeSettings(() => _planFile); });
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
