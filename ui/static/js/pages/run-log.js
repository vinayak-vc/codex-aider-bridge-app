// run-log.js — Log parsing, rendering, and tag filtering for the Run page.
// Extracted from run.js for maintainability.

const $ = id => document.getElementById(id);
const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

let _lineCount = 0;
let _autoScroll = true;
let _logView = 'parsed';
let _tagCounts = { task: 0, review: 0, error: 0, warning: 0, bridge: 0, proxy: 0, aider: 0, info: 0 };
let _hiddenTags = new Set();

// ── Log Append ──────────────────────────────────────────────────────────────

export function appendLog(rawLine) {
  const terminal = $('log-terminal');
  if (terminal) {
    const span = document.createElement('span');
    let cls = '';
    if (/\|\s*(ERROR|CRITICAL)\s*\|/.test(rawLine)) cls = 'log-error';
    else if (/\|\s*WARNING\s*\|/.test(rawLine)) cls = 'log-warn';
    else if (/supervisor approved/.test(rawLine)) cls = 'log-ok';
    else if (/Bridge start|plan_ready/.test(rawLine)) cls = 'log-info';
    if (cls) span.className = cls;
    span.textContent = rawLine + '\n';
    terminal.appendChild(span);
    if (_logView === 'raw' && _autoScroll) terminal.scrollTop = terminal.scrollHeight;
  }

  _lineCount++;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = `${_lineCount} lines`;

  const parsed = parseLine(rawLine);
  if (parsed) appendParsedEvent(parsed);
}

export function clearLog() {
  _lineCount = 0;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = '0 lines';
  const terminal = $('log-terminal');
  if (terminal) terminal.innerHTML = '';
  const parsed = $('log-parsed');
  if (parsed) {
    parsed.querySelectorAll('.parsed-event').forEach(e => e.remove());
    const pe = $('log-parsed-empty');
    if (pe) pe.style.display = '';
  }
  _tagCounts = { task: 0, review: 0, error: 0, warning: 0, bridge: 0, proxy: 0, aider: 0, info: 0 };
  Object.keys(_tagCounts).forEach(tag => {
    const el = $(`tag-count-${tag}`);
    if (el) el.textContent = '0';
  });
}

export function switchLogView(view) {
  _logView = view;
  $('btn-log-parsed')?.classList.toggle('--active', view === 'parsed');
  $('btn-log-raw')?.classList.toggle('--active', view === 'raw');
  const p = $('log-parsed'); if (p) p.style.display = view === 'parsed' ? '' : 'none';
  const r = $('log-terminal'); if (r) r.style.display = view === 'raw' ? '' : 'none';
  const t = $('log-tag-bar'); if (t) t.style.display = view === 'parsed' ? '' : 'none';
}

export function toggleTag(tag) {
  if (_hiddenTags.has(tag)) _hiddenTags.delete(tag); else _hiddenTags.add(tag);
  document.querySelectorAll('.log-tag').forEach(btn =>
    btn.classList.toggle('--active', !_hiddenTags.has(btn.dataset.tag))
  );
  $('log-parsed')?.querySelectorAll('.parsed-event').forEach(ev => {
    ev.dataset.hidden = _hiddenTags.has(ev.dataset.tag) ? 'true' : 'false';
  });
}

export function setAutoScroll(val) { _autoScroll = val; }
export function getAutoScroll() { return _autoScroll; }

// ── Parsed Log ──────────────────────────────────────────────────────────────

function parseLine(rawLine) {
  const tsMatch = rawLine.match(/(\d{2}:\d{2}:\d{2})/);
  const time = tsMatch ? tsMatch[1] : '';
  const line = rawLine;
  if (line.trim().startsWith('{"_bridge_event"')) return null;

  const I = (c) => `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="${c}" width="12" height="12">`;
  const icons = {
    play: `${I('var(--color-accent)')}<path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"/></svg>`,
    check: `${I('var(--color-success)')}<path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>`,
    warn: `${I('var(--color-warning)')}<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>`,
    error: `${I('var(--color-danger)')}<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>`,
    info: `${I('var(--color-info)')}<path stroke-linecap="round" stroke-linejoin="round" d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/></svg>`,
  };

  let m;
  m = line.match(/Task\s+(\d+)\s*[—-]\s*attempt\s+(\d+)\/(\d+)\s*[—-]\s*files:\s*(.+)/);
  if (m) return { time, tag: 'task', cls: '--task', label: `Task ${m[1]}`, text: `Attempt ${m[2]}/${m[3]} — ${m[4].trim()}`, icon: icons.play };

  if (/supervisor approved|auto-approved/.test(line)) { m = line.match(/Task\s+(\d+)/); return { time, tag: 'review', cls: '--review', label: m ? `Task ${m[1]} Approved` : 'Approved', text: '', icon: icons.check }; }

  m = line.match(/supervisor requested rework[^:]*:\s*(.+)/);
  if (m) return { time, tag: 'review', cls: '--warning', label: 'Rework', text: m[1], icon: icons.warn };

  if (/\|\s*(ERROR|CRITICAL)\s*\|/.test(line)) return { time, tag: 'error', cls: '--error', label: 'Error', text: line.replace(/.*\|\s*(ERROR|CRITICAL)\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.error };
  if (/\|\s*WARNING\s*\|/.test(line)) return { time, tag: 'warning', cls: '--warning', label: 'Warning', text: line.replace(/.*\|\s*WARNING\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.warn };
  if (/Bridge start|Plan ready|Pre-flight|Loaded.*task|Project knowledge/.test(line)) return { time, tag: 'bridge', cls: '--info', label: 'Bridge', text: line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), icon: icons.info };
  if (/\[proxy\]/.test(line)) return { time, tag: 'proxy', cls: '--proxy', label: 'Proxy', text: line.replace(/.*\[proxy\]\s*/, ''), icon: icons.info };
  if (/\[bridge\]/.test(line)) return { time, tag: 'bridge', cls: '--info', label: 'Bridge', text: line.replace(/.*\[bridge\]\s*/, ''), icon: icons.info };
  if (/\[aider\]/.test(line)) return { time, tag: 'aider', cls: '--task', label: 'Aider', text: line.replace(/.*\[aider\]:\s*/, ''), icon: icons.play };
  if (/Git readiness|gitignore|Rollback point|undo all changes/.test(line)) return null;
  if (/\|\s*INFO\s*\|/.test(line)) return { time, tag: 'info', cls: '--info', label: '', text: line.replace(/.*\|\s*INFO\s*\|\s*\w+\s*\|\s*/, ''), icon: '' };
  return null;
}

function appendParsedEvent(p) {
  const container = $('log-parsed');
  if (!container) return;
  const emptyEl = $('log-parsed-empty');
  if (emptyEl) emptyEl.style.display = 'none';
  if (p.tag) {
    _tagCounts[p.tag] = (_tagCounts[p.tag] || 0) + 1;
    const el = $(`tag-count-${p.tag}`);
    if (el) el.textContent = _tagCounts[p.tag];
  }
  const div = document.createElement('div');
  div.className = `parsed-event ${p.cls}`;
  div.dataset.tag = p.tag || '';
  if (_hiddenTags.has(p.tag)) div.dataset.hidden = 'true';
  div.innerHTML = `<span class="parsed-time">${_esc(p.time)}</span><span class="parsed-icon">${p.icon}</span><span class="parsed-content">${p.label ? `<span class="parsed-label">${_esc(p.label)}</span>` : ''}${_esc(p.text)}</span><span class="parsed-tag">${_esc(p.tag)}</span>`;
  container.appendChild(div);
  if (_autoScroll) container.scrollTop = container.scrollHeight;
}
