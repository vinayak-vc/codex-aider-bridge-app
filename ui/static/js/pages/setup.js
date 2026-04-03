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

  runChecks();
}

init();
