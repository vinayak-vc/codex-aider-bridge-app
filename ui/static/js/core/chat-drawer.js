import { toast } from '/static/js/core/toast.js';

const STORE_KEY = 'bridge_chat_drawer_open';
let currentRepoRoot = '';
let currentMessagesJson = '';
let isGenerating = false;
let pollTimer = null;

const $ = id => document.getElementById(id);
const AUTO_SCROLL_THRESHOLD_PX = 48;

function isNearBottom(el) {
  if (!el) {
    return true;
  }
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= AUTO_SCROLL_THRESHOLD_PX;
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
    '<button class="chat-drawer__copy-btn" type="button" aria-label="Copy response" title="Copy response">' +
      '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor" width="14" height="14" aria-hidden="true">' +
        '<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125H5.625A1.125 1.125 0 0 1 4.5 20.625V9.375c0-.621.504-1.125 1.125-1.125H9m6.75 9V6.75A2.25 2.25 0 0 0 13.5 4.5h-3A2.25 2.25 0 0 0 8.25 6.75v10.5m7.5 0h-7.5" />' +
      '</svg>' +
      '<span>Copy</span>' +
    '</button>'
  );
}

function inlineFormat(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function renderMarkdown(raw) {
  const lines = String(raw || '').split('\n');
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

function projectLabel(path) {
  const clean = String(path || '').replace(/\\/g, '/');
  if (!clean) {
    return 'No project selected';
  }
  const parts = clean.split('/').filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : clean;
}

function setDrawerOpen(open) {
  const drawer = $('chat-drawer');
  const toggle = $('chat-drawer-toggle');
  if (!drawer || !toggle) {
    return;
  }

  drawer.classList.toggle('chat-drawer--open', open);
  toggle.hidden = open;
  localStorage.setItem(STORE_KEY, open ? '1' : '0');
}

function autoResize(el) {
  if (!el) {
    return;
  }
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function setGeneratingState(active) {
  isGenerating = active;
  const stopButton = $('chat-drawer-stop');
  const sendButton = $('chat-drawer-send');
  if (stopButton) {
    stopButton.style.display = active ? '' : 'none';
    stopButton.disabled = !active;
  }
  if (sendButton) {
    sendButton.disabled = active;
  }
}

function renderMessages(messages, options = {}) {
  const container = $('chat-drawer-messages');
  const empty = $('chat-drawer-empty');
  if (!container || !empty) {
    return;
  }

  const shouldStickToBottom = options.forceScroll === true || isNearBottom(container);

  container.querySelectorAll('.chat-drawer__msg').forEach(el => el.remove());
  empty.style.display = messages.length > 0 ? 'none' : '';

  for (let i = 0; i < messages.length; i++) {
    const entry = messages[i];
    if (!entry || (entry.role !== 'user' && entry.role !== 'assistant')) {
      continue;
    }

    const wrapper = document.createElement('div');
    wrapper.className = `chat-drawer__msg chat-drawer__msg--${entry.role}`;

    const bubble = document.createElement('div');
    bubble.className = 'chat-drawer__bubble';
    if (isGenerating && i === messages.length - 1 && entry.role === 'assistant') {
      bubble.classList.add('chat-drawer__bubble--streaming');
    }
    bubble.innerHTML = entry.role === 'assistant' ? renderMarkdown(entry.content || '') : esc(entry.content || '');

    const meta = document.createElement('span');
    meta.className = 'chat-drawer__meta';
    meta.textContent = entry.role === 'assistant'
      ? (isGenerating && i === messages.length - 1 ? 'Assistant is typing…' : 'Assistant')
      : 'You';

    wrapper.appendChild(bubble);
    const footer = document.createElement('div');
    footer.className = 'chat-drawer__footer-row';
    footer.appendChild(meta);

    if (entry.role === 'assistant' && String(entry.content || '').trim()) {
      const actions = document.createElement('div');
      actions.className = 'chat-drawer__actions-row';
      actions.innerHTML = copyButtonMarkup();

      const copyButton = actions.querySelector('.chat-drawer__copy-btn');
      copyButton?.addEventListener('click', async () => {
        const ok = await copyText(entry.content || '');
        if (!ok) {
          toast('Could not copy response.', 'error', 1800);
          return;
        }
        const label = copyButton.querySelector('span');
        if (label) {
          label.textContent = 'Copied';
        }
        copyButton.classList.add('chat-drawer__copy-btn--copied');
        window.setTimeout(() => {
          copyButton.classList.remove('chat-drawer__copy-btn--copied');
          if (label) {
            label.textContent = 'Copy';
          }
        }, 1800);
      });

      footer.appendChild(actions);
    }

    wrapper.appendChild(footer);
    container.appendChild(wrapper);
  }

  if (shouldStickToBottom) {
    container.scrollTop = container.scrollHeight;
  }
}

function currentHistory() {
  try {
    const state = JSON.parse(currentMessagesJson || '[]');
    return Array.isArray(state) ? state : [];
  } catch (_) {
    return [];
  }
}

function updateStatus(text) {
  const status = $('chat-drawer-status');
  if (!status) {
    return;
  }
  if (text) {
    status.textContent = text;
    status.style.display = '';
  } else {
    status.textContent = '';
    status.style.display = 'none';
  }
}

async function refreshState() {
  if (!currentRepoRoot) {
    renderMessages([], { forceScroll: true });
    updateStatus('Select a project to chat with its saved context.');
    setGeneratingState(false);
    return;
  }

  try {
    const data = await fetch('/api/chat/state?repo_root=' + encodeURIComponent(currentRepoRoot)).then(r => r.json());
    const messages = Array.isArray(data.messages) ? data.messages : [];
    const nextJson = JSON.stringify(messages);
    currentMessagesJson = nextJson;
    setGeneratingState(Boolean(data.is_generating));
    renderMessages(messages);
    updateStatus(data.error || '');
  } catch (_) {}
}

async function loadCurrentProject() {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    currentRepoRoot = String(settings.repo_root || '').trim();
  } catch (_) {
    currentRepoRoot = '';
  }

  const project = $('chat-drawer-project');
  if (project) {
    project.textContent = projectLabel(currentRepoRoot);
  }
  await refreshState();
}

async function sendMessage() {
  const input = $('chat-drawer-input');
  if (!input || isGenerating) {
    return;
  }

  const message = String(input.value || '').trim();
  if (!message) {
    return;
  }
  if (!currentRepoRoot) {
    toast('Select a project first.', 'error', 2500);
    return;
  }

  const history = currentHistory();
  const resp = await fetch('/api/chat/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      repo_root: currentRepoRoot,
      message: message,
      history: history,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    toast(err.error || 'Could not start chat.', 'error', 3000);
    return;
  }

  input.value = '';
  autoResize(input);
  await refreshState();
}

async function stopMessage() {
  if (!currentRepoRoot || !isGenerating) {
    return;
  }

  await fetch('/api/chat/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_root: currentRepoRoot }),
  }).catch(() => {});

  await refreshState();
}

async function clearChat() {
  if (!currentRepoRoot) {
    return;
  }

  await stopMessage();
  await fetch('/api/chat/history?repo_root=' + encodeURIComponent(currentRepoRoot), {
    method: 'DELETE',
  }).catch(() => {});

  currentMessagesJson = '[]';
  await refreshState();
  toast('Started a new chat.', 'info', 2000);
}

function startPolling() {
  if (pollTimer !== null) {
    return;
  }
  pollTimer = window.setInterval(() => {
    void refreshState();
  }, 1200);
}

export async function initChatDrawer() {
  $('chat-drawer-toggle')?.addEventListener('click', () => {
    setDrawerOpen(true);
  });
  $('chat-drawer-close')?.addEventListener('click', () => {
    setDrawerOpen(false);
  });
  $('chat-drawer-new')?.addEventListener('click', () => {
    void clearChat();
  });
  $('chat-drawer-stop')?.addEventListener('click', () => {
    void stopMessage();
  });
  $('chat-drawer-send')?.addEventListener('click', () => {
    void sendMessage();
  });

  const input = $('chat-drawer-input');
  if (input) {
    input.addEventListener('input', () => autoResize(input));
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void sendMessage();
      }
    });
  }

  window.addEventListener('bridge:project-switched', e => {
    const path = e.detail && e.detail.path ? String(e.detail.path) : '';
    currentRepoRoot = path;
    const project = $('chat-drawer-project');
    if (project) {
      project.textContent = projectLabel(currentRepoRoot);
    }
    void refreshState();
  });

  const shouldOpen = localStorage.getItem(STORE_KEY) === '1';
  setDrawerOpen(shouldOpen);
  await loadCurrentProject();
  startPolling();
}
