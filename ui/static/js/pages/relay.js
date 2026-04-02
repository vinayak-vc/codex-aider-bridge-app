// pages/relay.js - AI Relay wizard controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost } from '/static/js/core/api.js';
import { toast } from '/static/js/core/toast.js';

const $ = id => document.getElementById(id);

let _step = 1;
let _goal = '';
let _repoRoot = '';
let _aiderModel = '';
let _tasks = [];
let _currentTaskId = null;
let _sse = null;
let _completedTasks = 0;
let _totalTasks = 0;
let _liveRunActive = false;

const RELAY_STORE_KEY = 'relay_wizard_state';

function localRelaySnapshot() {
  return {
    step: _step,
    goal: $('relay-goal')?.value || _goal,
    repo_root: $('relay-repo-root')?.value || _repoRoot,
    aider_model: $('relay-aider-model')?.value || _aiderModel,
    prompt_output: $('prompt-output')?.textContent || '',
    plan_paste: $('plan-paste')?.value || '',
  };
}

function saveRelayState() {
  const state = localRelaySnapshot();
  try {
    localStorage.setItem(RELAY_STORE_KEY, JSON.stringify({
      step: state.step,
      goal: state.goal,
      repoRoot: state.repo_root,
      aiderModel: state.aider_model,
      promptOutput: state.prompt_output,
      planPaste: state.plan_paste,
      tasks: _tasks,
    }));
  } catch (_) {}

  fetch('/api/relay/state', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state),
  }).catch(() => {});
}

function loadRelayState() {
  try {
    const raw = localStorage.getItem(RELAY_STORE_KEY);
    if (raw) {
      return JSON.parse(raw);
    }
  } catch (_) {}
  return null;
}

function clearRelayState() {
  try {
    localStorage.removeItem(RELAY_STORE_KEY);
  } catch (_) {}
}

function goToStep(n) {
  _step = n;
  for (let i = 1; i <= 3; i++) {
    const panel = $(`relay-panel-${i}`);
    if (panel) {
      panel.style.display = i === n ? '' : 'none';
    }
  }
  for (let i = 1; i <= 3; i++) {
    const ind = $(`step-indicator-${i}`);
    if (!ind) {
      continue;
    }
    ind.dataset.active = String(i === n);
    ind.dataset.done = String(i < n);
  }
}

function updateRunControls() {
  const stopBtn = $('btn-relay-stop');
  const submitBtn = $('btn-submit-relay-decision');
  const confirmBtn = $('btn-confirm-tasks');

  if (stopBtn) {
    stopBtn.disabled = !_liveRunActive;
  }
  if (submitBtn) {
    submitBtn.disabled = !_liveRunActive;
  }
  if (confirmBtn) {
    confirmBtn.disabled = _liveRunActive;
  }
}

async function generatePrompt() {
  _goal = ($('relay-goal')?.value || '').trim();
  _repoRoot = ($('relay-repo-root')?.value || '').trim();
  _aiderModel = ($('relay-aider-model')?.value || 'ollama/mistral').trim();

  if (!_goal) {
    toast('Please enter a goal.', 'warning');
    $('relay-goal')?.focus();
    return;
  }

  const btn = $('btn-generate-prompt');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Generating...';
  }

  try {
    const data = await apiPost('/api/relay/generate-prompt', {
      goal: _goal,
      repo_root: _repoRoot,
    });
    const box = $('prompt-output');
    if (box) {
      box.textContent = data.prompt;
    }
    const wrap = $('prompt-output-wrap');
    if (wrap) {
      wrap.style.display = '';
    }
    const pasteWrap = $('plan-paste-wrap');
    if (pasteWrap) {
      pasteWrap.style.display = '';
    }
    saveRelayState();
  } catch (err) {
    toast(err.message || 'Failed to generate prompt.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Generate Prompt';
    }
  }
}

async function importPlan() {
  const raw = ($('plan-paste')?.value || '').trim();
  const errEl = $('import-plan-error');
  if (errEl) {
    errEl.style.display = 'none';
  }

  if (!raw) {
    toast('Please paste the AI response first.', 'warning');
    return;
  }

  const btn = $('btn-import-plan');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Importing...';
  }

  try {
    const data = await apiPost('/api/relay/import-plan', { raw_text: raw });
    _tasks = data.tasks || [];
    renderTaskList(_tasks);
    goToStep(2);
    saveRelayState();
  } catch (err) {
    const msg = err.message || 'Failed to parse plan.';
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = '';
    }
    toast(msg, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Import Plan';
    }
  }
}

function renderTaskList(tasks) {
  const list = $('relay-task-list');
  if (!list) {
    return;
  }
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<p class="text-subtle" style="font-size:var(--font-size-sm)">No tasks found.</p>';
    return;
  }

  list.innerHTML = tasks.map(t => {
    const type = String(t.type || 'modify').toLowerCase();
    const status = String(t.status || 'not_started').toLowerCase();
    const statusLabel = String(t.status_label || 'Not started');
    return `
    <div class="relay-task-item">
      <div class="relay-task-num">${t.id}</div>
      <div class="relay-task-body">
        <div class="relay-task-title">
          <span>${escHtml(t.title || '')}</span>
          <span class="relay-task-type-badge" data-type="${escHtml(type)}">${escHtml(type)}</span>
          <span class="relay-task-status-badge" data-status="${escHtml(status)}">${escHtml(statusLabel)}</span>
        </div>
        <div class="relay-task-instruction">${escHtml(t.instruction || '')}</div>
        ${t.files && t.files.length ? `<div class="relay-task-files">${t.files.map(escHtml).join(', ')}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

async function launchRun() {
  const settings = {
    goal: _goal,
    repo_root: _repoRoot,
    aider_model: _aiderModel,
    supervisor: 'ai_relay',
    manual_supervisor: true,
    workflow_profile: 'standard',
  };

  goToStep(3);
  saveRelayState();
  _liveRunActive = true;
  updateRunControls();
  setRelayStatus('running', 'Running...');
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
  if (_sse) {
    return;
  }

  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log', d => appendLog(d.line || ''))
    .on('relay_review_needed', d => onReviewNeeded(d))
    .on('review_required', d => onReviewNeeded(d))
    .on('progress', d => updateProgress(d.completed, d.total))
    .on('plan_ready', d => {
      _totalTasks = d.task_count || 0;
      updateProgress(0, _totalTasks);
    })
    .on('complete', d => onRunComplete(d))
    .on('error', d => onRunComplete({ status: 'failure', message: d.message }))
    .on('stopped', () => onRunComplete({ status: 'stopped' }))
    .connect();
}

function disconnectSSE() {
  _sse?.disconnect();
  _sse = null;
}

function appendLog(line) {
  const box = $('relay-log');
  if (!box) {
    return;
  }
  box.textContent += line + '\n';
  box.scrollTop = box.scrollHeight;
}

function setRelayStatus(status, label) {
  const chip = $('relay-status-chip');
  const lbl = $('relay-status-label');
  if (chip) {
    chip.dataset.status = status;
  }
  if (lbl) {
    lbl.textContent = label;
  }
}

function updateProgress(done, total) {
  _completedTasks = done;
  _totalTasks = total;
  const pct = total > 0 ? Math.round(done / total * 100) : 0;
  const bar = $('relay-progress-bar');
  const lbl = $('relay-progress-label');
  if (bar) {
    bar.style.width = pct + '%';
  }
  if (lbl) {
    lbl.textContent = `${done} / ${total}`;
  }
}

async function onReviewNeeded(data) {
  _currentTaskId = data.task_id;
  setRelayStatus('waiting_review', 'Waiting for review...');
  goToStep(3);

  const panel = $('relay-review-panel');
  const tidEl = $('relay-review-task-id');
  const attBadge = $('relay-attempt-badge');
  if (tidEl) {
    tidEl.textContent = _currentTaskId;
  }
  if (attBadge) {
    attBadge.textContent = `attempt ${data.attempt || 1}`;
  }

  try {
    const params = new URLSearchParams({
      task_id: _currentTaskId,
      repo_root: _repoRoot,
      goal: _goal,
    });
    const resp = await fetch(`/api/relay/review-packet?${params}`);
    const payload = await resp.json();
    if (resp.ok) {
      const box = $('relay-review-packet');
      if (box) {
        box.textContent = payload.packet;
      }
    }
  } catch (_) {}

  const decPaste = $('relay-decision-paste');
  if (decPaste) {
    decPaste.value = '';
  }
  const decErr = $('relay-decision-error');
  if (decErr) {
    decErr.style.display = 'none';
  }
  const replanWrap = $('relay-replan-wrap');
  if (replanWrap) {
    replanWrap.style.display = 'none';
  }

  if (panel) {
    panel.style.display = '';
  }
}

async function submitDecision() {
  if (!_liveRunActive) {
    toast('No live bridge run is active. Re-launch the run from Confirm Tasks first.', 'warning');
    return;
  }

  const raw = ($('relay-decision-paste')?.value || '').trim();
  const errEl = $('relay-decision-error');
  if (errEl) {
    errEl.style.display = 'none';
  }

  if (!raw) {
    toast('Please paste the AI\'s decision.', 'warning');
    return;
  }

  const btn = $('btn-submit-relay-decision');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Submitting...';
  }

  try {
    const data = await apiPost('/api/relay/submit-decision', {
      raw_text: raw,
      task_id: _currentTaskId,
      repo_root: _repoRoot,
    });

    if (data.decision === 'fail') {
      await loadReplanPrompt();
    } else {
      const panel = $('relay-review-panel');
      if (panel) {
        panel.style.display = 'none';
      }
      setRelayStatus('running', 'Running...');
      toast(`Decision submitted: ${data.decision}`, 'success');
    }
  } catch (err) {
    const msg = err.message || 'Failed to submit decision.';
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = '';
    }
    toast(msg, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Submit Decision';
    }
  }
}

async function loadReplanPrompt() {
  try {
    const data = await apiPost('/api/relay/replan-prompt', {
      task_id: _currentTaskId,
      repo_root: _repoRoot,
      goal: _goal,
      failed_reason: ($('relay-decision-paste')?.value || '').replace(/^FAILED:\s*/i, '').trim(),
    });
    const box = $('relay-replan-packet');
    if (box) {
      box.textContent = data.prompt;
    }
    const wrap = $('relay-replan-wrap');
    if (wrap) {
      wrap.style.display = '';
    }
  } catch (err) {
    toast('Could not generate replan prompt: ' + err.message, 'error');
  }
}

async function submitReplan() {
  if (!_liveRunActive) {
    toast('No live bridge run is active. Re-launch the run from Confirm Tasks first.', 'warning');
    return;
  }

  const raw = ($('relay-replan-paste')?.value || '').trim();
  const errEl = $('relay-replan-error');
  if (errEl) {
    errEl.style.display = 'none';
  }

  if (!raw) {
    toast('Please paste the replacement tasks.', 'warning');
    return;
  }

  const btn = $('btn-submit-replan');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Importing...';
  }

  try {
    const data = await apiPost('/api/relay/import-replan', {
      raw_text: raw,
      task_id: _currentTaskId,
    });
    _tasks = data.tasks || [];
    renderTaskList(_tasks);
    saveRelayState();
    toast(`Replan imported: ${data.count} tasks remaining.`, 'success');
    const panel = $('relay-review-panel');
    if (panel) {
      panel.style.display = 'none';
    }
    setRelayStatus('running', 'Running...');
  } catch (err) {
    const msg = err.message || 'Failed to import replan.';
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = '';
    }
    toast(msg, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Import Replacement Tasks';
    }
  }
}

function onRunComplete(data) {
  const status = data.status || 'failure';
  const elapsed = data.elapsed ? ` in ${data.elapsed}s` : '';
  _liveRunActive = false;
  updateRunControls();
  setRelayStatus(status, relayStatusLabel(status));

  const panel = $('relay-review-panel');
  if (panel) {
    panel.style.display = 'none';
  }
  const done = $('relay-done-panel');
  if (done) {
    done.style.display = '';
  }

  const icon = done?.querySelector('.relay-done-icon');
  if (icon) {
    icon.dataset.failed = String(status === 'failure');
  }

  const title = $('relay-done-title');
  const sub = $('relay-done-sub');
  if (title) {
    title.textContent = status === 'success' ? 'Run complete!' : status === 'stopped' ? 'Run stopped' : 'Run failed';
  }
  if (sub) {
    sub.textContent = `${_completedTasks} / ${_totalTasks} tasks completed${elapsed}.`;
  }

  disconnectSSE();
}

function copyText(elementId, btnId) {
  const text = $(elementId)?.textContent || '';
  navigator.clipboard?.writeText(text).then(() => {
    const btn = $(btnId);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => {
        btn.textContent = orig;
      }, 1500);
    }
  }).catch(() => toast('Copy failed - please copy manually.', 'warning'));
}

function resetWizard() {
  _step = 1;
  _goal = '';
  _repoRoot = '';
  _aiderModel = '';
  _tasks = [];
  _currentTaskId = null;
  _completedTasks = 0;
  _totalTasks = 0;
  _liveRunActive = false;
  disconnectSSE();

  ['plan-paste', 'relay-decision-paste', 'relay-replan-paste'].forEach(id => {
    const el = $(id);
    if (el) {
      el.value = '';
    }
  });

  ['prompt-output-wrap', 'plan-paste-wrap', 'relay-review-panel', 'relay-done-panel',
   'relay-replan-wrap', 'import-plan-error', 'relay-decision-error', 'relay-replan-error']
    .forEach(id => {
      const el = $(id);
      if (el) {
        el.style.display = 'none';
      }
    });

  const log = $('relay-log');
  if (log) {
    log.textContent = '';
  }

  clearRelayState();
  fetch('/api/relay/state', { method: 'DELETE' }).catch(() => {});
  goToStep(1);
  updateRunControls();
  prefillFromSettings();
}

function prefillFromSettings() {
  fetch('/api/settings').then(r => r.json()).then(s => {
    if ($('relay-goal') && !$('relay-goal').value && s.goal) {
      $('relay-goal').value = s.goal;
    }
    if ($('relay-repo-root')) {
      $('relay-repo-root').value = s.repo_root || '';
    }
    if ($('relay-aider-model')) {
      $('relay-aider-model').value = s.aider_model || 'ollama/mistral';
    }
  }).catch(() => {});
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function relayStatusLabel(status) {
  const normalized = String(status || '').replace(/_/g, ' ').trim().toLowerCase();
  if (!normalized) {
    return 'Idle';
  }
  return normalized.split(' ').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function applyRelayState(data) {
  _step = data.step || 1;
  _goal = data.goal || '';
  _repoRoot = data.repo_root || '';
  _aiderModel = data.aider_model || '';
  _tasks = data.tasks || [];
  _completedTasks = data.completed_tasks || 0;
  _totalTasks = data.total_tasks || _tasks.length || 0;
  _liveRunActive = Boolean(data.live_run_active);

  if ($('relay-goal')) $('relay-goal').value = _goal;
  if ($('relay-repo-root')) $('relay-repo-root').value = _repoRoot;
  if ($('relay-aider-model')) $('relay-aider-model').value = _aiderModel;
  if ($('prompt-output')) $('prompt-output').textContent = data.prompt_output || '';
  if ($('plan-paste')) $('plan-paste').value = data.plan_paste || '';

  const promptWrap = $('prompt-output-wrap');
  if (promptWrap) {
    promptWrap.style.display = data.prompt_output ? '' : 'none';
  }
  const pasteWrap = $('plan-paste-wrap');
  if (pasteWrap) {
    pasteWrap.style.display = (data.prompt_output || data.plan_paste) ? '' : 'none';
  }

  if (_tasks.length > 0) {
    renderTaskList(_tasks);
  }

  const runStatus = data.run_status || 'idle';
  const shouldOpenRun = _tasks.length > 0 && _liveRunActive;

  if (shouldOpenRun) {
    goToStep(3);
    setRelayStatus(runStatus, relayStatusLabel(runStatus));
    updateProgress(_completedTasks, _totalTasks);
    if (_liveRunActive) {
      connectSSE();
    }
  } else if (_tasks.length > 0) {
    goToStep(2);
    setRelayStatus('idle', 'Ready');
  } else {
    goToStep(1);
    setRelayStatus('idle', 'Idle');
  }

  updateRunControls();

  if (_liveRunActive && data.current_review && data.current_review.task_id) {
    void onReviewNeeded({
      task_id: data.current_review.task_id,
      attempt: data.current_review.attempt || 1,
    });
  }
}

async function restoreRelayState() {
  try {
    const resp = await fetch('/api/relay/state');
    const data = await resp.json();
    if (resp.ok && (data.tasks?.length || data.goal || data.prompt_output || data.plan_paste)) {
      applyRelayState(data);
      return true;
    }
  } catch (_) {}

  const saved = loadRelayState();
  if (!saved) {
    return false;
  }

  applyRelayState({
    step: saved.step || 1,
    goal: saved.goal || '',
    repo_root: saved.repoRoot || '',
    aider_model: saved.aiderModel || '',
    prompt_output: saved.promptOutput || '',
    plan_paste: saved.planPaste || '',
    tasks: saved.tasks || [],
    completed_tasks: 0,
    total_tasks: (saved.tasks || []).length,
    run_status: 'idle',
    is_running: false,
    live_run_active: false,
    current_review: null,
  });
  return true;
}

async function init() {
  const restored = await restoreRelayState();
  if (!restored) {
    goToStep(1);
    prefillFromSettings();
  }

  $('btn-generate-prompt')?.addEventListener('click', generatePrompt);
  $('btn-copy-prompt')?.addEventListener('click', () => copyText('prompt-output', 'btn-copy-prompt'));
  $('btn-import-plan')?.addEventListener('click', importPlan);

  $('btn-back-to-step1')?.addEventListener('click', () => goToStep(1));
  $('btn-confirm-tasks')?.addEventListener('click', launchRun);

  $('btn-relay-stop')?.addEventListener('click', async () => {
    if (!_liveRunActive) {
      toast('No live bridge run is active.', 'warning');
      return;
    }
    try {
      await fetch('/api/run/stop', { method: 'POST' });
    } catch (_) {}
  });
  $('btn-copy-packet')?.addEventListener('click', () => copyText('relay-review-packet', 'btn-copy-packet'));
  $('btn-submit-relay-decision')?.addEventListener('click', submitDecision);
  $('btn-copy-replan')?.addEventListener('click', () => copyText('relay-replan-packet', 'btn-copy-replan'));
  $('btn-submit-replan')?.addEventListener('click', submitReplan);
  $('btn-relay-new-run')?.addEventListener('click', resetWizard);
  $('btn-relay-reset')?.addEventListener('click', resetWizard);

  $('relay-explainer-toggle')?.addEventListener('click', () => {
    const body = $('relay-explainer-body');
    const btn = $('relay-explainer-toggle');
    if (!body || !btn) {
      return;
    }
    const hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    btn.textContent = hidden ? 'Hide guide' : 'Show guide';
    localStorage.setItem('relay-guide-hidden', hidden ? 'false' : 'true');
  });

  if (localStorage.getItem('relay-guide-hidden') === 'true') {
    const body = $('relay-explainer-body');
    const btn = $('relay-explainer-toggle');
    if (body) {
      body.style.display = 'none';
    }
    if (btn) {
      btn.textContent = 'Show guide';
    }
  }

  updateRunControls();
}

init();
