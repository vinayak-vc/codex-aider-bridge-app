// pages/chat.js - Chat page controller

import { toast } from '/static/js/core/toast.js';
import { play } from '/static/js/core/sounds.js';

const history = [];
let _streaming = false;
let _projectKey = '';
let _selectedModel = '';
let _abortController = null;

const $ = id => document.getElementById(id);
const AUTO_SCROLL_THRESHOLD_PX = 48;

function isNearBottom(el) {
  if (!el) {
    return true;
  }
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= AUTO_SCROLL_THRESHOLD_PX;
}

function renderMarkdown(raw) {
  const lines = raw.split('\n');
  let html = '';
  let inFence = false;
  let fenceLang = '';
  let fenceLines = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
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
    if (inFence) {
      fenceLines.push(line);
      continue;
    }

    if (/^### /.test(line)) {
      html += `<p><strong>${inlineFormat(line.slice(4))}</strong></p>`;
      continue;
    }
    if (/^## /.test(line)) {
      html += `<p><strong>${inlineFormat(line.slice(3))}</strong></p>`;
      continue;
    }
    if (/^# /.test(line)) {
      html += `<p><strong>${inlineFormat(line.slice(2))}</strong></p>`;
      continue;
    }
    if (/^[*\-] /.test(line)) {
      html += `<p style="margin-left:12px">• ${inlineFormat(line.slice(2))}</p>`;
      continue;
    }
    if (/^\d+\. /.test(line)) {
      html += `<p style="margin-left:12px">${inlineFormat(line)}</p>`;
      continue;
    }
    if (!line.trim()) {
      html += '<br>';
      continue;
    }
    html += `<p>${inlineFormat(line)}</p>`;
  }

  if (inFence && fenceLines.length > 0) {
    html += `<pre><code>${esc(fenceLines.join('\n'))}</code></pre>`;
  }

  return html;
}

function inlineFormat(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function copyText(text) {
  const value = String(text || '');
  if (!value.trim()) {
    return false;
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (_) {}

  try {
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const success = document.execCommand('copy');
    document.body.removeChild(area);
    return success;
  } catch (_) {
    return false;
  }
}

function copyButtonMarkup() {
  return (
    '<button class="chat-copy-btn" type="button" aria-label="Copy response" title="Copy response">' +
      '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor" width="14" height="14" aria-hidden="true">' +
        '<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125H5.625A1.125 1.125 0 0 1 4.5 20.625V9.375c0-.621.504-1.125 1.125-1.125H9m6.75 9V6.75A2.25 2.25 0 0 0 13.5 4.5h-3A2.25 2.25 0 0 0 8.25 6.75v10.5m7.5 0h-7.5" />' +
      '</svg>' +
      '<span>Copy</span>' +
    '</button>'
  );
}

function updateHistoryBadge() {
  const badge = $('chat-history-count');
  if (!badge) {
    return;
  }
  if (history.length > 0) {
    badge.textContent = history.length + ' msgs';
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function setStreamingState(streaming) {
  _streaming = streaming;

  const sendButton = $('btn-send-chat');
  const stopButton = $('btn-stop-chat');
  const input = $('chat-input');

  if (sendButton && streaming) {
    sendButton.disabled = true;
  }
  if (stopButton) {
    stopButton.style.display = streaming ? '' : 'none';
    stopButton.disabled = !streaming;
  }
  if (input) {
    input.disabled = false;
  }
}

async function persistHistory() {
  if (!_projectKey) {
    return;
  }

  try {
    await fetch('/api/chat/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_root: _projectKey,
        messages: history.slice(-100),
      }),
    });
  } catch (_) {}
}

function clearRenderedMessages() {
  const msgs = $('chat-messages');
  if (!msgs) {
    return;
  }
  msgs.querySelectorAll('.chat-msg').forEach(el => el.remove());
}

function showWelcomeIfEmpty() {
  const welcome = $('chat-welcome');
  if (!welcome) {
    return;
  }
  welcome.style.display = history.length > 0 ? 'none' : '';
}

function renderUserMessage(text) {
  const msgs = $('chat-messages');
  if (!msgs) {
    return;
  }

  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg--user';
  div.innerHTML =
    '<div class="chat-bubble">' + esc(text) + '</div>' +
    '<span class="chat-meta">You</span>';
  msgs.appendChild(div);
}

function renderAssistantMessage(text, isStreaming) {
  const msgs = $('chat-messages');
  if (!msgs) {
    return;
  }

  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg--assistant';

  const bubble = document.createElement('div');
  bubble.className = isStreaming ? 'chat-bubble chat-bubble--streaming' : 'chat-bubble';
  bubble.innerHTML = renderMarkdown(text || '');

  const meta = document.createElement('span');
  meta.className = 'chat-meta';
  meta.textContent = isStreaming ? 'Assistant is typing…' : 'Assistant';

  div.appendChild(bubble);
  const footer = document.createElement('div');
  footer.className = 'chat-msg-footer';
  footer.appendChild(meta);

  if (String(text || '').trim()) {
    const actions = document.createElement('div');
    actions.className = 'chat-msg-actions';
    actions.innerHTML = copyButtonMarkup();

    const copyButton = actions.querySelector('.chat-copy-btn');
    copyButton?.addEventListener('click', async () => {
      const ok = await copyText(text);
      if (!ok) {
        toast('Could not copy response.', 'error', 1800);
        return;
      }
      const label = copyButton.querySelector('span');
      if (label) {
        label.textContent = 'Copied';
      }
      copyButton.classList.add('chat-copy-btn--copied');
      window.setTimeout(() => {
        copyButton.classList.remove('chat-copy-btn--copied');
        if (label) {
          label.textContent = 'Copy';
        }
      }, 1800);
    });

    footer.appendChild(actions);
  }

  div.appendChild(footer);
  msgs.appendChild(div);
}

function renderConversation(options = {}) {
  clearRenderedMessages();
  showWelcomeIfEmpty();

  const msgs = $('chat-messages');
  if (!msgs) {
    return;
  }

  const shouldStickToBottom = options.forceScroll === true || isNearBottom(msgs);

  for (let i = 0; i < history.length; i++) {
    const entry = history[i];
    const isLastStreaming = _streaming && i === history.length - 1 && entry.role === 'assistant';
    if (entry.role === 'user') {
      renderUserMessage(entry.content);
    } else if (entry.role === 'assistant') {
      renderAssistantMessage(entry.content, isLastStreaming);
    }
  }

  if (shouldStickToBottom) {
    msgs.scrollTop = msgs.scrollHeight;
  }
  updateHistoryBadge();
}

function appendUserMessage(text) {
  history.push({ role: 'user', content: text });
  renderConversation({ forceScroll: true });
}

function createAssistantStreamHandle() {
  history.push({ role: 'assistant', content: '' });
  renderConversation({ forceScroll: true });
  void persistHistory();

  return {
    append(token) {
      const last = history[history.length - 1];
      if (!last || last.role !== 'assistant') {
        return;
      }
      last.content += token;
      renderConversation();
      void persistHistory();
    },
    error(message) {
      const last = history[history.length - 1];
      if (!last || last.role !== 'assistant') {
        return;
      }
      last.content = message;
      renderConversation();
      void persistHistory();
    },
    finalize() {
      renderConversation();
      void persistHistory();
      return history.length > 0 ? history[history.length - 1].content : '';
    },
  };
}

function appendErrorMessage(msg) {
  history.push({ role: 'assistant', content: msg });
  renderConversation({ forceScroll: true });
  void persistHistory();
}

async function loadConversation(repoRoot) {
  const targetProject = String(repoRoot || '').trim();
  await stopStreaming(false);
  _projectKey = targetProject;

  history.length = 0;
  setStreamingState(false);
  clearRenderedMessages();

  try {
    const query = targetProject ? '?repo_root=' + encodeURIComponent(targetProject) : '';
    const data = await fetch('/api/chat/history' + query).then(r => r.json());
    const saved = Array.isArray(data.messages) ? data.messages : [];
    for (const entry of saved) {
      if (!entry || (entry.role !== 'user' && entry.role !== 'assistant')) {
        continue;
      }
      history.push({
        role: entry.role,
        content: String(entry.content || ''),
      });
    }
  } catch (_) {}

  renderConversation();
}

async function loadCurrentProjectConversation() {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    await loadConversation((settings.repo_root || '').trim());
  } catch (_) {
    await loadConversation('');
  }
}

async function stopStreaming(showToast) {
  if (!_streaming) {
    return;
  }

  if (_abortController) {
    _abortController.abort();
    _abortController = null;
  }

  const last = history.length > 0 ? history[history.length - 1] : null;
  if (last && last.role === 'assistant' && !String(last.content || '').trim()) {
    history.pop();
    renderConversation();
  }

  setStreamingState(false);
  await persistHistory();
  await checkModelCompat();

  if (showToast) {
    toast('Chat response stopped.', 'info', 2000);
  }
}

async function sendMessage(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed || _streaming) {
    return;
  }

  appendUserMessage(trimmed);
  await persistHistory();

  const input = $('chat-input');
  if (input) {
    input.value = '';
    autoResize(input);
  }

  const requestHistory = history.slice(0, -1);
  const msg = createAssistantStreamHandle();
  const requestBody = {
    message: trimmed,
    history: requestHistory,
    model: _selectedModel,
  };

  _abortController = new AbortController();
  setStreamingState(true);

  try {
    play('messageSent');
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
      signal: _abortController.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
      play('error');
      history.pop();
      history.pop();
      renderConversation();
      appendErrorMessage(err.error || `Request failed (${resp.status})`);
      setStreamingState(false);
      _abortController = null;
      await checkModelCompat();
      return;
    }

    if (!resp.body) {
      throw new Error('Empty response body');
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let doneStream = false;

    while (true) {
      const result = await reader.read();
      if (result.done) {
        break;
      }

      buf += decoder.decode(result.value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() || '';

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) {
          continue;
        }

        const raw = line.slice(5).trim();
        if (!raw) {
          continue;
        }

        try {
          const chunk = JSON.parse(raw);
          if (chunk.error) {
            play('error');
            history.pop();
            renderConversation();
            appendErrorMessage(chunk.error);
            doneStream = true;
            break;
          }
          if (chunk.token) {
            msg.append(chunk.token);
          }
          if (chunk.done) {
            doneStream = true;
            break;
          }
        } catch (_) {}
      }

      if (doneStream) {
        break;
      }
    }

    if (_streaming) {
      play('chatDone');
      msg.finalize();
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      msg.finalize();
    } else {
      play('error');
      history.pop();
      renderConversation();
      appendErrorMessage(`Network error: ${err.message}`);
    }
  }

  _abortController = null;
  setStreamingState(false);
  await checkModelCompat();
}

function autoResize(el) {
  if (!el) {
    return;
  }
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

async function clearChat() {
  await stopStreaming(false);
  history.length = 0;
  renderConversation();

  if (_projectKey) {
    try {
      await fetch('/api/chat/history?repo_root=' + encodeURIComponent(_projectKey), {
        method: 'DELETE',
      });
    } catch (_) {}
  }

  toast('Started a new chat.', 'info', 2000);
}

async function checkModelCompat() {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const model = settings.aider_model || '';
    const chip = $('chat-model-chip');
    if (chip) {
      chip.textContent = model || 'no model configured';
    }

    const warning = $('chat-model-warning');
    const warnText = $('chat-model-warning-text');
    const ollamaWarn = $('chat-ollama-warning');
    const ollamaText = $('chat-ollama-warning-text');

    if (!model.startsWith('ollama/')) {
      if (warnText) {
        warnText.innerHTML =
          `Chat requires a local Ollama model. Your configured model <strong>${esc(model || '(none)')}</strong> ` +
          `requires an external API key — go to Run settings and switch to an <strong>ollama/…</strong> model first.`;
      }
      if (warning) {
        warning.style.display = '';
      }
      if (ollamaWarn) {
        ollamaWarn.style.display = 'none';
      }
      setStreamingState(false);
    } else {
      if (warning) {
        warning.style.display = 'none';
      }

      try {
        const status = await fetch('/api/chat/status').then(r => r.json());
        if (!status.ollama_running) {
          if (ollamaText) {
            ollamaText.innerHTML = 'Ollama is not running. Start it with <code>ollama serve</code>, then retry.';
          }
          if (ollamaWarn) {
            ollamaWarn.style.display = '';
          }
          if (!_streaming) {
            const sendButton = $('btn-send-chat');
            if (sendButton) {
              sendButton.disabled = true;
            }
          }
        } else if (!status.model_available) {
          const bare = model.replace('ollama/', '');
          if (ollamaText) {
            ollamaText.innerHTML =
              `Model <strong>${esc(bare)}</strong> is not pulled. Run <code>ollama pull ${esc(bare)}</code> to download it, then retry.`;
          }
          if (ollamaWarn) {
            ollamaWarn.style.display = '';
          }
          if (!_streaming) {
            const sendButton = $('btn-send-chat');
            if (sendButton) {
              sendButton.disabled = true;
            }
          }
        } else {
          if (ollamaWarn) {
            ollamaWarn.style.display = 'none';
          }
          if (!_streaming) {
            const sendButton = $('btn-send-chat');
            if (sendButton) {
              sendButton.disabled = false;
            }
          }
        }
      } catch (_) {
        if (ollamaWarn) {
          ollamaWarn.style.display = 'none';
        }
        if (!_streaming) {
          const sendButton = $('btn-send-chat');
          if (sendButton) {
            sendButton.disabled = false;
          }
        }
      }
    }

    const ctxStatus = $('chat-context-status');
    if (ctxStatus) {
      const repo = settings.repo_root || '';
      ctxStatus.textContent = repo
        ? `using repo: ${repo.replace(/\\/g, '/').split('/').pop()}`
        : 'no repo configured (set in Run tab)';
    }
  } catch (_) {}
}

async function init() {
  const input = $('chat-input');
  const btnSend = $('btn-send-chat');

  if (input) {
    input.addEventListener('input', () => autoResize(input));
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void sendMessage(input.value);
      }
    });
  }

  if (btnSend) {
    btnSend.addEventListener('click', () => void sendMessage(input ? input.value : ''));
  }

  $('btn-clear-chat')?.addEventListener('click', () => {
    void clearChat();
  });

  $('btn-stop-chat')?.addEventListener('click', () => {
    void stopStreaming(true);
  });

  $('btn-limits-close')?.addEventListener('click', () => {
    const el = $('chat-limits');
    if (el) {
      el.style.display = 'none';
    }
  });

  document.querySelectorAll('.chat-suggestion').forEach(btn => {
    btn.addEventListener('click', () => {
      const text = btn.textContent || '';
      if (input) {
        input.value = text;
        autoResize(input);
      }
      void sendMessage(text);
    });
  });

  $('btn-ollama-recheck')?.addEventListener('click', () => {
    void checkModelCompat();
  });

  $('chat-model-select')?.addEventListener('change', e => {
    _selectedModel = e.target.value;
  });

  window.addEventListener('bridge:project-switched', e => {
    const path = e.detail && e.detail.path ? String(e.detail.path) : '';
    void loadConversation(path).then(() => checkModelCompat());
  });

  await loadCurrentProjectConversation();
  await loadModels();
  await checkModelCompat();
  setStreamingState(false);
}

async function loadModels() {
  const sel = $('chat-model-select');
  if (!sel) {
    return;
  }

  try {
    const status = await fetch('/api/chat/status').then(r => r.json());
    const models = status.available_models || [];
    const settings = await fetch('/api/settings').then(r => r.json());
    const configured = (settings.aider_model || '').replace('ollama/', '');

    if (models.length === 0) {
      sel.innerHTML = '<option value="">No Ollama models found</option>';
      return;
    }

    sel.innerHTML = models.map(function(m) {
      const selected = (m === _selectedModel || m.startsWith(configured.split(':')[0])) ? ' selected' : '';
      return '<option value="' + esc(m) + '"' + selected + '>' + esc(m) + '</option>';
    }).join('');

    _selectedModel = sel.value;
  } catch (_) {
    sel.innerHTML = '<option value="">Could not load models</option>';
  }
}

init();
