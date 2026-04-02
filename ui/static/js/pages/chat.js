// pages/chat.js — Chat page controller

import { toast } from '/static/js/core/toast.js';

// ── State ─────────────────────────────────────────────────────────────────────

const history = [];   // [{role: 'user'|'assistant', content: string}]
let _streaming = false;

// ── DOM helpers ───────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

// ── Simple inline markdown renderer ──────────────────────────────────────────
// Converts streamed assistant text to safe HTML

function renderMarkdown(raw) {
  const lines = raw.split('\n');
  let html = '';
  let inFence = false;
  let fenceLang = '';
  let fenceLines = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code block
    const fenceMatch = line.match(/^```(\w*)$/);
    if (fenceMatch) {
      if (!inFence) {
        inFence = true;
        fenceLang = fenceMatch[1];
        fenceLines = [];
      } else {
        html += `<pre><code class="lang-${esc(fenceLang)}">${esc(fenceLines.join('\n'))}</code></pre>`;
        inFence = false;
        fenceLines = [];
      }
      continue;
    }
    if (inFence) { fenceLines.push(line); continue; }

    // Headings
    if (/^### /.test(line)) { html += `<p><strong>${inlineFormat(line.slice(4))}</strong></p>`; continue; }
    if (/^## /.test(line))  { html += `<p><strong>${inlineFormat(line.slice(3))}</strong></p>`; continue; }
    if (/^# /.test(line))   { html += `<p><strong>${inlineFormat(line.slice(2))}</strong></p>`; continue; }

    // List items
    if (/^[*\-] /.test(line)) { html += `<p style="margin-left:12px">• ${inlineFormat(line.slice(2))}</p>`; continue; }
    if (/^\d+\. /.test(line)) { html += `<p style="margin-left:12px">${inlineFormat(line)}</p>`; continue; }

    // Blank line
    if (!line.trim()) { html += '<br>'; continue; }

    // Normal paragraph
    html += `<p>${inlineFormat(line)}</p>`;
  }

  // Close unclosed fence
  if (inFence && fenceLines.length) {
    html += `<pre><code>${esc(fenceLines.join('\n'))}</code></pre>`;
  }

  return html;
}

function inlineFormat(s) {
  return esc(s)
    // **bold**
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // *italic*
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // `code`
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ── Message rendering ─────────────────────────────────────────────────────────

function appendUserMessage(text) {
  const msgs = $('chat-messages');
  const welcome = $('chat-welcome');
  if (welcome) welcome.style.display = 'none';

  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg--user';
  div.innerHTML = `
    <div class="chat-bubble">${esc(text)}</div>
    <span class="chat-meta">You</span>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function appendAssistantMessage() {
  // Returns { bubble, wrapper, setContent, finalize }
  const msgs = $('chat-messages');

  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg--assistant';

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble chat-bubble--streaming';
  bubble.innerHTML = '';

  const meta = document.createElement('span');
  meta.className = 'chat-meta';
  meta.textContent = 'Assistant';

  div.appendChild(bubble);
  div.appendChild(meta);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;

  let rawContent = '';

  return {
    append(token) {
      rawContent += token;
      bubble.innerHTML = renderMarkdown(rawContent);
      msgs.scrollTop = msgs.scrollHeight;
    },
    error(msg) {
      bubble.className = 'chat-bubble chat-bubble--error';
      bubble.innerHTML = esc(msg);
      bubble.classList.remove('chat-bubble--streaming');
    },
    finalize() {
      bubble.classList.remove('chat-bubble--streaming');
      return rawContent;
    },
  };
}

function appendErrorMessage(msg) {
  const msgs = $('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg--assistant';
  div.innerHTML = `<div class="chat-bubble chat-bubble--error">${esc(msg)}</div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

// ── Send message ──────────────────────────────────────────────────────────────

async function sendMessage(text) {
  text = text.trim();
  if (!text || _streaming) return;

  _streaming = true;
  setSendState(false);

  // Add to history and render user bubble
  history.push({ role: 'user', content: text });
  appendUserMessage(text);

  // Clear input
  const input = $('chat-input');
  if (input) { input.value = ''; autoResize(input); }

  // Create streaming bubble
  const msg = appendAssistantMessage();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history: history.slice(0, -1) }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
      msg.error(err.error || `Request failed (${resp.status})`);
      history.pop(); // remove failed user message
      _streaming = false;
      setSendState(true);
      return;
    }

    // Stream SSE tokens from response body
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const parts = buf.split('\n\n');
      buf = parts.pop(); // keep partial trailing chunk

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        try {
          const chunk = JSON.parse(raw);
          if (chunk.error) {
            msg.error(chunk.error);
            history.pop();
            _streaming = false;
            setSendState(true);
            return;
          }
          if (chunk.token) msg.append(chunk.token);
          if (chunk.done) break;
        } catch (_) {}
      }
    }

    const finalContent = msg.finalize();
    history.push({ role: 'assistant', content: finalContent });

  } catch (err) {
    msg.error(`Network error: ${err.message}`);
    history.pop();
  }

  _streaming = false;
  setSendState(true);
}

// ── Input auto-resize ─────────────────────────────────────────────────────────

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

// ── Send button state ─────────────────────────────────────────────────────────

function setSendState(enabled) {
  const btn = $('btn-send-chat');
  if (btn) btn.disabled = !enabled;
}

// ── Clear conversation ────────────────────────────────────────────────────────

function clearChat() {
  history.length = 0;
  _streaming = false;
  setSendState(true);

  const msgs = $('chat-messages');
  if (!msgs) return;

  // Remove all message divs (keep welcome)
  msgs.querySelectorAll('.chat-msg').forEach(el => el.remove());

  // Show welcome again
  const welcome = $('chat-welcome');
  if (welcome) welcome.style.display = '';
}

// ── Check model compatibility ─────────────────────────────────────────────────

async function checkModelCompat() {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const model = settings.aider_model || '';
    const chip  = $('chat-model-chip');
    if (chip) chip.textContent = model || 'no model configured';

    const warning     = $('chat-model-warning');
    const warnText    = $('chat-model-warning-text');
    const ollamaWarn  = $('chat-ollama-warning');
    const ollamaText  = $('chat-ollama-warning-text');

    if (!model.startsWith('ollama/')) {
      // Non-Ollama model — chat won't work
      if (warnText) {
        warnText.innerHTML =
          `Chat requires a local Ollama model. Your configured model <strong>${esc(model || '(none)')}</strong> ` +
          `requires an external API key — go to Run settings and switch to an <strong>ollama/…</strong> model first.`;
      }
      if (warning) warning.style.display = '';
      if (ollamaWarn) ollamaWarn.style.display = 'none';
      setSendState(false);
    } else {
      if (warning) warning.style.display = 'none';

      // Check Ollama is actually running and the model is pulled
      try {
        const status = await fetch('/api/chat/status').then(r => r.json());
        if (!status.ollama_running) {
          if (ollamaText) ollamaText.innerHTML =
            `Ollama is not running. Start it with <code>ollama serve</code>, then retry.`;
          if (ollamaWarn) ollamaWarn.style.display = '';
          setSendState(false);
        } else if (!status.model_available) {
          const bare = model.replace('ollama/', '');
          if (ollamaText) ollamaText.innerHTML =
            `Model <strong>${esc(bare)}</strong> is not pulled. Run <code>ollama pull ${esc(bare)}</code> to download it, then retry.`;
          if (ollamaWarn) ollamaWarn.style.display = '';
          setSendState(false);
        } else {
          if (ollamaWarn) ollamaWarn.style.display = 'none';
          setSendState(true);
        }
      } catch (_) {
        // Status check failed — don't block chat; Ollama might still work
        if (ollamaWarn) ollamaWarn.style.display = 'none';
        setSendState(true);
      }
    }

    // Update context status in limits banner
    const ctxStatus = $('chat-context-status');
    if (ctxStatus) {
      const repo = settings.repo_root || '';
      ctxStatus.textContent = repo
        ? `using repo: ${repo.replace(/\\/g, '/').split('/').pop()}`
        : 'no repo configured (set in Run tab)';
    }
  } catch (_) {}
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
  const input  = $('chat-input');
  const btnSend = $('btn-send-chat');

  // Auto-resize textarea
  input?.addEventListener('input', () => autoResize(input));

  // Enter to send, Shift+Enter for newline
  input?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input.value);
    }
  });

  // Send button
  btnSend?.addEventListener('click', () => sendMessage(input?.value || ''));

  // Clear button
  $('btn-clear-chat')?.addEventListener('click', () => {
    clearChat();
    toast('Conversation cleared.', 'info', 2000);
  });

  // Dismiss limits banner
  $('btn-limits-close')?.addEventListener('click', () => {
    const el = $('chat-limits');
    if (el) el.style.display = 'none';
  });

  // Suggestion chips
  document.querySelectorAll('.chat-suggestion').forEach(btn => {
    btn.addEventListener('click', () => {
      if (input) {
        input.value = btn.textContent;
        autoResize(input);
      }
      sendMessage(btn.textContent);
    });
  });

  // Ollama retry button
  $('btn-ollama-recheck')?.addEventListener('click', checkModelCompat);

  // Check model compat on load
  checkModelCompat();
}

init();
