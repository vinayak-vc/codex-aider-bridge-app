// pages/run.js — AI Relay Chat Controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';

const $ = id => document.getElementById(id);
const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// ── State ────────────────────────────────────────────────────────────────────

let _state = 'idle'; // idle | prompting | waiting_plan | tasks_loaded | running | reviewing | done
let _tasks = [];
let _planFile = '';
let _repoRoot = '';
let _relaySessionId = '';
let _sse = null;
let _pollTimer = null;
let _currentReviewTaskId = null;

// ── Message Rendering ────────────────────────────────────────────────────────

function addUserMsg(text) {
  const el = _createMsg('user');
  el.querySelector('.relay-bubble').textContent = text;
  _append(el);
}

function addBridgeMsg(text) {
  const el = _createMsg('bridge');
  el.querySelector('.relay-bubble').textContent = text;
  _append(el);
}

function addPromptCard(title, prompt) {
  const card = document.createElement('div');
  card.className = 'relay-card relay-card--prompt';
  card.innerHTML = `
    <div class="relay-card-title">${_esc(title)}</div>
    <div class="relay-card-body">Copy this and paste into your AI:</div>
    <pre>${_esc(prompt)}</pre>
    <div class="relay-card-actions">
      <button class="btn btn--primary btn--sm relay-copy-btn">Copy to Clipboard</button>
    </div>
  `;
  card.querySelector('.relay-copy-btn').addEventListener('click', async () => {
    await navigator.clipboard.writeText(prompt);
    toast('Copied to clipboard', 'success');
  });
  _append(card);
}

function addPasteCard(title, placeholder, onSubmit) {
  const card = document.createElement('div');
  card.className = 'relay-card relay-card--prompt';
  card.innerHTML = `
    <div class="relay-card-title">${_esc(title)}</div>
    <textarea class="relay-paste-area" placeholder="${_esc(placeholder)}"></textarea>
    <div class="relay-card-actions">
      <button class="btn btn--primary btn--sm relay-submit-btn">Submit</button>
    </div>
  `;
  const textarea = card.querySelector('.relay-paste-area');
  const btn = card.querySelector('.relay-submit-btn');
  btn.addEventListener('click', () => {
    const text = textarea.value.trim();
    if (!text) { toast('Paste the AI response first', 'warning'); return; }
    btn.disabled = true;
    btn.textContent = 'Processing...';
    onSubmit(text);
  });
  _append(card);
  textarea.focus();
}

function addTaskListCard(tasks) {
  const card = document.createElement('div');
  card.className = 'relay-card relay-card--tasks';
  let html = `<div class="relay-card-title">Loaded ${tasks.length} task(s)</div>`;
  for (const t of tasks) {
    html += `<div class="relay-task-item" data-id="${t.id}">
      <span class="relay-task-num">${t.id}</span>
      <span class="relay-task-type">${_esc(t.type || '?')}</span>
      <span class="relay-task-files">${_esc((t.files || []).join(', '))}</span>
    </div>`;
  }
  html += `<div class="relay-card-actions">
    <button class="btn btn--primary btn--sm relay-run-btn">Run All</button>
    <button class="btn btn--secondary btn--sm relay-load-btn">Load Different Plan</button>
  </div>`;
  card.innerHTML = html;
  card.querySelector('.relay-run-btn').addEventListener('click', () => launchRun());
  card.querySelector('.relay-load-btn')?.addEventListener('click', () => {
    _state = 'idle';
    addBridgeMsg('Enter a new goal or paste a plan JSON.');
  });
  _append(card);
}

function addStatusCard(text, type = 'status') {
  const card = document.createElement('div');
  card.className = `relay-card relay-card--${type}`;
  card.innerHTML = `<div class="relay-card-body">${_esc(text)}</div>`;
  _append(card);
}

function addReviewCard(taskId, packet) {
  const card = document.createElement('div');
  card.className = 'relay-card relay-card--review';
  card.innerHTML = `
    <div class="relay-card-title">Review Required — Task ${taskId}</div>
    <div class="relay-card-body">Copy this diff and paste into your AI for review:</div>
    <pre>${_esc(packet)}</pre>
    <div class="relay-card-actions">
      <button class="btn btn--primary btn--sm relay-copy-btn">Copy Review to Clipboard</button>
    </div>
  `;
  card.querySelector('.relay-copy-btn').addEventListener('click', async () => {
    await navigator.clipboard.writeText(packet);
    toast('Review packet copied', 'success');
  });
  _append(card);

  // Add paste card for decision
  addPasteCard('Paste AI Decision', 'Paste PASS or REWORK: <instruction> here...', async (text) => {
    try {
      await apiPost('/api/relay/submit-decision', {
        raw_text: text,
        task_id: taskId,
        repo_root: _repoRoot,
        relay_session_id: _relaySessionId,
      });
      addBridgeMsg(`Decision submitted for Task ${taskId}`);
      _state = 'running';
      _currentReviewTaskId = null;
    } catch (err) {
      toast(err.message || 'Failed to submit decision', 'error');
    }
  });
}

function _createMsg(role) {
  const wrapper = document.createElement('div');
  wrapper.className = `relay-msg relay-msg--${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'relay-bubble';
  const meta = document.createElement('span');
  meta.className = 'relay-meta';
  meta.textContent = role === 'user' ? 'You' : 'Bridge';
  wrapper.appendChild(bubble);
  wrapper.appendChild(meta);
  return wrapper;
}

function _append(el) {
  const container = $('relay-messages');
  if (!container) return;
  // Hide welcome message
  const welcome = $('relay-welcome');
  if (welcome) welcome.style.display = 'none';
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

// ── Core Flow ────────────────────────────────────────────────────────────────

async function handleInput(text) {
  if (!text.trim()) return;

  // Try to detect if it's a JSON plan paste
  const trimmed = text.trim();
  if ((trimmed.startsWith('{') || trimmed.startsWith('```')) && trimmed.includes('"tasks"')) {
    addUserMsg(trimmed.length > 200 ? trimmed.slice(0, 200) + '...' : trimmed);
    await importPlan(trimmed);
    return;
  }

  // Try to detect if it's a PASS/REWORK decision
  const upper = trimmed.toUpperCase();
  if (_state === 'reviewing' && (upper.startsWith('PASS') || upper.startsWith('REWORK'))) {
    addUserMsg(trimmed);
    try {
      await apiPost('/api/relay/submit-decision', {
        raw_text: trimmed,
        task_id: _currentReviewTaskId,
        repo_root: _repoRoot,
        relay_session_id: _relaySessionId,
      });
      addBridgeMsg(`Decision submitted for Task ${_currentReviewTaskId}`);
      _state = 'running';
      _currentReviewTaskId = null;
    } catch (err) {
      toast(err.message || 'Failed to submit decision', 'error');
    }
    return;
  }

  // Otherwise treat as a goal
  addUserMsg(trimmed);
  await generatePrompt(trimmed);
}

async function generatePrompt(goal) {
  _state = 'prompting';
  setStatus('Generating prompt...');

  try {
    const settings = loadSettings();
    _repoRoot = settings.repo_root;

    if (!_repoRoot) {
      addStatusCard('Set a project folder in Settings first.', 'error');
      _state = 'idle';
      setStatus('Idle');
      return;
    }

    const res = await apiPost('/api/relay/generate-prompt', { goal, repo_root: _repoRoot });
    if (res.error) {
      addStatusCard(res.error, 'error');
      _state = 'idle';
      setStatus('Idle');
      return;
    }

    addPromptCard('Planning Prompt', res.prompt);
    addPasteCard('Paste AI Response', 'Paste the JSON task plan from your AI here...', async (text) => {
      addUserMsg(text.length > 200 ? text.slice(0, 200) + '...' : text);
      await importPlan(text);
    });

    _state = 'waiting_plan';
    setStatus('Waiting for plan...');
  } catch (err) {
    addStatusCard(err.message || 'Failed to generate prompt', 'error');
    _state = 'idle';
    setStatus('Idle');
  }
}

async function importPlan(rawText) {
  try {
    const res = await apiPost('/api/relay/import-plan', { raw_text: rawText });
    if (res.error) {
      addStatusCard(res.error, 'error');
      return;
    }
    _tasks = res.tasks || [];
    _relaySessionId = res.relay_session_id || '';
    addTaskListCard(_tasks);
    _state = 'tasks_loaded';
    setStatus(`${_tasks.length} tasks loaded`);
  } catch (err) {
    addStatusCard(err.message || 'Failed to import plan', 'error');
  }
}

async function launchRun() {
  const settings = loadSettings();
  settings.plan_file = _planFile || '';
  settings.manual_supervisor = true;

  if (!settings.repo_root) {
    toast('Set project folder in Settings', 'warning');
    return;
  }

  _state = 'running';
  setStatus('Running...');
  $('btn-stop-run').style.display = '';
  addStatusCard('Bridge started — Aider is executing tasks...', 'status');

  // Connect SSE for live log
  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log', d => {
      // Show important log lines as status cards
      const line = d.line || '';
      if (/Task\s+\d+.*attempt/i.test(line)) {
        addStatusCard(line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), 'status');
      }
    })
    .on('complete', d => {
      _state = 'done';
      const ok = d.status === 'success';
      addStatusCard(ok ? 'All tasks completed successfully!' : 'Run finished with failures.', ok ? 'done' : 'error');
      setStatus(ok ? 'Complete' : 'Failed');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
      addBridgeMsg('Enter a new goal to start another run.');
    })
    .on('error', d => {
      _state = 'done';
      addStatusCard(d.message || 'Run failed', 'error');
      setStatus('Error');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
    })
    .on('stopped', () => {
      _state = 'idle';
      addStatusCard('Run stopped by user.', 'error');
      setStatus('Stopped');
      $('btn-stop-run').style.display = 'none';
      stopPolling();
    })
    .connect();

  // Start polling for review requests
  startPolling();

  try {
    await apiPost('/api/settings', settings);
    await apiPost('/api/run', settings);
  } catch (err) {
    addStatusCard(err.message || 'Failed to start run', 'error');
    _state = 'idle';
    setStatus('Error');
  }
}

// ── Polling for Review Requests ──────────────────────────────────────────────

function startPolling() {
  if (_pollTimer) return;
  _pollTimer = setInterval(pollReview, 2000);
}

function stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

async function pollReview() {
  if (_state !== 'running') return;

  try {
    const state = await fetch('/api/relay/state').then(r => r.json());

    // Check for review request
    if (state.current_review && !_currentReviewTaskId) {
      const taskId = state.current_review.task_id;
      _currentReviewTaskId = taskId;
      _state = 'reviewing';
      setStatus(`Review required — Task ${taskId}`);

      // Get the review packet
      const packetRes = await fetch(
        `/api/relay/review-packet?task_id=${taskId}&repo_root=${encodeURIComponent(_repoRoot)}&relay_session_id=${_relaySessionId}`
      ).then(r => r.json());

      addReviewCard(taskId, packetRes.packet || JSON.stringify(state.current_review, null, 2));
    }

    // Check if run completed
    if (!state.is_running && !state.live_run_active && _state === 'running') {
      _state = 'done';
      setStatus('Complete');
      stopPolling();
    }
  } catch (_) {}
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function loadSettings() {
  return {
    repo_root: $('f-repo-root')?.value?.trim() || '',
    aider_model: $('f-aider-model')?.value?.trim() || 'ollama/qwen2.5-coder:7b',
    supervisor: 'manual',
    manual_supervisor: true,
    task_timeout: parseInt($('f-task-timeout')?.value || '600', 10),
    max_task_retries: parseInt($('f-max-retries')?.value || '10', 10),
  };
}

function setStatus(text) {
  const el = $('relay-status');
  if (el) el.textContent = text;
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  // Load settings
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    if ($('f-repo-root')) $('f-repo-root').value = settings.repo_root || '';
    if ($('f-aider-model')) $('f-aider-model').value = settings.aider_model || 'ollama/qwen2.5-coder:7b';
    if ($('f-task-timeout')) $('f-task-timeout').value = settings.task_timeout || 600;
    if ($('f-max-retries')) $('f-max-retries').value = settings.max_task_retries || 10;
    _repoRoot = settings.repo_root || '';
  } catch (_) {}

  // Check if a run is already active
  try {
    const status = await fetch('/api/run/status').then(r => r.json());
    if (status.is_running) {
      _state = 'running';
      setStatus('Running...');
      $('btn-stop-run').style.display = '';
      addStatusCard('A run is already in progress. Reconnecting...', 'status');
      if (!_sse) {
        _sse = new SSEClient('/api/run/stream');
        _sse
          .on('log', d => {
            const line = d.line || '';
            if (/Task\s+\d+.*attempt/i.test(line)) {
              addStatusCard(line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), 'status');
            }
          })
          .on('complete', d => {
            _state = 'done';
            addStatusCard(d.status === 'success' ? 'All tasks completed!' : 'Run finished with failures.', d.status === 'success' ? 'done' : 'error');
            setStatus(d.status === 'success' ? 'Complete' : 'Failed');
            $('btn-stop-run').style.display = 'none';
            stopPolling();
          })
          .connect();
      }
      startPolling();
      return;
    }
  } catch (_) {}

  // Wire send button
  $('btn-send')?.addEventListener('click', () => {
    const input = $('relay-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    handleInput(text);
    input.value = '';
    autoResize(input);
  });

  // Wire enter key (Ctrl+Enter to send)
  $('relay-input')?.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      $('btn-send')?.click();
    }
  });

  // Auto-resize textarea
  $('relay-input')?.addEventListener('input', e => autoResize(e.target));

  // Settings
  $('btn-settings')?.addEventListener('click', () => {
    $('settings-overlay').style.display = '';
  });
  $('wiz-btn-close-settings')?.addEventListener('click', () => {
    $('settings-overlay').style.display = 'none';
    // Save settings
    const settings = loadSettings();
    _repoRoot = settings.repo_root;
    apiPost('/api/settings', settings).catch(() => {});
  });
  $('settings-overlay')?.addEventListener('click', e => {
    if (e.target.id === 'settings-overlay') {
      $('settings-overlay').style.display = 'none';
      const settings = loadSettings();
      _repoRoot = settings.repo_root;
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
      }
    } catch (_) {}
  });

  // Stop button
  $('btn-stop-run')?.addEventListener('click', async () => {
    try { await fetch('/api/run/stop', { method: 'POST' }); } catch (_) {}
  });
}

init();
