// pages/setup.js — Setup page controller

import { toast } from '/static/js/core/toast.js';

// ── DOM helpers ───────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

// ── Dependency card renderer ──────────────────────────────────────────────────

function applyCard(name, info) {
  const card    = $(`card-${name}`);
  const dot     = $(`dot-${name}`);
  const verEl   = $(`ver-${name}`);
  const pathEl  = $(`path-${name}`);
  const hintEl  = $(`hint-${name}`);
  if (!card) return;

  // Determine state
  let state = 'ok';
  if (!info.installed) {
    state = 'error';
  } else if (info.hint || info.ok === false) {
    state = 'warn';
  }

  card.className = `check-card --${state}`;

  if (dot) {
    dot.className = `check-dot --${state === 'error' ? 'error' : state === 'warn' ? 'warn' : 'ok'}`;
  }

  if (verEl) {
    if (!info.installed) {
      verEl.textContent = 'Not installed';
      verEl.style.color = 'var(--color-danger)';
    } else {
      verEl.textContent = info.version || 'Installed';
      verEl.style.color = '';
    }
  }

  if (pathEl) pathEl.textContent = info.path || '';

  if (hintEl) {
    if (info.hint) {
      hintEl.textContent    = info.hint;
      hintEl.style.display  = '';
    } else {
      hintEl.style.display = 'none';
    }
  }

  // Aider-specific: show install button if not installed
  const installBtn = $('btn-install-aider');
  if (name === 'aider' && installBtn) {
    installBtn.style.display = !info.installed ? '' : 'none';
  }
}

// ── Nav badge (issues count) ──────────────────────────────────────────────────

function updateNavBadge(checks) {
  const issues = Object.values(checks).filter(c => !c.installed || c.hint || c.ok === false).length;
  const badge  = $('setup-issues-badge');
  if (!badge) return;
  if (issues > 0) {
    badge.textContent    = issues;
    badge.style.display  = '';
  } else {
    badge.style.display = 'none';
  }
}

// ── Run dependency checks ─────────────────────────────────────────────────────

async function runChecks() {
  // Reset all cards to loading
  ['python','aider','ollama','codex','claude'].forEach(n => {
    const card = $(`card-${n}`);
    const dot  = $(`dot-${n}`);
    const ver  = $(`ver-${n}`);
    if (card) card.className = 'check-card --loading';
    if (dot)  dot.className  = 'check-dot --spin';
    if (ver)  ver.textContent = 'Checking…';
  });

  try {
    const checks = await fetch('/api/check').then(r => r.json());
    Object.entries(checks).forEach(([name, info]) => applyCard(name, info));
    updateNavBadge(checks);

    // Show/hide Aider install section
    const aiderSection = $('aider-install-section');
    if (aiderSection) {
      aiderSection.style.display = (!checks.aider?.installed) ? '' : 'none';
    }

    // Show Ollama section if Ollama is installed
    const ollamaSection = $('ollama-section');
    if (ollamaSection && checks.ollama?.installed) {
      ollamaSection.style.display = '';
      renderModelList(checks.ollama.models || []);
    }
  } catch (err) {
    toast('Could not reach /api/check — is Bridge running?', 'error');
  }
}

// ── Ollama model list ─────────────────────────────────────────────────────────

function renderModelList(models) {
  const list = $('model-list');
  if (!list) return;

  if (!models.length) {
    list.innerHTML = `<div class="text-subtle" style="font-size:var(--font-size-sm);padding:8px 0">No models installed. Pull one below.</div>`;
    return;
  }

  list.innerHTML = models.map(m => `
    <div class="model-row">
      <span class="model-row-name">${escHtml(m)}</span>
      <button class="model-row-use" data-model="${escHtml(m)}">Use this model</button>
    </div>
  `).join('');

  list.querySelectorAll('.model-row-use').forEach(btn => {
    btn.addEventListener('click', async () => {
      const model = btn.dataset.model;
      try {
        const settings = await fetch('/api/settings').then(r => r.json());
        settings.aider_model = `ollama/${model}`;
        await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(settings),
        });
        toast(`Model set to ollama/${model}`, 'success');
      } catch (_) {
        toast('Could not save model setting.', 'error');
      }
    });
  });
}

// ── Aider install (SSE stream) ────────────────────────────────────────────────

function startAiderInstall() {
  const terminal  = $('install-terminal');
  const statusLbl = $('install-status-label');
  const btn       = $('btn-run-install');

  if (!terminal) return;

  terminal.innerHTML = '';
  terminal.classList.add('--visible');
  if (btn) btn.disabled = true;
  if (statusLbl) statusLbl.textContent = 'Installing…';

  const es = new EventSource('/api/install/aider');

  es.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (d.line !== undefined) {
        const span = document.createElement('span');
        const l    = d.line;
        if (/error|failed/i.test(l))       span.className = 't-err';
        else if (/successfully|done/i.test(l)) span.className = 't-ok';
        span.textContent = l + '\n';
        terminal.appendChild(span);
        terminal.scrollTop = terminal.scrollHeight;
      }
      if (d.done) {
        es.close();
        if (btn) btn.disabled = false;
        if (d.status === 'success') {
          if (statusLbl) statusLbl.textContent = 'Installed ✓';
          toast('Aider installed successfully.', 'success');
          runChecks();
        } else {
          if (statusLbl) statusLbl.textContent = 'Install failed — see output above.';
          toast('Aider install failed.', 'error');
        }
      }
    } catch (_) {}
  };

  es.onerror = () => {
    es.close();
    if (btn) btn.disabled = false;
    if (statusLbl) statusLbl.textContent = 'Connection error.';
    toast('Lost connection during install.', 'error');
  };
}

// ── Ollama pull (SSE stream) ──────────────────────────────────────────────────

function startOllamaPull() {
  const modelInput = $('pull-model-input');
  const terminal   = $('pull-terminal');
  const btn        = $('btn-pull-model');
  const model      = modelInput?.value.trim();

  if (!model) {
    toast('Enter a model name first.', 'warning');
    modelInput?.focus();
    return;
  }
  if (!terminal) return;

  terminal.textContent = '';
  terminal.classList.add('--visible');
  if (btn) btn.disabled = true;

  fetch('/api/ollama/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(() => {});   // fire-and-forget; stream via EventSource

  // Re-open as SSE (same endpoint streams back)
  const es = new EventSource(`/api/ollama/pull?model=${encodeURIComponent(model)}`);

  // Fallback: use fetch streaming if EventSource doesn't trigger
  // (The pull endpoint is POST — use a simple fetch reader approach)
  es.close();   // close the GET attempt

  const reader_fetch = fetch('/api/ollama/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  });

  reader_fetch.then(async res => {
    const reader = res.body?.getReader();
    const dec    = new TextDecoder();
    if (!reader) return;

    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        const dataLine = part.replace(/^data: /, '');
        try {
          const d = JSON.parse(dataLine);
          if (d.line !== undefined) {
            terminal.textContent += d.line + '\n';
            terminal.scrollTop = terminal.scrollHeight;
          }
          if (d.done) {
            if (btn) btn.disabled = false;
            if (d.status === 'success') {
              toast(`Model "${model}" pulled successfully.`, 'success');
              runChecks();   // refresh model list
            } else {
              toast(`Pull failed for "${model}".`, 'error');
            }
          }
        } catch (_) {}
      }
    }
    if (btn) btn.disabled = false;
  }).catch(() => {
    if (btn) btn.disabled = false;
    toast('Pull request failed.', 'error');
  });
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Entry point ───────────────────────────────────────────────────────────────

function init() {
  $('btn-recheck')?.addEventListener('click', runChecks);
  $('btn-install-aider')?.addEventListener('click', () => {
    $('aider-install-section').style.display = '';
  });
  $('btn-run-install')?.addEventListener('click', startAiderInstall);
  $('btn-pull-model')?.addEventListener('click', startOllamaPull);
  $('pull-model-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') startOllamaPull();
  });

  // GPU
  $('btn-refresh-gpu')?.addEventListener('click', loadGpuInfo);
  $('btn-toggle-gpu-procs')?.addEventListener('click', toggleGpuProcs);
  $('btn-unload-model')?.addEventListener('click', async () => {
    const btn = $('btn-unload-model');
    if (btn) { btn.disabled = true; btn.textContent = 'Unloading...'; }
    try {
      const res = await fetch('/api/system/unload-model', { method: 'POST' }).then(r => r.json());
      if (res.ok) {
        if (btn) btn.textContent = 'Freed!';
        setTimeout(() => { loadGpuInfo(); if (btn) { btn.textContent = 'Free VRAM'; btn.disabled = false; } }, 2000);
      } else {
        alert(res.error || 'Failed to unload model');
        if (btn) { btn.textContent = 'Free VRAM'; btn.disabled = false; }
      }
    } catch (_) {
      if (btn) { btn.textContent = 'Free VRAM'; btn.disabled = false; }
    }
  });

  runChecks();
  loadGpuInfo();
}

// ── GPU ──────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadGpuInfo() {
  try {
    const data = await fetch('/api/system/gpu-processes').then(r => r.json());
    const gpu = data.gpu || {};
    const procs = data.processes || [];
    const body = $('gpu-info-body');
    const title = $('gpu-card-title');
    const countEl = $('gpu-proc-count');

    if (countEl) countEl.textContent = procs.length;

    if (!gpu.has_gpu) {
      if (title) title.textContent = 'No GPU Detected';
      if (body) body.innerHTML = `
        <div style="padding:8px 0;color:var(--color-danger);font-size:var(--font-size-sm)">
          <strong>No NVIDIA GPU found.</strong> Ollama will run on CPU which is extremely slow.
          <br>For usable performance, an NVIDIA GPU with 6GB+ VRAM is required.
        </div>`;
      return;
    }

    if (title) title.textContent = gpu.gpu_name || 'GPU';

    const vramPct = gpu.vram_total_gb > 0
      ? Math.round((gpu.vram_used_gb / gpu.vram_total_gb) * 100) : 0;

    const statusColor = gpu.status === 'gpu_active' ? 'var(--color-success)'
      : gpu.status === 'gpu_available_not_used' ? 'var(--color-danger)'
      : 'var(--color-text-muted)';
    const statusLabel = gpu.status === 'gpu_active' ? 'Active (GPU)'
      : gpu.status === 'gpu_available_not_used' ? 'WARNING: Using CPU!'
      : gpu.status === 'gpu_ready' ? 'Ready'
      : gpu.ollama_backend || '?';

    let html = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
        <div>
          <div style="font-size:var(--font-size-xs);color:var(--color-text-subtle)">VRAM Usage</div>
          <div style="font-size:var(--font-size-lg);font-weight:700">${gpu.vram_used_gb} / ${gpu.vram_total_gb} GB</div>
          <div style="height:6px;background:var(--color-surface-3);border-radius:3px;margin-top:4px;overflow:hidden">
            <div style="height:100%;width:${vramPct}%;background:${vramPct > 80 ? 'var(--color-danger)' : 'var(--color-accent)'};border-radius:3px"></div>
          </div>
        </div>
        <div>
          <div style="font-size:var(--font-size-xs);color:var(--color-text-subtle)">Ollama Backend</div>
          <div style="font-size:var(--font-size-lg);font-weight:700;color:${statusColor}">${esc(statusLabel)}</div>
          <div style="font-size:11px;color:var(--color-text-subtle)">GPU Util: ${gpu.gpu_utilization}%</div>
        </div>
      </div>`;

    if (gpu.hint) {
      const isWarn = gpu.status === 'gpu_available_not_used' || gpu.status === 'cpu_only';
      html += `<div style="padding:8px 12px;border-radius:var(--radius-md);font-size:var(--font-size-xs);line-height:1.6;
        background:${isWarn ? 'color-mix(in srgb, var(--color-danger) 8%, transparent)' : 'var(--color-surface)'};
        border:1px solid ${isWarn ? 'color-mix(in srgb, var(--color-danger) 25%, transparent)' : 'var(--color-border-muted)'};
        color:var(--color-text-muted);white-space:pre-line">${esc(gpu.hint)}</div>`;
    }

    if (body) body.innerHTML = html;

    // Render processes
    renderGpuProcs(procs);
  } catch (ex) {
    const body = $('gpu-info-body');
    if (body) body.innerHTML = '<div class="text-subtle" style="font-size:var(--font-size-xs)">Could not detect GPU.</div>';
  }
}

function renderGpuProcs(procs) {
  const list = $('gpu-proc-list');
  if (!list) return;

  if (!procs.length) {
    list.innerHTML = '<div class="text-subtle" style="font-size:var(--font-size-xs);padding:8px 0;text-align:center">No GPU processes found.</div>';
    return;
  }

  // Protected processes that shouldn't be killed
  const protect = new Set(['explorer.exe', 'csrss.exe', 'dwm.exe', 'winlogon.exe',
    'svchost.exe', 'system', 'searchhost.exe', 'shellexperiencehost.exe']);

  list.innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:var(--font-size-xs)">
      <thead><tr style="color:var(--color-text-subtle);border-bottom:1px solid var(--color-border-muted)">
        <th style="text-align:left;padding:4px 8px">Process</th>
        <th style="text-align:right;padding:4px 8px">PID</th>
        <th style="text-align:right;padding:4px 8px">VRAM (MB)</th>
        <th style="text-align:center;padding:4px 8px;width:60px">Action</th>
      </tr></thead>
      <tbody>
        ${procs.map(p => {
          const isProtected = protect.has(p.name.toLowerCase());
          const isOllama = p.name.toLowerCase().includes('ollama');
          return `<tr style="border-bottom:1px solid var(--color-border-muted)">
            <td style="padding:5px 8px;font-family:var(--font-mono);color:${isOllama ? 'var(--color-success)' : 'var(--color-text-muted)'}">
              ${esc(p.name)} ${isOllama ? '<span class="badge badge--success" style="font-size:9px;margin-left:4px">ollama</span>' : ''}
            </td>
            <td style="text-align:right;padding:5px 8px;color:var(--color-text-subtle)">${p.pid}</td>
            <td style="text-align:right;padding:5px 8px;font-variant-numeric:tabular-nums">${p.memory_mb || '?'}</td>
            <td style="text-align:center;padding:5px 8px">
              ${isProtected || isOllama
                ? '<span style="color:var(--color-text-subtle);font-size:10px">protected</span>'
                : `<button class="btn btn--danger btn--sm" style="padding:2px 8px;font-size:10px" data-kill-pid="${p.pid}" data-kill-name="${esc(p.name)}">Kill</button>`}
            </td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;

  // Bind kill buttons
  list.querySelectorAll('[data-kill-pid]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const pid = parseInt(btn.dataset.killPid, 10);
      const name = btn.dataset.killName;
      if (!confirm(`Kill "${name}" (PID ${pid})? This will close the application.`)) return;
      try {
        const res = await fetch('/api/system/kill-process', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pid }),
        }).then(r => r.json());
        if (res.ok) {
          btn.textContent = 'Killed';
          btn.disabled = true;
          setTimeout(loadGpuInfo, 2000); // Refresh after kill
        } else {
          alert(res.error || 'Failed to kill process');
        }
      } catch (_) {}
    });
  });
}

function toggleGpuProcs() {
  const list = $('gpu-proc-list');
  const btn = $('btn-toggle-gpu-procs');
  if (!list || !btn) return;
  const show = list.style.display === 'none';
  list.style.display = show ? '' : 'none';
  btn.textContent = show ? 'Hide' : 'Show';
  btn.setAttribute('aria-expanded', show ? 'true' : 'false');
}

init();
