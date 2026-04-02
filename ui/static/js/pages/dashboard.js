// pages/dashboard.js — Dashboard page controller

import { SSEClient } from '/static/js/core/sse.js';
import { store }     from '/static/js/core/store.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';

// ── Constants ─────────────────────────────────────────────────────────────────

const RING_CIRCUMFERENCE = 364.42; // 2 * π * 58

// Status → human label
const STATUS_LABEL = {
  idle:     'Idle',
  running:  'Running',
  paused:   'Paused',
  success:  'Success',
  failure:  'Failed',
  stopped:  'Stopped',
};

// Task status → badge variant
const TASK_BADGE = {
  running:  'accent',
  approved: 'success',
  rework:   'warning',
  retrying: 'warning',
  failure:  'danger',
  'dry-run':'info',
  retry:    'info',
  pending:  'muted',
};

// ── DOM refs ──────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const els = {
  ringFill:       () => $('ring-fill'),
  ringPct:        () => $('ring-pct'),
  ringLabel:      () => $('ring-label'),
  statStatus:     () => $('stat-status-val'),
  statDone:       () => $('stat-done'),
  statTotal:      () => $('stat-total'),
  statDriver:     () => $('stat-driver-val'),
  statRunId:      () => $('stat-run-id-val'),
  taskFeed:       () => $('task-feed'),
  taskEmpty:      () => $('task-empty-state'),
  taskBadge:      () => $('task-count-badge'),
  driverChip:     () => $('driver-chip'),
  driverChipName: () => $('driver-chip-name'),
  repoLabel:      () => $('repo-root-label'),
  btnPause:       () => $('btn-pause'),
  btnResume:      () => $('btn-resume'),
  btnStop:        () => $('btn-stop'),
  btnNewRun:      () => $('btn-new-run'),
  reviewBanner:   () => $('review-banner'),
  reviewBannerMsg:() => $('review-banner-msg'),
  btnOpenReview:  () => $('btn-open-review'),
  reviewPanel:    () => $('review-panel'),
  btnCloseReview: () => $('btn-close-review'),
  reviewDiffView: () => $('review-diff-view'),
  reviewValidMsg: () => $('review-validation-msg'),
  reworkNoteWrap: () => $('rework-note-wrap'),
  reworkNote:     () => $('rework-note'),
  btnSubmitReview:() => $('btn-submit-review'),
};

// ── Ring rendering ────────────────────────────────────────────────────────────

function setRing(pct, status) {
  const fill  = els.ringFill();
  const label = els.ringLabel();
  const pctEl = els.ringPct();
  if (!fill) return;

  const offset = RING_CIRCUMFERENCE * (1 - Math.max(0, Math.min(1, pct / 100)));
  fill.style.strokeDashoffset = offset;
  fill.dataset.status = status || 'idle';

  if (pctEl) pctEl.textContent = Math.round(pct) + '%';
  if (label) label.textContent = STATUS_LABEL[status] || status || 'Idle';
}

// ── Task row rendering ────────────────────────────────────────────────────────

function renderTaskRow(task) {
  const feed    = els.taskFeed();
  const emptyEl = els.taskEmpty();
  if (!feed) return;

  if (emptyEl) emptyEl.style.display = 'none';

  const existing = feed.querySelector(`[data-task-id="${CSS.escape(String(task.id))}"]`);

  const status    = task.status || 'pending';
  const badgeType = TASK_BADGE[status] || 'muted';
  const files     = Array.isArray(task.files) ? task.files : [];
  const attempt   = task.attempt != null ? `attempt ${task.attempt}` : '';

  const fileTags = files.slice(0, 5).map(f => {
    const short = f.split(/[\\/]/).pop();
    return `<span class="file-tag" title="${escHtml(f)}">${escHtml(short)}</span>`;
  }).join('');
  const moreFiles = files.length > 5
    ? `<span class="file-tag">+${files.length - 5} more</span>`
    : '';

  const scopeChip = task.scope
    ? `<span class="task-scope-chip">${escHtml(task.scope)}</span>`
    : '';

  const reworkHtml = (status === 'rework' || status === 'retrying') && task.rework_reason
    ? `<div class="task-rework">↩ ${escHtml(task.rework_reason)}</div>`
    : '';

  const html = `
    <div class="task-row-header">
      <span class="task-id">#${escHtml(String(task.id))}</span>
      <span class="badge badge--${badgeType}">${escHtml(status)}</span>
      ${scopeChip}
      <div class="task-files">${fileTags}${moreFiles}</div>
      ${attempt ? `<span class="task-attempt">${escHtml(attempt)}</span>` : ''}
    </div>
    ${task.instruction
      ? `<div class="task-instruction">${escHtml(task.instruction)}</div>`
      : ''}
    ${reworkHtml}
  `;

  if (existing) {
    existing.dataset.status = status;
    existing.innerHTML = html;
  } else {
    const row = document.createElement('div');
    row.className = 'task-row';
    row.dataset.status = status;
    row.dataset.taskId = String(task.id);
    row.innerHTML = html;
    feed.prepend(row);
  }
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── UI state sync from store ──────────────────────────────────────────────────

function syncStats() {
  const status    = store.get('runStatus');
  const done      = store.get('completedTasks');
  const total     = store.get('totalTasks');
  const driver    = store.get('driver');
  const runId     = store.get('runId');
  const isPaused  = store.get('isPaused');
  const repoRoot  = store.get('repoRoot');

  // Stats row
  const statusEl = els.statStatus();
  if (statusEl) {
    statusEl.textContent = isPaused ? 'Paused' : (STATUS_LABEL[status] || status);
  }
  const doneEl = els.statDone(); if (doneEl) doneEl.textContent = done;
  const totEl  = els.statTotal(); if (totEl)  totEl.textContent = total;
  const drvEl  = els.statDriver(); if (drvEl) drvEl.textContent = driver || '—';
  const ridEl  = els.statRunId();
  if (ridEl) ridEl.textContent = runId ? String(runId).slice(0, 8) : '—';

  // Task count badge
  const tasks   = store.get('tasks') || {};
  const taskCnt = els.taskBadge();
  if (taskCnt) taskCnt.textContent = Object.keys(tasks).length;

  // Ring
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const rStatus = isPaused ? 'stopped'
    : (status === 'running' ? 'running'
    : status === 'success'  ? 'success'
    : status === 'failure'  ? 'failure'
    : status === 'stopped'  ? 'stopped'
    : 'idle');
  setRing(pct, rStatus);

  // Driver chip
  const chip = els.driverChip();
  const chipName = els.driverChipName();
  if (chip && chipName) {
    if (driver && status !== 'idle') {
      chip.style.display = '';
      chipName.textContent = driver;
    } else {
      chip.style.display = 'none';
    }
  }

  // Repo label
  const repoEl = els.repoLabel();
  if (repoEl && repoRoot) repoEl.textContent = repoRoot;

  // Control buttons
  const isActive = status === 'running' || isPaused;
  const btnPause  = els.btnPause();
  const btnResume = els.btnResume();
  const btnStop   = els.btnStop();

  if (btnPause)  btnPause.style.display  = (isActive && !isPaused) ? '' : 'none';
  if (btnResume) btnResume.style.display = isPaused ? '' : 'none';
  if (btnStop)   btnStop.style.display   = isActive ? '' : 'none';
}

// ── SSE event handlers ────────────────────────────────────────────────────────

function handleStart(data) {
  store.set('runStatus', 'running');
  store.set('isPaused', false);
  store.set('runId', data.run_id || null);
  store.set('driver', data.driver || '');
  store.set('repoRoot', data.repo_root || '');
  store.set('totalTasks', 0);
  store.set('completedTasks', 0);
  store.set('tasks', {});
  store.set('reviewPending', null);

  // Clear task feed
  const feed = els.taskFeed();
  if (feed) {
    feed.innerHTML = '';
    const emptyEl = els.taskEmpty();
    if (emptyEl) feed.appendChild(emptyEl);
  }
  hideReview();
  syncStats();
}

function handlePlanReady(data) {
  if (data.total_tasks != null) store.set('totalTasks', data.total_tasks);
  syncStats();
}

function handleTaskUpdate(data) {
  const tasks = { ...(store.get('tasks') || {}) };
  const task  = data.task || data;
  if (!task.id) return;
  tasks[task.id] = task;
  store.set('tasks', tasks);

  // Update completed count
  const done = Object.values(tasks).filter(t =>
    t.status === 'approved' || t.status === 'success' || t.status === 'failed' || t.status === 'failure'
  ).length;
  store.set('completedTasks', done);

  renderTaskRow(task);
  syncStats();
}

function handleProgress(data) {
  if (data.completed != null) store.set('completedTasks', data.completed);
  if (data.total    != null) store.set('totalTasks', data.total);
  syncStats();
}

function handleComplete(data) {
  const status = data.status || 'success';
  store.set('runStatus', status);
  store.set('isPaused', false);
  syncStats();
  hideReview();
  toast(
    status === 'failure' ? 'Run finished with failures.' : status === 'stopped' ? 'Run stopped.' : 'Run completed successfully.',
    status === 'failure' ? 'error' : status === 'stopped' ? 'warning' : 'success',
  );
}

function handlePaused() {
  store.set('isPaused', true);
  syncStats();
  toast('Run paused — waiting for resume.', 'info');
}

function handleResumed() {
  store.set('isPaused', false);
  store.set('runStatus', 'running');
  syncStats();
  toast('Run resumed.', 'success');
}

function handleStopped() {
  store.set('runStatus', 'stopped');
  store.set('isPaused', false);
  syncStats();
  hideReview();
  toast('Run stopped.', 'warning');
}

function handleError(data) {
  store.set('runStatus', 'failure');
  store.set('isPaused', false);
  syncStats();
  toast(data.message || 'An error occurred.', 'error', 8000, 'Run Error');
}

function handleReviewRequired(data) {
  store.set('reviewPending', data);
  const banner = els.reviewBanner();
  const msg    = els.reviewBannerMsg();
  if (msg) msg.textContent = data.validation_message || 'A task needs your approval before the run continues.';
  if (banner) banner.style.display = '';
  toast('Human review required — check the dashboard.', 'warning', 0, 'Review Required');
}

// ── Review panel ──────────────────────────────────────────────────────────────

function hideReview() {
  const banner = els.reviewBanner();
  const panel  = els.reviewPanel();
  if (banner) banner.style.display = 'none';
  if (panel)  panel.style.display  = 'none';
  store.set('reviewPending', null);
}

async function openReviewPanel() {
  const panel = els.reviewPanel();
  if (!panel) return;
  panel.style.display = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const diffEl   = els.reviewDiffView();
  const validMsg = els.reviewValidMsg();
  const pending  = store.get('reviewPending');

  if (validMsg && pending) {
    validMsg.textContent = pending.validation_message || '';
  }

  if (diffEl) {
    diffEl.textContent = 'Loading diff…';
    try {
      const res = await fetch('/api/run/review/current');
      if (res.ok) {
        const d = await res.json();
        renderDiff(diffEl, d.request?.diff || d.diff || '');
      } else {
        diffEl.textContent = '(no diff available)';
      }
    } catch (_) {
      diffEl.textContent = '(failed to load diff)';
    }
  }
}

function renderDiff(pre, raw) {
  if (!raw) { pre.textContent = '(empty diff)'; return; }
  pre.innerHTML = raw
    .split('\n')
    .map(line => {
      if (line.startsWith('+') && !line.startsWith('+++'))
        return `<span class="diff-add">${escHtml(line)}</span>`;
      if (line.startsWith('-') && !line.startsWith('---'))
        return `<span class="diff-del">${escHtml(line)}</span>`;
      if (line.startsWith('@@') || line.startsWith('diff ') || line.startsWith('index '))
        return `<span class="diff-meta">${escHtml(line)}</span>`;
      return escHtml(line);
    })
    .join('\n');
}

async function submitReview() {
  const decision = document.querySelector('input[name="review-decision"]:checked')?.value;
  if (!decision) return;

  const note    = els.reworkNote()?.value?.trim() || '';
  const payload = { decision };
  if (decision === 'rework') payload.note = note;

  const btn = els.btnSubmitReview();
  if (btn) btn.disabled = true;

  try {
    await apiPost('/api/run/review/submit', payload);
    hideReview();
    toast('Decision submitted.', 'success');
  } catch (err) {
    toast(err.message || 'Failed to submit review.', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Initial status hydration ──────────────────────────────────────────────────

async function hydrate() {
  try {
    const s = await fetch('/api/run/status').then(r => r.json());
    store.set('runStatus',      s.status      || 'idle');
    store.set('isPaused',       s.paused      || false);
    store.set('totalTasks',     s.total_tasks || 0);
    store.set('completedTasks', s.completed_tasks ?? s.done_tasks ?? 0);
    store.set('driver',         s.driver      || '');
    store.set('repoRoot',       s.repo_root   || '');
    store.set('runId',          s.run_id      || null);
  } catch (_) {}

  // Load existing tasks
  try {
    const payload = await fetch('/api/run/tasks').then(r => r.json());
    const tasks = Array.isArray(payload?.tasks) ? payload.tasks : [];
    if (tasks.length) {
      const map = {};
      tasks.forEach(t => { map[t.id] = t; renderTaskRow(t); });
      store.set('tasks', map);
      if (payload?.total != null) {
        store.set('totalTasks', payload.total);
      }
      if (payload?.completed != null) {
        store.set('completedTasks', payload.completed);
      }
    }
  } catch (_) {}

  // Check pending review
  try {
    const rev = await fetch('/api/run/review/current').then(r => r.json());
    if (rev?.pending && rev.request?.task_id) {
      handleReviewRequired({
        task_id: rev.request.task_id,
        validation_message: rev.request.validation_message || '',
      });
    }
  } catch (_) {}

  syncStats();
}

// ── Wire up controls ──────────────────────────────────────────────────────────

function bindControls() {
  els.btnPause()?.addEventListener('click', async () => {
    try {
      await apiPost('/api/run/pause');
      store.set('isPaused', true);
      syncStats();
    } catch (err) {
      toast(err.message || 'Could not pause.', 'error');
    }
  });

  els.btnResume()?.addEventListener('click', async () => {
    try {
      await apiPost('/api/run/resume');
      store.set('isPaused', false);
      store.set('runStatus', 'running');
      syncStats();
    } catch (err) {
      toast(err.message || 'Could not resume.', 'error');
    }
  });

  els.btnStop()?.addEventListener('click', async () => {
    if (!confirm('Stop the current run?')) return;
    try {
      await apiPost('/api/run/stop');
      store.set('runStatus', 'stopped');
      store.set('isPaused', false);
      syncStats();
    } catch (err) {
      toast(err.message || 'Could not stop.', 'error');
    }
  });

  els.btnOpenReview()?.addEventListener('click',  openReviewPanel);
  els.btnCloseReview()?.addEventListener('click', () => {
    const panel = els.reviewPanel();
    if (panel) panel.style.display = 'none';
  });

  els.btnSubmitReview()?.addEventListener('click', submitReview);

  // Show/hide rework textarea based on radio choice
  document.querySelectorAll('input[name="review-decision"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const wrap = els.reworkNoteWrap();
      if (wrap) wrap.style.display = radio.value === 'rework' ? '' : 'none';
    });
  });
}

// ── SSE connection ────────────────────────────────────────────────────────────

function connectSSE() {
  const sse = new SSEClient('/api/run/stream');
  sse
    .on('start',           handleStart)
    .on('plan_ready',      handlePlanReady)
    .on('task_update',     handleTaskUpdate)
    .on('progress',        handleProgress)
    .on('complete',        handleComplete)
    .on('paused',          handlePaused)
    .on('resumed',         handleResumed)
    .on('stopped',         handleStopped)
    .on('error',           handleError)
    .on('review_required', handleReviewRequired)
    .connect();

  // Disconnect when page unloads
  window.addEventListener('beforeunload', () => sse.disconnect());
}

// ── Entry point ───────────────────────────────────────────────────────────────

async function init() {
  bindControls();
  await hydrate();
  connectSSE();
}

init();
