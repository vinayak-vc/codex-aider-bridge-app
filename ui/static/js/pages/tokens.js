// pages/tokens.js — Token analytics page controller

import { SSEClient } from '/static/js/core/sse.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

const $   = id => document.getElementById(id);
const esc = s  => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function fmtNum(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString();
}

function fmtPct(n) {
  if (n == null) return '—';
  return Number(n).toFixed(1) + '%';
}

function fmtWhen(ts) {
  if (!ts) return '—';
  try {
    const d    = new Date(ts);
    if (isNaN(d)) return ts;
    const diff = Math.floor((Date.now() - d) / 1000);
    if (diff < 60)    return 'just now';
    if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch (_) { return ts; }
}

function fmtElapsed(sec) {
  if (!sec && sec !== 0) return '—';
  if (sec < 60)   return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

// ── State ─────────────────────────────────────────────────────────────────────

let _sessions      = [];
let _totals        = {};
let _selectedIdx   = null;
let _repoRoot      = '';
let _sse           = null;
let _refreshTimer  = null;

// ── Render top stats ──────────────────────────────────────────────────────────

function renderStats(totals, sessions) {
  $('stat-sessions').textContent   = fmtNum(totals.sessions_count   ?? sessions.length);
  $('stat-tasks').textContent      = fmtNum(totals.tasks_executed_total ?? 0);
  $('stat-supervisor').textContent = fmtNum(totals.supervisor_tokens_total ?? 0);

  // total_ai_tokens = sum of session.total_ai_tokens across sessions
  const totalAI = sessions.reduce((sum, s) =>
    sum + (s.session?.total_ai_tokens ?? s.supervisor?.total ?? 0), 0);
  $('stat-ai-total').textContent = fmtNum(totalAI);
  $('stat-aider').textContent     = '~' + fmtNum(totals.aider_tokens_total ?? 0);
  $('stat-saved').textContent    = fmtNum(totals.tokens_saved_total ?? 0);

  // Savings bar
  const pct = totals.savings_percent_avg ?? 0;
  const bar = $('savings-bar-fill');
  if (bar) bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
  const pctEl = $('savings-bar-pct');
  if (pctEl) pctEl.textContent = fmtPct(pct);

  // Show interactive notice if any session has estimate tokens
  const hasEstimate = sessions.some(s => s.session?.is_estimate);
  const notice = $('interactive-notice');
  if (notice) notice.style.display = hasEstimate ? '' : 'none';
}

// ── Bar chart ─────────────────────────────────────────────────────────────────

function renderChart(sessions) {
  const container = $('sessions-chart');
  if (!container) return;

  if (!sessions.length) { container.innerHTML = ''; return; }

  // Find max AI tokens for bar scaling
  const maxTokens = Math.max(
    1,
    ...sessions.map(s => s.session?.total_ai_tokens ?? s.supervisor?.total ?? 0)
  );

  container.innerHTML = sessions.map((s, i) => {
    const aiTokens   = s.session?.total_ai_tokens ?? s.supervisor?.total ?? 0;
    const saved      = s.savings?.tokens_saved ?? 0;
    const productive = s.productivity?.is_productive !== false;
    const pct        = Math.round((aiTokens / maxTokens) * 100);
    const barClass   = productive ? '' : '--waste';
    const goal       = (s.goal || '(no goal)').slice(0, 28);
    const when       = fmtWhen(s.timestamp);

    return `
      <div class="session-bar-row" data-idx="${i}" id="chart-row-${i}">
        <span class="session-bar-label" title="${esc(s.goal || '')}">${esc(goal)}</span>
        <div class="session-bar-track">
          <div class="session-bar-fill ${barClass}" style="width:${pct}%"></div>
        </div>
        <span class="session-bar-value">${fmtNum(aiTokens)} · ${when}</span>
      </div>
    `;
  }).join('');

  container.querySelectorAll('.session-bar-row').forEach(row => {
    row.addEventListener('click', () => selectSession(parseInt(row.dataset.idx, 10)));
  });
}

// ── Sessions table ────────────────────────────────────────────────────────────

function renderTable(sessions) {
  const tbody = $('sessions-tbody');
  if (!tbody) return;

  if (!sessions.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding:24px;text-align:center;color:var(--color-text-subtle)">No sessions recorded.</td></tr>`;
    return;
  }

  tbody.innerHTML = sessions.map((s, i) => {
    const aiTokens   = s.session?.total_ai_tokens ?? s.supervisor?.total ?? 0;
    const aiderEst   = s.aider?.estimated_tokens ?? 0;
    const saved      = s.savings?.tokens_saved ?? 0;
    const savePct    = s.savings?.savings_percent ?? 0;
    const tasks      = s.aider?.tasks_executed ?? 0;
    const productive = s.productivity?.is_productive !== false;

    return `
      <tr data-idx="${i}" id="table-row-${i}">
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${esc(s.goal || '')}">${esc((s.goal || '(no goal)').slice(0, 40))}</td>
        <td style="font-variant-numeric:tabular-nums">${tasks}</td>
        <td style="font-variant-numeric:tabular-nums">${fmtNum(aiTokens)}</td>
        <td style="font-variant-numeric:tabular-nums;color:var(--color-info)">~${fmtNum(aiderEst)}</td>
        <td style="font-variant-numeric:tabular-nums;color:var(--color-success)">${fmtNum(saved)}</td>
        <td>
          <span class="badge ${savePct >= 50 ? 'badge--success' : savePct > 0 ? 'badge--muted' : 'badge--warning'}"
                style="font-size:10px">${fmtPct(savePct)}</span>
          ${!productive ? '<span class="badge badge--warning" style="font-size:10px;margin-left:4px">no tasks</span>' : ''}
        </td>
        <td style="color:var(--color-text-subtle)">${fmtWhen(s.timestamp)}</td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('tr[data-idx]').forEach(row => {
    row.addEventListener('click', () => selectSession(parseInt(row.dataset.idx, 10)));
  });
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function renderDetail(s, idx) {
  $('detail-empty').style.display   = 'none';
  $('detail-content').style.display = '';

  $('detail-goal').textContent = s.goal || '(no goal)';
  $('detail-meta').textContent =
    `${s.timestamp ? new Date(s.timestamp).toLocaleString() : ''}` +
    `${s.repo_root ? '  ·  ' + s.repo_root : ''}` +
    `  ·  ${fmtElapsed(s.elapsed_seconds)}`;

  const sup   = s.supervisor  || {};
  const sess  = s.session     || {};
  const aider = s.aider       || {};
  const sav   = s.savings     || {};

  renderDetailSection('detail-supervisor', 'Supervisor tokens', [
    ['Plan in',     fmtNum(sup.plan_in)],
    ['Plan out',    fmtNum(sup.plan_out)],
    ['Review in',   fmtNum(sup.review_in)],
    ['Review out',  fmtNum(sup.review_out)],
    ['Subplan in',  fmtNum(sup.subplan_in)],
    ['Subplan out', fmtNum(sup.subplan_out)],
    ['Total',       fmtNum(sup.total)],
  ]);

  renderDetailSection('detail-savings', 'Savings', [
    ['Estimated direct', fmtNum(sav.estimated_direct_tokens)],
    ['Total AI tokens',  fmtNum(sav.total_ai_tokens)],
    ['Tokens saved',     fmtNum(sav.tokens_saved)],
    ['Savings %',        fmtPct(sav.savings_percent)],
  ]);

  const aiderEst = aider.estimated_tokens ?? 0;

  renderDetailSection('detail-aider', 'Aider (local LLM)', [
    ['Tasks executed',  fmtNum(aider.tasks_executed)],
    ['Tasks skipped',   fmtNum(aider.tasks_skipped)],
    ['Reworks',         fmtNum(aider.reworks)],
    ['Sub-plans',       fmtNum(aider.subplans_generated)],
    ['Est. tokens',     '~' + fmtNum(aiderEst)],
  ]);

  const estimateLabel = sess.is_estimate ? ' (est.)' : ' (exact)';
  renderDetailSection('detail-session', 'Interactive session', [
    ['Session tokens', fmtNum(sess.tokens) + estimateLabel],
    ['Total AI',       fmtNum(sess.total_ai_tokens)],
    ['Supervisor cmd', (s.supervisor_command || '—').slice(0, 24)],
  ]);

  // Savings comparison card
  const cmpEl = $('detail-comparison');
  if (cmpEl) {
    const directTotal  = sav.estimated_direct_tokens ?? 0;
    const aiTotal      = sav.total_ai_tokens ?? 0;
    const bridgeTotal  = aiTotal + aiderEst;
    const saved        = sav.tokens_saved ?? 0;
    const savedPct     = sav.savings_percent ?? 0;

    $('cmp-without').textContent = fmtNum(directTotal) + ' tokens';
    $('cmp-with').textContent    = fmtNum(bridgeTotal) + ' tokens';
    $('cmp-with-desc').textContent = `${fmtNum(aiTotal)} cloud + ~${fmtNum(aiderEst)} local`;
    $('cmp-saved').textContent   = fmtNum(saved) + ' tokens';
    $('cmp-pct').textContent     = fmtPct(savedPct);
    cmpEl.style.display = directTotal > 0 ? '' : 'none';
  }

  // Per-task Aider breakdown
  const perTask   = aider.per_task || [];
  const ptWrap    = $('detail-per-task');
  const ptTbody   = $('per-task-tbody');
  if (ptWrap && ptTbody) {
    if (perTask.length > 0) {
      ptTbody.innerHTML = perTask.map(t =>
        `<tr><td>Task ${esc(t.task_id)}</td><td>~${fmtNum(t.estimated_tokens)}</td></tr>`
      ).join('') +
        `<tr style="font-weight:600"><td>Total</td><td>~${fmtNum(aiderEst)}</td></tr>`;
      ptWrap.style.display = '';
    } else {
      ptWrap.style.display = 'none';
    }
  }

  const noteEl = $('detail-note');
  if (noteEl && sav.note) {
    noteEl.textContent  = sav.note;
    noteEl.style.display = '';
  } else if (noteEl) {
    noteEl.style.display = 'none';
  }
}

function renderDetailSection(id, title, rows) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = `
    <div class="detail-section-title">${esc(title)}</div>
    ${rows.map(([k, v]) => `
      <div class="detail-row">
        <span class="detail-key">${esc(k)}</span>
        <span class="detail-value">${esc(String(v))}</span>
      </div>
    `).join('')}
  `;
}

// ── Selection ─────────────────────────────────────────────────────────────────

function selectSession(idx) {
  if (idx < 0 || idx >= _sessions.length) return;
  _selectedIdx = idx;

  // Highlight chart row
  document.querySelectorAll('.session-bar-row').forEach((r, i) =>
    r.classList.toggle('--active', i === idx));

  // Highlight table row
  document.querySelectorAll('.sessions-table tr[data-idx]').forEach((r, i) =>
    r.classList.toggle('--active', i === idx));

  renderDetail(_sessions[idx], idx);
}

// ── Data loading ──────────────────────────────────────────────────────────────

async function loadTokens() {
  try {
    if (!_repoRoot) {
      const settings = await fetch('/api/settings').then(r => r.json());
      _repoRoot = settings?.repo_root || '';
    }

    const url = _repoRoot
      ? `/api/reports/tokens?repo_root=${encodeURIComponent(_repoRoot)}`
      : '/api/tokens';
    const data = await fetch(url).then(r => r.json());
    _sessions  = Array.isArray(data.sessions) ? data.sessions : [];
    _totals    = data.totals || {};
  } catch (_) {
    _sessions = [];
    _totals   = {};
  }

  const empty   = $('tokens-empty');
  const content = $('tokens-content');

  if (!_sessions.length) {
    if (empty)   empty.style.display   = '';
    if (content) content.style.display = 'none';
    return;
  }

  if (empty)   empty.style.display   = 'none';
  if (content) content.style.display = '';

  renderStats(_totals, _sessions);
  renderChart(_sessions);
  renderTable(_sessions);

  // Auto-select most recent session
  selectSession(0);
}

function scheduleRefresh() {
  if (_refreshTimer) {
    clearTimeout(_refreshTimer);
  }
  _refreshTimer = setTimeout(() => {
    _refreshTimer = null;
    void loadTokens();
  }, 400);
}

function connectSSE() {
  if (_sse) {
    return;
  }

  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('start', scheduleRefresh)
    .on('token_report', scheduleRefresh)
    .on('progress', scheduleRefresh)
    .on('complete', scheduleRefresh)
    .on('stopped', scheduleRefresh)
    .on('error', scheduleRefresh)
    .connect();

  window.addEventListener('beforeunload', () => _sse?.disconnect(), { once: true });
}

// ── Entry point ───────────────────────────────────────────────────────────────

// ── Diagnostics panel ────────────────────────────────────────────────────────

async function loadDiagnostics() {
  const panel = $('diagnostics-panel');
  if (!panel) return;

  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const repo = _repoRoot || settings?.repo_root || '';
    if (!repo) { panel.style.display = 'none'; return; }

    const resp = await fetch(`/api/reports/diagnostics?repo_root=${encodeURIComponent(repo)}`);
    if (!resp.ok) { panel.style.display = 'none'; return; }
    const diag = await resp.json();

    // Status badge
    const badge = $('diag-status-badge');
    if (badge) {
      const s = diag.status || 'unknown';
      badge.textContent = s;
      badge.className = `badge ${s === 'success' ? 'badge--success' : 'badge--warning'}`;
    }

    // Summary
    const sumEl = $('diag-summary');
    if (sumEl) sumEl.textContent = diag.ai_summary || 'No summary available.';

    // Blocking patterns
    const patternsEl = $('diag-patterns');
    if (patternsEl) {
      const patterns = diag.blocking_patterns || [];
      if (patterns.length) {
        patternsEl.innerHTML = `
          <div class="detail-section-title" style="margin-bottom:8px">Blocking Patterns Detected</div>
          ${patterns.map(p => `
            <div style="padding:8px 12px;margin-bottom:8px;background:color-mix(in srgb, var(--color-warning) 8%, transparent);border:1px solid color-mix(in srgb, var(--color-warning) 25%, transparent);border-radius:var(--radius-md);font-size:var(--font-size-xs);line-height:1.5">
              <strong>${esc(p.pattern)}</strong> (${p.count}x, tasks: ${p.tasks.join(', ')})<br>
              ${esc(p.suggestion)}
            </div>
          `).join('')}
        `;
      } else {
        patternsEl.innerHTML = '<div style="font-size:var(--font-size-xs);color:var(--color-text-subtle)">No blocking patterns detected.</div>';
      }
    }

    panel.style.display = '';
  } catch (_) {
    panel.style.display = 'none';
  }
}

// ── Entry point ───────────────────────────────────────────────────────────────

function init() {
  $('btn-refresh-tokens')?.addEventListener('click', () => { loadTokens(); loadDiagnostics(); });
  window.addEventListener('bridge:project-switched', event => {
    _repoRoot = event?.detail?.path || '';
    scheduleRefresh();
  });
  connectSSE();
  loadTokens();
  loadDiagnostics();
}

init();
