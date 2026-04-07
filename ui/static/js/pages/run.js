// pages/run.js — AI Relay "Mission Control" Controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';

const $ = id => document.getElementById(id);
const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// ── State ────────────────────────────────────────────────────────────────────

let _state = 'idle';  // idle | prompting | waiting_plan | tasks_loaded | running | reviewing | done
let _tasks = [];
let _planFile = '';
let _repoRoot = '';
let _relaySessionId = '';
let _sse = null;
let _pollTimer = null;
let _currentReviewTaskId = null;
let _runStartTime = null;
let _elapsedTimer = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

function loadSettings() {
  return {
    repo_root: ($('f-repo-root') || {}).value || _repoRoot,
    aider_model: ($('f-aider-model') || {}).value || 'ollama/qwen2.5-coder:7b',
    task_timeout: parseInt(($('f-task-timeout') || {}).value) || 600,
    max_task_retries: parseInt(($('f-max-retries') || {}).value) || 10,
  };
}

function autoResize(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

// ── Status Badge ─────────────────────────────────────────────────────────────

function setStatus(text, type = '') {
  const el = $('mc-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'mc-status' + (type ? ` --${type}` : '');
}

// ── Pipeline Rendering ───────────────────────────────────────────────────────

function renderPipeline(tasks) {
  const list = $('mc-task-list');
  if (!list) return;
  if (!tasks || tasks.length === 0) {
    list.innerHTML = '<div class="mc-empty-pipeline">No tasks loaded</div>';
    $('mc-progress-label').textContent = '—';
    $('mc-progress-fill').style.width = '0%';
    return;
  }

  let html = '';
  for (const t of tasks) {
    const state = _getTaskState(t);
    const icon  = _getTaskIcon(t, state);
    const files = (t.files || []).join(', ');
    html += `
      <div class="mc-task --${state}" data-id="${t.id}">
        <div class="mc-task-icon">${icon}</div>
        <div class="mc-task-body">
          <div class="mc-task-label">${_esc(t.type || '?').toUpperCase()} ${_esc((t.title || files).substring(0, 30))}</div>
          <div class="mc-task-file">${_esc(files)}</div>
        </div>
      </div>`;
  }
  list.innerHTML = html;
  _updateProgress(tasks);
}

function _getTaskState(task) {
  const s = (task.status || '').toLowerCase();
  if (s === 'approved' || s === 'done' || s === 'completed') return 'done';
  if (s === 'failed') return 'failed';
  if (s === 'skipped') return 'skipped';
  if (s === 'reviewing' || s === 'review') return 'review';
  if (s === 'running' || s === 'active') return 'active';
  return 'locked';
}

function _getTaskIcon(task, state) {
  if (state === 'done')    return '✓';
  if (state === 'failed')  return '✕';
  if (state === 'skipped') return '—';
  if (state === 'active')  return '▶';
  if (state === 'review')  return '⚠';
  return task.id;
}

function updateTaskState(taskId, state, label) {
  // Update internal array
  const task = _tasks.find(t => String(t.id) === String(taskId));
  if (task) {
    task.status = state;
    task.status_label = label || state;
  }
  // Update DOM
  const el = $('mc-task-list');
  if (!el) return;
  const row = el.querySelector(`[data-id="${taskId}"]`);
  if (row) {
    row.className = `mc-task --${state}`;
    const iconEl = row.querySelector('.mc-task-icon');
    if (iconEl) iconEl.textContent = _getTaskIcon({ id: taskId }, state);
  }
  _updateProgress(_tasks);
}

function _updateProgress(tasks) {
  const done = tasks.filter(t => {
    const s = (t.status || '').toLowerCase();
    return s === 'approved' || s === 'done' || s === 'completed' || s === 'skipped';
  }).length;
  const total = tasks.length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const label = $('mc-progress-label');
  const fill  = $('mc-progress-fill');
  if (label) label.textContent = `${done} / ${total}`;
  if (fill) fill.style.width = `${pct}%`;
}

// ── Session Metadata ─────────────────────────────────────────────────────────

function updateSessionMeta() {
  const proj = $('mc-meta-project');
  const model = $('mc-meta-model');
  const sess = $('mc-meta-session');
  if (proj) {
    const name = _repoRoot.split(/[\\/]/).pop() || '—';
    proj.textContent = name;
    proj.title = _repoRoot;
  }
  if (model) model.textContent = ($('f-aider-model') || {}).value || '—';
  if (sess) sess.textContent = _relaySessionId || '—';
}

function startElapsedTimer() {
  _runStartTime = Date.now();
  if (_elapsedTimer) clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - _runStartTime) / 1000);
    const h = String(Math.floor(secs / 3600)).padStart(2, '0');
    const m = String(Math.floor((secs % 3600) / 60)).padStart(2, '0');
    const s = String(secs % 60).padStart(2, '0');
    const el = $('mc-meta-elapsed');
    if (el) el.textContent = `${h}:${m}:${s}`;
  }, 1000);
}

function stopElapsedTimer() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
}

// ── Console ──────────────────────────────────────────────────────────────────

function showConsole() {
  const el = $('mc-console');
  if (el) el.style.display = '';
}

function hideConsole() {
  const el = $('mc-console');
  if (el) el.style.display = 'none';
}

function appendLog(line) {
  const el = $('mc-console-output');
  if (!el) return;
  el.textContent += line + '\n';
  el.scrollTop = el.scrollHeight;
}

function clearConsole() {
  const el = $('mc-console-output');
  if (el) el.textContent = '';
}

// ── Action Zone Rendering ────────────────────────────────────────────────────

function clearAction() {
  const el = $('mc-action');
  if (el) el.innerHTML = '';
}

function addActionCard(type, title, bodyHtml) {
  const el = $('mc-action');
  if (!el) return null;
  const card = document.createElement('div');
  card.className = `mc-card --${type}`;
  card.innerHTML = `
    <div class="mc-card-title">${_esc(title)}</div>
    <div class="mc-card-body">${bodyHtml}</div>
  `;
  el.appendChild(card);
  el.scrollTop = el.scrollHeight;
  return card;
}

function addStatusMsg(text, type = 'status') {
  addActionCard(type, type === 'error' ? 'Error' : type === 'done' ? 'Complete' : 'Status', _esc(text));
}

function addPromptCard(prompt) {
  const card = addActionCard('prompt', 'Planning Prompt', `
    <div>Copy this and paste into your AI:</div>
    <pre>${_esc(prompt)}</pre>
    <div class="mc-card-actions">
      <button class="btn btn--primary btn--sm mc-copy-btn">Copy to Clipboard</button>
    </div>
  `);
  if (card) {
    card.querySelector('.mc-copy-btn').addEventListener('click', async () => {
      await navigator.clipboard.writeText(prompt);
      toast('Copied to clipboard', 'success');
    });
  }
}

function addPasteCard(title, placeholder, onSubmit) {
  const card = addActionCard('prompt', title, `
    <textarea class="mc-paste-area" placeholder="${_esc(placeholder)}"></textarea>
    <div class="mc-card-actions">
      <button class="btn btn--primary btn--sm mc-paste-submit">Submit</button>
    </div>
  `);
  if (!card) return;
  const textarea = card.querySelector('.mc-paste-area');
  card.querySelector('.mc-paste-submit').addEventListener('click', () => {
    const text = textarea.value.trim();
    if (!text) { toast('Paste content first', 'warning'); return; }
    onSubmit(text);
  });
  textarea.focus();
}

function addReviewCard(taskId, packet) {
  const action = $('mc-action');
  if (action) action.classList.add('--reviewing');

  const card = addActionCard('review', `Review Required — Task ${taskId}`, `
    <div>Copy this diff and paste into your AI for review:</div>
    <pre>${_esc(packet)}</pre>
    <div class="mc-card-actions">
      <button class="btn btn--primary btn--sm mc-copy-review">Copy Review to Clipboard</button>
    </div>
  `);
  if (card) {
    card.querySelector('.mc-copy-review').addEventListener('click', async () => {
      await navigator.clipboard.writeText(packet);
      toast('Review packet copied', 'success');
    });
  }

  // Show decision zone
  showDecision(taskId);
}

// ── Decision Zone ────────────────────────────────────────────────────────────

function showDecision(taskId) {
  const el = $('mc-decision');
  if (el) el.style.display = '';
  const input = $('mc-decision-input');
  if (input) { input.value = ''; input.focus(); }

  // Wire the submit button (re-wire each time to capture taskId)
  const btn = $('btn-submit-decision');
  if (btn) {
    const newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn.id = 'btn-submit-decision';
    newBtn.addEventListener('click', async () => {
      const text = ($('mc-decision-input') || {}).value?.trim();
      if (!text) { toast('Paste a decision first', 'warning'); return; }
      try {
        await apiPost('/api/relay/submit-decision', {
          raw_text: text,
          task_id: taskId,
          repo_root: _repoRoot,
          relay_session_id: _relaySessionId,
        });
        toast('Decision submitted', 'success');
        hideDecision();
        _currentReviewTaskId = null;
        updateTaskState(taskId, 'done', 'Approved');
        _state = 'running';
        setStatus('Running...', 'running');
        const action = $('mc-action');
        if (action) action.classList.remove('--reviewing');
      } catch (err) {
        toast(err.message || 'Failed to submit', 'error');
      }
    });
  }
}

function hideDecision() {
  const el = $('mc-decision');
  if (el) el.style.display = 'none';
}

// ── Goal Input Handling ──────────────────────────────────────────────────────

async function handleGoalInput(text) {
  const trimmed = text.trim();
  if (!trimmed) return;

  // Try to detect JSON plan paste
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    await importPlan(trimmed, _repoRoot);
    return;
  }

  await generatePrompt(trimmed);
}

async function generatePrompt(goal) {
  _state = 'prompting';
  setStatus('Generating...');
  setGoalBarEnabled(false);

  try {
    const settings = loadSettings();
    _repoRoot = settings.repo_root;

    if (!_repoRoot) {
      addStatusMsg('Set a project folder in Settings first.', 'error');
      _state = 'idle';
      setStatus('Idle');
      setGoalBarEnabled(true);
      return;
    }

    const res = await apiPost('/api/relay/generate-prompt', { goal, repo_root: _repoRoot });
    if (res.error) {
      addStatusMsg(res.error, 'error');
      _state = 'idle';
      setStatus('Idle');
      setGoalBarEnabled(true);
      return;
    }

    clearAction();
    addPromptCard(res.prompt);
    addPasteCard('Paste AI Response', 'Paste the JSON task plan from your AI here...', async (text) => {
      await importPlan(text, _repoRoot);
    });

    _state = 'waiting_plan';
    setStatus('Waiting for plan...');
  } catch (err) {
    addStatusMsg(err.message || 'Failed to generate prompt', 'error');
    _state = 'idle';
    setStatus('Idle');
    setGoalBarEnabled(true);
  }
}

async function importPlan(rawText, repoRoot) {
  try {
    const res = await apiPost('/api/relay/import-plan', { raw_text: rawText, repo_root: repoRoot });
    if (res.error) {
      addStatusMsg(res.error, 'error');
      return;
    }
    _tasks = res.tasks || [];
    _relaySessionId = res.relay_session_id || '';
    _planFile = res.plan_file || '';
    renderPipeline(_tasks);
    updateSessionMeta();

    clearAction();
    addStatusMsg(`${_tasks.length} tasks loaded. Click "Run All" to start.`, 'status');

    // Show controls
    const runBtn = $('btn-run-all');
    const loadBtn = $('btn-load-plan');
    if (runBtn) runBtn.style.display = '';
    if (loadBtn) loadBtn.style.display = '';

    _state = 'tasks_loaded';
    setStatus(`${_tasks.length} tasks loaded`);
    setGoalBarEnabled(false);
  } catch (err) {
    addStatusMsg(err.message || 'Failed to import plan', 'error');
  }
}

// ── Run Execution ────────────────────────────────────────────────────────────

async function launchRun() {
  const settings = loadSettings();
  settings.plan_file = _planFile || '';
  settings.manual_supervisor = true;
  settings.relay_session_id = _relaySessionId;

  if (!settings.repo_root) {
    toast('Set project folder in Settings', 'warning');
    return;
  }

  _state = 'running';
  setStatus('Running...', 'running');
  setGoalBarEnabled(false);
  $('btn-stop-run').style.display = '';
  $('btn-run-all').style.display = 'none';
  $('btn-load-plan').style.display = 'none';

  clearAction();
  addStatusMsg('Bridge started — Aider is executing tasks...', 'status');
  showConsole();
  startElapsedTimer();

  // Connect SSE
  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log', d => {
      const line = d.line || '';
      appendLog(line);
      // Update pipeline on task start
      const taskMatch = line.match(/Task\s+(\d+).*attempt/i);
      if (taskMatch) {
        const tid = parseInt(taskMatch[1]);
        updateTaskState(tid, 'active', 'Running');
        addStatusMsg(line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), 'status');
      }
    })
    .on('supervisor_review_requested', () => {
      pollReview();
    })
    .on('complete', d => {
      _state = 'done';
      const ok = d.status === 'success';
      setStatus(ok ? 'Complete' : 'Failed', ok ? 'done' : 'error');
      addStatusMsg(ok ? 'All tasks completed successfully!' : 'Run finished with failures.', ok ? 'done' : 'error');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
      stopElapsedTimer();
      setGoalBarEnabled(true);
    })
    .on('error', d => {
      _state = 'done';
      setStatus('Error', 'error');
      addStatusMsg(d.message || 'Run failed', 'error');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
      stopElapsedTimer();
      setGoalBarEnabled(true);
    })
    .on('stopped', () => {
      _state = 'idle';
      setStatus('Stopped', 'error');
      addStatusMsg('Run stopped by user.', 'error');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
      stopElapsedTimer();
      setGoalBarEnabled(true);
    })
    .connect();

  startPolling();

  try {
    await apiPost('/api/settings', settings);
    await apiPost('/api/run', settings);
  } catch (err) {
    addStatusMsg(err.message || 'Failed to start run', 'error');
    _state = 'done';
    setStatus('Error', 'error');
    stopPolling();
    stopElapsedTimer();
    setGoalBarEnabled(true);
  }
}

// ── Polling ──────────────────────────────────────────────────────────────────

function startPolling() {
  stopPolling();
  _pollTimer = setInterval(pollReview, 2000);
}

function stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

async function pollReview() {
  if (_state !== 'running' && _state !== 'reviewing') return;

  try {
    const state = await fetch('/api/relay/state?repo_root=' + encodeURIComponent(_repoRoot)).then(r => r.json());

    // Update tasks in pipeline if available
    if (state.tasks && state.tasks.length > 0) {
      _tasks = state.tasks;
      renderPipeline(_tasks);
    }

    // Check for review request
    if (state.current_review && !_currentReviewTaskId) {
      const taskId = state.current_review.task_id;
      _currentReviewTaskId = taskId;
      _state = 'reviewing';
      setStatus(`Review — Task ${taskId}`, 'reviewing');
      updateTaskState(taskId, 'review', 'Review');

      // Get the review packet
      const packetRes = await fetch(
        `/api/relay/review-packet?task_id=${taskId}&repo_root=${encodeURIComponent(_repoRoot)}&relay_session_id=${_relaySessionId}`
      ).then(r => r.json());

      addReviewCard(taskId, packetRes.packet || JSON.stringify(state.current_review, null, 2));
    }

    // Check if run completed
    if (!state.is_running && !state.live_run_active && _state === 'running') {
      _state = 'done';
      setStatus('Complete', 'done');
      stopPolling();
      stopElapsedTimer();
      setGoalBarEnabled(true);
    }
  } catch (_) {}
}

// ── UI Zone Control ──────────────────────────────────────────────────────────

function setGoalBarEnabled(enabled) {
  const bar = $('mc-goal-bar');
  if (bar) {
    if (enabled) bar.classList.remove('--disabled');
    else bar.classList.add('--disabled');
  }
}

// ── Initialization ───────────────────────────────────────────────────────────

async function init() {
  // Load settings
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    if ($('f-repo-root')) $('f-repo-root').value = settings.repo_root || '';
    if ($('f-aider-model')) $('f-aider-model').value = settings.aider_model || 'ollama/qwen2.5-coder:7b';
    if ($('f-task-timeout')) $('f-task-timeout').value = settings.task_timeout || 600;
    if ($('f-max-retries')) $('f-max-retries').value = settings.max_task_retries || 10;
    _repoRoot = settings.repo_root || '';
    updateSessionMeta();
  } catch (_) {}

  // Restore relay state from project
  try {
    const state = await fetch('/api/relay/state?repo_root=' + encodeURIComponent(_repoRoot)).then(r => r.json());

    _repoRoot = state.repo_root || _repoRoot;
    _relaySessionId = state.relay_session_id || '';
    updateSessionMeta();

    // Restore tasks
    if (state.tasks && state.tasks.length > 0) {
      _tasks = state.tasks;
      _planFile = state.plan_file || '';
      renderPipeline(_tasks);

      // Hide welcome
      const welcome = $('mc-welcome');
      if (welcome) welcome.style.display = 'none';

      // Show prompt if available
      if (state.prompt_output) {
        addPromptCard(state.prompt_output);
      }

      // Check if bridge is running
      const runStatus = await fetch('/api/run/status').then(r => r.json());
      if (runStatus.is_running) {
        _state = 'running';
        setStatus('Running...', 'running');
        setGoalBarEnabled(false);
        $('btn-stop-run').style.display = '';
        showConsole();
        startElapsedTimer();
        addStatusMsg('Reconnected to running bridge session.', 'status');

        // Reconnect SSE
        if (!_sse) {
          _sse = new SSEClient('/api/run/stream');
          _sse
            .on('log', d => {
              const line = d.line || '';
              appendLog(line);
              const taskMatch = line.match(/Task\s+(\d+).*attempt/i);
              if (taskMatch) {
                updateTaskState(parseInt(taskMatch[1]), 'active', 'Running');
              }
            })
            .on('supervisor_review_requested', () => pollReview())
            .on('complete', d => {
              _state = 'done';
              setStatus(d.status === 'success' ? 'Complete' : 'Failed', d.status === 'success' ? 'done' : 'error');
              $('btn-stop-run').style.display = 'none';
              stopPolling();
              stopElapsedTimer();
              setGoalBarEnabled(true);
            })
            .connect();
        }
        startPolling();
      } else {
        // Not running — show controls
        _state = 'tasks_loaded';
        setStatus(`${_tasks.length} tasks loaded`);
        setGoalBarEnabled(false);
        const runBtn = $('btn-run-all');
        const loadBtn = $('btn-load-plan');
        if (runBtn) runBtn.style.display = '';
        if (loadBtn) loadBtn.style.display = '';
        addStatusMsg(`${_tasks.length} tasks restored from project. Click "Run All" to continue.`, 'status');
      }

      // Check for active review
      if (state.current_review) {
        const taskId = state.current_review.task_id;
        _currentReviewTaskId = taskId;
        _state = 'reviewing';
        setStatus(`Review — Task ${taskId}`, 'reviewing');
        updateTaskState(taskId, 'review', 'Review');

        const res = await fetch(
          `/api/relay/review-packet?task_id=${taskId}&repo_root=${encodeURIComponent(_repoRoot)}&relay_session_id=${_relaySessionId}`
        ).then(r => r.json());
        addReviewCard(taskId, res.packet || JSON.stringify(state.current_review, null, 2));
        startPolling();
      }
    }
  } catch (err) {
    console.error('Failed to restore relay state:', err);
  }

  // ── Wire Events ──────────────────────────────────────────────────────────

  // Send button
  $('btn-send')?.addEventListener('click', () => {
    const input = $('mc-goal-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    // Hide welcome
    const welcome = $('mc-welcome');
    if (welcome) welcome.style.display = 'none';
    handleGoalInput(text);
    input.value = '';
    autoResize(input);
  });

  // Enter key (Ctrl/Cmd + Enter)
  $('mc-goal-input')?.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      $('btn-send')?.click();
    }
  });

  // Auto-resize
  $('mc-goal-input')?.addEventListener('input', e => autoResize(e.target));

  // Run All button
  $('btn-run-all')?.addEventListener('click', () => launchRun());

  // Load Different Plan
  $('btn-load-plan')?.addEventListener('click', () => {
    _state = 'idle';
    _tasks = [];
    renderPipeline([]);
    clearAction();
    hideConsole();
    hideDecision();
    setGoalBarEnabled(true);
    setStatus('Idle');
    const welcome = $('mc-welcome');
    if (welcome) welcome.style.display = '';
    $('btn-run-all').style.display = 'none';
    $('btn-load-plan').style.display = 'none';
  });

  // Clear console
  $('btn-clear-console')?.addEventListener('click', clearConsole);

  // Stop button
  $('btn-stop-run')?.addEventListener('click', async () => {
    try { await fetch('/api/run/stop', { method: 'POST' }); } catch (_) {}
  });

  // Settings
  $('btn-settings')?.addEventListener('click', () => {
    $('settings-overlay').style.display = '';
  });
  $('wiz-btn-close-settings')?.addEventListener('click', () => {
    $('settings-overlay').style.display = 'none';
    const settings = loadSettings();
    _repoRoot = settings.repo_root;
    updateSessionMeta();
    apiPost('/api/settings', settings).catch(() => {});
  });
  $('settings-overlay')?.addEventListener('click', e => {
    if (e.target.id === 'settings-overlay') {
      $('settings-overlay').style.display = 'none';
      const settings = loadSettings();
      _repoRoot = settings.repo_root;
      updateSessionMeta();
      apiPost('/api/settings', settings).catch(() => {});
    }
  });

  // Browse folder
  $('btn-browse-folder')?.addEventListener('click', async () => {
    try {
      const d = await fetch('/api/browse/folder').then(r => r.json());
      if (d.path && $('f-repo-root')) {
        $('f-repo-root').value = d.path;
        _repoRoot = d.path;
        updateSessionMeta();
      }
    } catch (_) {}
  });
}

init();
