// core/action-log.js — Records every UI action for debugging
//
// Captures: clicks, state changes, API calls, SSE events, errors
// Stored in sessionStorage, downloadable as JSON

const MAX_ENTRIES = 500;
const STORAGE_KEY = 'bridge_action_log';

let _entries = [];

function _now() {
  return new Date().toISOString();
}

function _getState() {
  return {
    page: location.pathname,
    supervisor: document.querySelector('input[name="supervisor"]:checked')?.value || '?',
    repo: document.getElementById('f-repo-root')?.value || '?',
    model: document.getElementById('f-aider-model')?.value || '?',
    activeTab: document.querySelector('.run-tabs .tab.--active')?.dataset?.tab || '?',
  };
}

export function logAction(action, detail = {}) {
  const entry = {
    ts: _now(),
    action,
    state: _getState(),
    ...detail,
  };
  _entries.push(entry);
  if (_entries.length > MAX_ENTRIES) _entries.shift();

  // Persist to sessionStorage
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(_entries));
  } catch (_) {}
}

export function logClick(element, label) {
  logAction('click', {
    element: label || element?.id || element?.textContent?.trim()?.slice(0, 30) || '?',
    tag: element?.tagName || '?',
  });
}

export function logAPI(method, url, status, error) {
  logAction('api', { method, url, status, error: error || undefined });
}

export function logSSE(eventType, data) {
  logAction('sse', { event: eventType, data: typeof data === 'object' ? JSON.stringify(data).slice(0, 200) : String(data).slice(0, 200) });
}

export function logError(message, source) {
  logAction('error', { message: String(message).slice(0, 300), source });
}

export function logStateChange(key, value) {
  logAction('state_change', { key, value: String(value).slice(0, 100) });
}

export function getLog() {
  return [..._entries];
}

export function downloadLog() {
  const blob = new Blob([JSON.stringify(_entries, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `bridge_action_log_${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function clearLog() {
  _entries = [];
  try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) {}
}

// Load from sessionStorage on module init
try {
  const saved = sessionStorage.getItem(STORAGE_KEY);
  if (saved) _entries = JSON.parse(saved);
} catch (_) {}

// Auto-capture all button clicks
document.addEventListener('click', e => {
  const btn = e.target.closest('button, a.nav-item, .tab, .supervisor-option label, .model-preset');
  if (btn) logClick(btn);
}, true);

// Auto-capture unhandled errors
window.addEventListener('error', e => {
  logError(e.message, e.filename + ':' + e.lineno);
});

// Expose globally for console access
window.__bridgeActionLog = { getLog, downloadLog, clearLog, logAction };
