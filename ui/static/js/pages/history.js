// pages/history.js — History page controller

import { apiDelete, apiPost } from '/static/js/core/api.js';
import { toast }              from '/static/js/core/toast.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

const $   = id => document.getElementById(id);
const esc = s  => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const STATUS_BADGE = {
  success: 'badge--success',
  failure: 'badge--danger',
  stopped: 'badge--warning',
  running: 'badge--accent',
  error:   'badge--danger',
};

function fmtElapsed(sec) {
  if (!sec && sec !== 0) return '—';
  if (sec < 60)  return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

function fmtWhen(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts.replace(' ', 'T'));
    if (isNaN(d)) return ts;
    const now  = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60)   return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch (_) { return ts; }
}

// ── State ─────────────────────────────────────────────────────────────────────

let _allEntries = [];
let _modalEntry = null;

// ── Load + render ─────────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    _allEntries = res.ok ? await res.json() : [];
  } catch (_) {
    _allEntries = [];
  }
  applyFilter();
}

function applyFilter() {
  const q      = ($('history-search')?.value || '').toLowerCase();
  const status = $('history-filter')?.value || '';

  const filtered = _allEntries.filter(e => {
    const matchQ = !q || (e.goal || '').toLowerCase().includes(q);
    const matchS = !status || (e.status || '') === status;
    return matchQ && matchS;
  });

  renderTable(filtered);
}

function renderTable(entries) {
  const tbody   = $('history-tbody');
  const wrap    = $('history-table-wrap');
  const empty   = $('history-empty');
  const countEl = $('history-count-label');
  const clearBtn = $('btn-clear-all');

  if (countEl) countEl.textContent = `${_allEntries.length} run${_allEntries.length !== 1 ? 's' : ''}`;
  if (clearBtn) clearBtn.style.display = _allEntries.length > 0 ? '' : 'none';

  if (!entries.length) {
    if (wrap)  wrap.style.display  = 'none';
    if (empty) empty.style.display = '';
    if (tbody) tbody.innerHTML = '';
    return;
  }

  if (wrap)  wrap.style.display  = '';
  if (empty) empty.style.display = 'none';
  if (!tbody) return;

  tbody.innerHTML = entries.map(e => {
    const badgeCls = STATUS_BADGE[e.status] || 'badge--muted';
    const tasks    = e.tasks != null ? e.tasks : '—';
    const elapsed  = fmtElapsed(e.elapsed);
    const when     = fmtWhen(e.timestamp);
    const dryLabel = e.dry_run ? ' <span class="badge badge--info" style="font-size:10px">dry-run</span>' : '';

    return `
      <tr data-id="${esc(e.id)}">
        <td>
          <div class="history-goal">${esc(e.goal || '(no goal)')}</div>
          <div class="history-meta">${esc(e.repo_root || '')}${e.aider_model ? ' · ' + esc(e.aider_model) : ''}</div>
        </td>
        <td>
          <span class="badge ${badgeCls}">${esc(e.status || 'unknown')}</span>
          ${dryLabel}
        </td>
        <td style="font-variant-numeric:tabular-nums">${tasks}</td>
        <td style="font-variant-numeric:tabular-nums;white-space:nowrap">${elapsed}</td>
        <td style="white-space:nowrap;color:var(--color-text-subtle);font-size:var(--font-size-xs)"
            title="${esc(e.timestamp || '')}">${when}</td>
        <td>
          <div class="history-actions">
            <button class="btn btn--secondary btn--sm" data-action="log" data-id="${esc(e.id)}" title="View log">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="13" height="13">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z"/>
              </svg>
              Log
            </button>
            <button class="btn btn--secondary btn--sm" data-action="rerun" data-id="${esc(e.id)}" title="Re-run with same settings">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="13" height="13">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/>
              </svg>
              Re-run
            </button>
            <div class="delete-confirm" id="del-confirm-${esc(e.id)}">
              <span>Delete?</span>
              <button class="btn btn--danger btn--sm" data-action="delete-confirm" data-id="${esc(e.id)}">Yes</button>
              <button class="btn btn--secondary btn--sm" data-action="delete-cancel" data-id="${esc(e.id)}">No</button>
            </div>
            <button class="btn btn--ghost btn--sm" data-action="delete-start" data-id="${esc(e.id)}" title="Delete">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="13" height="13">
                <path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"/>
              </svg>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

// ── Log modal ─────────────────────────────────────────────────────────────────

function openLogModal(entry) {
  _modalEntry = entry;

  const modal     = $('log-modal');
  const title     = $('log-modal-title');
  const sub       = $('log-modal-sub');
  const stats     = $('log-modal-stats');
  const terminal  = $('log-modal-terminal');

  if (!modal) return;

  if (title) title.textContent = entry.goal || '(no goal)';
  if (sub)   sub.textContent   = `${entry.timestamp || ''}  ·  ${entry.repo_root || ''}`;

  // Stats
  if (stats) {
    const badgeCls = STATUS_BADGE[entry.status] || 'badge--muted';
    stats.innerHTML = `
      <div class="log-stat">
        <span class="log-stat__label">Status</span>
        <span class="log-stat__value"><span class="badge ${badgeCls}">${esc(entry.status || '—')}</span></span>
      </div>
      <div class="log-stat">
        <span class="log-stat__label">Tasks</span>
        <span class="log-stat__value">${entry.tasks ?? '—'}</span>
      </div>
      <div class="log-stat">
        <span class="log-stat__label">Elapsed</span>
        <span class="log-stat__value">${fmtElapsed(entry.elapsed)}</span>
      </div>
      <div class="log-stat">
        <span class="log-stat__label">Model</span>
        <span class="log-stat__value" style="font-size:var(--font-size-xs);font-family:var(--font-mono)">${esc(entry.aider_model || '—')}</span>
      </div>
    `;
  }

  // Log lines
  if (terminal) {
    const lines = Array.isArray(entry.log) ? entry.log : [];
    if (lines.length) {
      terminal.textContent = lines.join('\n');
    } else {
      terminal.textContent = '(no log captured for this run)';
    }
    terminal.scrollTop = terminal.scrollHeight;
  }

  modal.style.display = '';
  modal.focus();
}

function closeLogModal() {
  const modal = $('log-modal');
  if (modal) modal.style.display = 'none';
  _modalEntry = null;
}

// ── Re-run ────────────────────────────────────────────────────────────────────

async function rerunEntry(entry) {
  // Persist settings from this history entry to /api/settings, then navigate to /run
  const settings = {
    goal:               entry.goal              || '',
    repo_root:          entry.repo_root         || '',
    aider_model:        entry.aider_model       || '',
    supervisor_command: entry.supervisor_command || '',
    dry_run:            entry.dry_run            || false,
  };
  try {
    await apiPost('/api/settings', settings);
  } catch (_) { /* non-fatal — navigate anyway */ }
  window.location.href = '/run';
}

// ── Delete ────────────────────────────────────────────────────────────────────

async function deleteEntry(id) {
  try {
    await apiDelete(`/api/history/${id}`);
    _allEntries = _allEntries.filter(e => e.id !== id);
    applyFilter();
    toast('Run deleted.', 'success');
  } catch (err) {
    toast(err.message || 'Delete failed.', 'error');
  }
}

async function clearAll() {
  try {
    await apiDelete('/api/history');
    _allEntries = [];
    applyFilter();
    $('clear-all-confirm').style.display = 'none';
    $('btn-clear-all').style.display = 'none';
    toast('History cleared.', 'success');
  } catch (err) {
    toast(err.message || 'Clear failed.', 'error');
  }
}

// ── Event delegation ──────────────────────────────────────────────────────────

function bindTableActions() {
  const tbody = $('history-tbody');
  if (!tbody) return;

  tbody.addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const id     = btn.dataset.id;
    const entry  = _allEntries.find(e => e.id === id);

    if (action === 'log' && entry) {
      openLogModal(entry);
    }

    if (action === 'rerun' && entry) {
      rerunEntry(entry);
    }

    if (action === 'delete-start') {
      const confirm = $(`del-confirm-${id}`);
      if (confirm) confirm.classList.add('--visible');
      btn.style.display = 'none';
    }

    if (action === 'delete-cancel') {
      const confirm = $(`del-confirm-${id}`);
      if (confirm) confirm.classList.remove('--visible');
      // Restore delete icon button
      const row = btn.closest('tr');
      const delBtn = row?.querySelector('[data-action="delete-start"]');
      if (delBtn) delBtn.style.display = '';
    }

    if (action === 'delete-confirm') {
      deleteEntry(id);
    }
  });
}

// ── Bind all controls ─────────────────────────────────────────────────────────

function bindControls() {
  // Search + filter
  $('history-search')?.addEventListener('input',  applyFilter);
  $('history-filter')?.addEventListener('change', applyFilter);

  // Clear all (two-step)
  $('btn-clear-all')?.addEventListener('click', () => {
    $('clear-all-confirm').style.display = '';
  });
  $('btn-clear-all-cancel')?.addEventListener('click', () => {
    $('clear-all-confirm').style.display = 'none';
  });
  $('btn-clear-all-confirm')?.addEventListener('click', clearAll);

  // Modal close
  $('log-modal-close')?.addEventListener('click', closeLogModal);
  $('log-modal')?.addEventListener('click', e => {
    if (e.target === $('log-modal')) closeLogModal(); // click on backdrop
  });
  $('log-modal-rerun')?.addEventListener('click', () => {
    if (_modalEntry) rerunEntry(_modalEntry);
  });

  // Keyboard: Esc closes modal
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeLogModal();
  });

  bindTableActions();
}

// ── Entry point ───────────────────────────────────────────────────────────────

function init() {
  bindControls();
  loadHistory();
}

init();
