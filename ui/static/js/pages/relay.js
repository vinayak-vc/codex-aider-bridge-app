// pages/relay.js — AI Relay wizard controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

// ── State ─────────────────────────────────────────────────────────────────────

let _step          = 1;
let _goal          = '';
let _repoRoot      = '';
let _aiderModel    = '';
let _tasks         = [];
let _currentTaskId = null;
let _sse           = null;
let _completedTasks = 0;
let _totalTasks    = 0;

// ── Step navigation ───────────────────────────────────────────────────────────

function goToStep(n) {
  _step = n;
  for (let i = 1; i <= 3; i++) {
    const panel = $(`relay-panel-${i}`);
    if (panel) panel.style.display = i === n ? '' : 'none';
  }
  // Update step indicators
  for (let i = 1; i <= 3; i++) {
    const ind = $(`step-indicator-${i}`);
    if (!ind) continue;
    ind.dataset.active = String(i === n);
    ind.dataset.done   = String(i < n);
  }
}

// ── Step 1 helpers ────────────────────────────────────────────────────────────

async function generatePrompt() {
  _goal      = ($('relay-goal')?.value || '').trim();
  _repoRoot  = ($('relay-repo-root')?.value || '').trim();
  _aiderModel= ($('relay-aider-model')?.value || 'ollama/mistral').trim();

  if (!_goal) {
    toast('Please enter a goal.', 'warning');
    $('relay-goal')?.focus();
    return;
  }

  const btn = $('btn-generate-prompt');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }

  try {
    const data = await apiPost('/api/relay/generate-prompt', {
      goal: _goal,
      repo_root: _repoRoot,
    });
    const box = $('prompt-output');
    if (box) box.textContent = data.prompt;
    const wrap = $('prompt-output-wrap');
    if (wrap) wrap.style.display = '';
    const pasteWrap = $('plan-paste-wrap');
    if (pasteWrap) pasteWrap.style.display = '';
  } catch (err) {
    toast(err.message || 'Failed to generate prompt.', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Generate Prompt'; }
  }
}

async function importPlan() {
  const raw = ($('plan-paste')?.value || '').trim();
  const errEl = $('import-plan-error');
  if (errEl) errEl.style.display = 'none';

  if (!raw) {
    toast('Please paste the AI response first.', 'warning');
    return;
  }

  const btn = $('btn-import-plan');
  if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }

  try {
    const data = await apiPost('/api/relay/import-plan', { raw_text: raw });
    _tasks = data.tasks || [];
    renderTaskList(_tasks);
    goToStep(2);
  } catch (err) {
    const msg = err.message || 'Failed to parse plan.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Import Plan'; }
  }
}

// ── Step 2 helpers ────────────────────────────────────────────────────────────

function renderTaskList(tasks) {
  const list = $('relay-task-list');
  if (!list) return;
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<p class="text-subtle" style="font-size:var(--font-size-sm)">No tasks found.</p>';
    return;
  }
  list.innerHTML = tasks.map(t => `
    <div class="relay-task-item">
      <div class="relay-task-num">${t.id}</div>
      <div class="relay-task-body">
        <div class="relay-task-title">${escHtml(t.title || '')}</div>
        <div class="relay-task-instruction">${escHtml(t.instruction || '')}</div>
        ${t.files && t.files.length ? `<div class="relay-task-files">${t.files.map(escHtml).join(', ')}</div>` : ''}
      </div>
    </div>`).join('');
}

// ── Step 3 helpers ────────────────────────────────────────────────────────────

async function launchRun() {
  const settings = {
    goal:           _goal,
    repo_root:      _repoRoot,
    aider_model:    _aiderModel,
    supervisor:     'ai_relay',
    manual_supervisor: true,
    workflow_profile: 'standard',
  };

  goToStep(3);
  setRelayStatus('running', 'Running…');
  updateProgress(0, _tasks.length);

  connectSSE();

  try {
    await apiPost('/api/run', settings);
  } catch (err) {
    setRelayStatus('failure', 'Failed to start');
    toast(err.message || 'Failed to start run.', 'error');
    _sse?.disconnect();
  }
}

function connectSSE() {
  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');

  _sse
    .on('log',                 d => appendLog(d.line || ''))
    .on('relay_review_needed', d => onReviewNeeded(d))
    .on('review_required',     d => onReviewNeeded(d))
    .on('progress',            d => updateProgress(d.completed, d.total))
    .on('plan_ready',          d => { _totalTasks = d.task_count || 0; updateProgress(0, _totalTasks); })
    .on('complete',            d => onRunComplete(d))
    .on('error',               d => onRunComplete({ status: 'failure', message: d.message }))
    .on('stopped',             ()=> onRunComplete({ status: 'stopped' }))
    .connect();
}

function appendLog(line) {
  const box = $('relay-log');
  if (!box) return;
  box.textContent += line + '\n';
  box.scrollTop = box.scrollHeight;
}

function setRelayStatus(status, label) {
  const chip  = $('relay-status-chip');
  const lbl   = $('relay-status-label');
  if (chip)  chip.dataset.status = status;
  if (lbl)   lbl.textContent = label;
}

function updateProgress(done, total) {
  _completedTasks = done;
  _totalTasks     = total;
  const pct  = total > 0 ? Math.round(done / total * 100) : 0;
  const bar  = $('relay-progress-bar');
  const lbl  = $('relay-progress-label');
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = `${done} / ${total}`;
}

async function onReviewNeeded(data) {
  _currentTaskId = data.task_id;
  setRelayStatus('waiting_review', 'Waiting for review…');

  const panel  = $('relay-review-panel');
  const tidEl  = $('relay-review-task-id');
  const attBadge = $('relay-attempt-badge');
  if (tidEl)   tidEl.textContent = _currentTaskId;
  if (attBadge) attBadge.textContent = `attempt ${data.attempt || 1}`;

  // Fetch review packet
  try {
    const params = new URLSearchParams({
      task_id:   _currentTaskId,
      repo_root: _repoRoot,
      goal:      _goal,
    });
    const resp = await fetch(`/api/relay/review-packet?${params}`);
    const d    = await resp.json();
    if (resp.ok) {
      const box = $('relay-review-packet');
      if (box) box.textContent = d.packet;
    }
  } catch (_) {}

  // Clear old inputs
  const decPaste = $('relay-decision-paste');
  if (decPaste) decPaste.value = '';
  const decErr = $('relay-decision-error');
  if (decErr)  decErr.style.display = 'none';
  const replanWrap = $('relay-replan-wrap');
  if (replanWrap) replanWrap.style.display = 'none';

  if (panel) panel.style.display = '';
}

async function submitDecision() {
  const raw = ($('relay-decision-paste')?.value || '').trim();
  const errEl = $('relay-decision-error');
  if (errEl) errEl.style.display = 'none';

  if (!raw) {
    toast('Please paste the AI\'s decision.', 'warning');
    return;
  }

  const btn = $('btn-submit-relay-decision');
  if (btn) { btn.disabled = true; btn.textContent = 'Submitting…'; }

  try {
    const data = await apiPost('/api/relay/submit-decision', {
      raw_text:  raw,
      task_id:   _currentTaskId,
      repo_root: _repoRoot,
    });

    if (data.decision === 'fail') {
      // Show replan section
      await loadReplanPrompt();
    } else {
      // Hide review panel — bridge will continue
      const panel = $('relay-review-panel');
      if (panel) panel.style.display = 'none';
      setRelayStatus('running', 'Running…');
      toast(`Decision submitted: ${data.decision}`, 'success');
    }
  } catch (err) {
    const msg = err.message || 'Failed to submit decision.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Submit Decision'; }
  }
}

async function loadReplanPrompt() {
  try {
    const data = await apiPost('/api/relay/replan-prompt', {
      task_id:       _currentTaskId,
      repo_root:     _repoRoot,
      goal:          _goal,
      failed_reason: ($('relay-decision-paste')?.value || '').replace(/^FAILED:\s*/i, '').trim(),
    });
    const box = $('relay-replan-packet');
    if (box) box.textContent = data.prompt;
    const wrap = $('relay-replan-wrap');
    if (wrap) wrap.style.display = '';
  } catch (err) {
    toast('Could not generate replan prompt: ' + err.message, 'error');
  }
}

async function submitReplan() {
  const raw = ($('relay-replan-paste')?.value || '').trim();
  const errEl = $('relay-replan-error');
  if (errEl) errEl.style.display = 'none';

  if (!raw) {
    toast('Please paste the replacement tasks.', 'warning');
    return;
  }

  const btn = $('btn-submit-replan');
  if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }

  try {
    const data = await apiPost('/api/relay/import-replan', {
      raw_text: raw,
      task_id:  _currentTaskId,
    });
    _tasks = data.tasks || [];
    toast(`Replan imported: ${data.count} tasks remaining.`, 'success');
    const panel = $('relay-review-panel');
    if (panel) panel.style.display = 'none';
    setRelayStatus('running', 'Running…');
  } catch (err) {
    const msg = err.message || 'Failed to import replan.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Import Replacement Tasks'; }
  }
}

function onRunComplete(data) {
  const status  = data.status || 'failure';
  const elapsed = data.elapsed ? ` in ${data.elapsed}s` : '';
  setRelayStatus(status, status.charAt(0).toUpperCase() + status.slice(1));

  const panel = $('relay-review-panel');
  if (panel) panel.style.display = 'none';
  const done = $('relay-done-panel');
  if (done) done.style.display = '';

  const icon = done?.querySelector('.relay-done-icon');
  if (icon) icon.dataset.failed = String(status === 'failure');

  const title = $('relay-done-title');
  const sub   = $('relay-done-sub');
  if (title) title.textContent = status === 'success' ? 'Run complete!' : status === 'stopped' ? 'Run stopped' : 'Run failed';
  if (sub)   sub.textContent   = `${_completedTasks} / ${_totalTasks} tasks completed${elapsed}.`;

  _sse?.disconnect();
}

// ── Copy helpers ──────────────────────────────────────────────────────────────

function copyText(elementId, btnId) {
  const text = $(elementId)?.textContent || '';
  navigator.clipboard?.writeText(text).then(() => {
    const btn = $(btnId);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    }
  }).catch(() => toast('Copy failed — please copy manually.', 'warning'));
}

// ── Reset ─────────────────────────────────────────────────────────────────────

function resetWizard() {
  _step = 1;
  _goal = ''; _repoRoot = ''; _aiderModel = '';
  _tasks = []; _currentTaskId = null;
  _completedTasks = 0; _totalTasks = 0;
  _sse?.disconnect(); _sse = null;

  // Clear fields
  const fields = ['relay-goal','relay-repo-root','relay-aider-model',
                  'plan-paste','relay-decision-paste','relay-replan-paste'];
  fields.forEach(id => { const el = $(id); if (el) el.value = ''; });

  // Hide output sections
  ['prompt-output-wrap','plan-paste-wrap','relay-review-panel','relay-done-panel',
   'relay-replan-wrap','import-plan-error','relay-decision-error','relay-replan-error']
    .forEach(id => { const el = $(id); if (el) el.style.display = 'none'; });

  // Clear log
  const log = $('relay-log'); if (log) log.textContent = '';

  goToStep(1);
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
  goToStep(1);

  // Pre-fill from saved settings
  fetch('/api/settings').then(r => r.json()).then(s => {
    if (s.goal      && $('relay-goal'))       $('relay-goal').value       = s.goal;
    if (s.repo_root && $('relay-repo-root'))  $('relay-repo-root').value  = s.repo_root;
    if (s.aider_model && $('relay-aider-model')) $('relay-aider-model').value = s.aider_model;
  }).catch(() => {});

  // Step 1
  $('btn-generate-prompt')?.addEventListener('click', generatePrompt);
  $('btn-copy-prompt')?.addEventListener('click', () => copyText('prompt-output', 'btn-copy-prompt'));
  $('btn-import-plan')?.addEventListener('click', importPlan);

  // Step 2
  $('btn-back-to-step1')?.addEventListener('click', () => goToStep(1));
  $('btn-confirm-tasks')?.addEventListener('click', launchRun);

  // Step 3
  $('btn-relay-stop')?.addEventListener('click', async () => {
    try { await fetch('/api/run/stop', { method: 'POST' }); } catch (_) {}
  });
  $('btn-copy-packet')?.addEventListener('click', () => copyText('relay-review-packet', 'btn-copy-packet'));
  $('btn-submit-relay-decision')?.addEventListener('click', submitDecision);
  $('btn-copy-replan')?.addEventListener('click', () => copyText('relay-replan-packet', 'btn-copy-replan'));
  $('btn-submit-replan')?.addEventListener('click', submitReplan);
  $('btn-relay-new-run')?.addEventListener('click', resetWizard);

  // Reset button (header)
  $('btn-relay-reset')?.addEventListener('click', resetWizard);
}

init();
