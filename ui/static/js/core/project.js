// core/project.js — Project switcher (top bar)

const $ = id => document.getElementById(id);

// ── State ─────────────────────────────────────────────────────────────────────

let _projects    = [];   // [{name, path}, …]
let _currentPath = '';
let _open        = false;

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function fetchProjects() {
  try {
    _projects = await fetch('/api/projects').then(r => r.json());
  } catch (_) { _projects = []; }
}

async function fetchCurrentPath() {
  try {
    const s  = await fetch('/api/settings').then(r => r.json());
    _currentPath = s.repo_root || '';
    // Auto-register if not yet in list
    if (_currentPath && !_projects.some(p => p.path === _currentPath)) {
      await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: _currentPath }),
      });
      await fetchProjects();
    }
  } catch (_) {}
}

// ── Render ────────────────────────────────────────────────────────────────────

function nameLabel(path) {
  return path ? (path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || path) : '';
}

function renderProjectName() {
  const el = $('project-name');
  if (!el) return;
  if (_currentPath) {
    const proj = _projects.find(p => p.path === _currentPath);
    el.textContent = (proj?.name) || nameLabel(_currentPath) || _currentPath;
    el.classList.remove('project-name--empty');
  } else {
    el.textContent = 'No project selected';
    el.classList.add('project-name--empty');
  }
}

function renderDropdown() {
  const list = $('project-dropdown-list');
  if (!list) return;

  if (_projects.length === 0) {
    list.innerHTML = `<p class="project-dropdown-empty">No saved projects yet.<br>Add one below.</p>`;
    return;
  }

  list.innerHTML = _projects.map(p => {
    const active  = p.path === _currentPath;
    const display = p.name || nameLabel(p.path);
    return `
      <div class="project-dropdown-item ${active ? 'project-dropdown-item--active' : ''}"
           role="option" aria-selected="${active}" data-path="${esc(p.path)}">
        <span class="project-dropdown-item-icon" aria-hidden="true">
          ${active
            ? `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5"/></svg>`
            : `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 0 1 4.5 9.75h15A2.25 2.25 0 0 1 21.75 12v.75m-8.69-6.44-2.12-2.12a1.5 1.5 0 0 0-1.061-.44H4.5A2.25 2.25 0 0 0 2.25 6v8.25m19.5 0v.243a2.25 2.25 0 0 1-2.25 2.25H4.5a2.25 2.25 0 0 1-2.25-2.25V6.75"/></svg>`
          }
        </span>
        <div class="project-dropdown-item-body">
          <span class="project-dropdown-item-name">${esc(display)}</span>
          <span class="project-dropdown-item-path">${esc(p.path)}</span>
        </div>
        <button class="project-dropdown-item-remove" data-path="${esc(p.path)}"
                title="Remove from list" aria-label="Remove ${esc(display)}">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
               stroke-width="2" stroke="currentColor" width="12" height="12">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>`;
  }).join('');

  // Bind item clicks
  list.querySelectorAll('.project-dropdown-item').forEach(el => {
    el.addEventListener('click', async e => {
      // Don't fire if remove button clicked
      if (e.target.closest('.project-dropdown-item-remove')) return;
      const path = el.dataset.path;
      if (path && path !== _currentPath) await switchProject(path);
      closeDropdown();
    });
  });

  // Bind remove buttons
  list.querySelectorAll('.project-dropdown-item-remove').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const path = btn.dataset.path;
      await fetch(`/api/projects/${encodeURIComponent(path)}`, { method: 'DELETE' });
      await fetchProjects();
      renderDropdown();
      renderProjectName();
    });
  });
}

// ── Switch project ────────────────────────────────────────────────────────────

async function switchProject(path) {
  try {
    await fetch('/api/projects/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    _currentPath = path;
    renderProjectName();
    renderDropdown();
    refreshGitStatus();
    window.dispatchEvent(new CustomEvent('bridge:project-switched', {
      detail: { path: path },
    }));
  } catch (_) {}
}

// ── Add project ───────────────────────────────────────────────────────────────

async function addProject() {
  const path = prompt('Enter the full path to your project repository:');
  if (!path || !path.trim()) return;
  const name = prompt('Project name (leave blank to use folder name):', '');
  await fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: path.trim(), name: (name || '').trim() }),
  });
  // Auto-switch to newly added project
  await switchProject(path.trim());
  await fetchProjects();
  renderDropdown();
  closeDropdown();
}

// ── Dropdown open/close ───────────────────────────────────────────────────────

function openDropdown() {
  const dd  = $('project-dropdown');
  const btn = $('project-switcher');
  if (!dd) return;
  renderDropdown();
  dd.hidden = false;
  btn?.setAttribute('aria-expanded', 'true');
  _open = true;
}

function closeDropdown() {
  const dd  = $('project-dropdown');
  const btn = $('project-switcher');
  if (!dd) return;
  dd.hidden = true;
  btn?.setAttribute('aria-expanded', 'false');
  _open = false;
}

function toggleDropdown() {
  _open ? closeDropdown() : openDropdown();
}

// ── Utility ───────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Git branch chip ──────────────────────────────────────────────────────────

async function refreshGitStatus() {
  const branchName = $('sb-branch-name');
  const gitDot = $('sb-git-dot');
  const gitLabel = $('sb-git-label');

  if (!_currentPath) {
    if (branchName) branchName.textContent = '—';
    if (gitLabel) gitLabel.textContent = '—';
    return;
  }

  try {
    const data = await fetch(`/api/git/status?repo_root=${encodeURIComponent(_currentPath)}`).then(r => r.json());
    if (data.error) {
      if (branchName) branchName.textContent = '—';
      if (gitLabel) gitLabel.textContent = 'Not a repo';
      return;
    }

    if (branchName) branchName.textContent = data.branch || 'detached';
    if (gitDot) gitDot.dataset.clean = data.is_clean ? 'true' : 'false';
    if (gitLabel) {
      if (data.is_clean) {
        gitLabel.textContent = 'Clean';
      } else {
        const parts = [];
        if (data.staged) parts.push(`${data.staged} staged`);
        if (data.unstaged) parts.push(`${data.unstaged} modified`);
        if (data.untracked) parts.push(`${data.untracked} new`);
        gitLabel.textContent = parts.join(', ') || 'Dirty';
      }
    }
  } catch (_) {
    if (branchName) branchName.textContent = '—';
  }
}

// ── Status bar run status ────────────────────────────────────────────────────

async function refreshRunStatus() {
  try {
    const data = await fetch('/api/run/status').then(r => r.json());
    const statusEl = $('sb-run-status');
    const tasksEl  = $('sb-tasks');
    const runDot   = $('sb-run-dot');
    const navChip  = $('nav-status-chip');
    const navLabel = $('nav-status-label');

    const status = data.status || 'idle';
    const labels = {
      idle: 'Idle', running: 'Running', success: 'Done',
      failure: 'Failed', stopped: 'Stopped', paused: 'Paused',
      waiting_review: 'Review',
    };
    const label = labels[status] || status;

    if (statusEl) statusEl.innerHTML =
      `<span class="status-dot" id="sb-run-dot" data-status="${status}"></span> ${label}`;
    if (tasksEl) tasksEl.textContent =
      `${data.completed_tasks || 0}/${data.total_tasks || 0} tasks`;

    // Also update the nav sidebar chip
    if (navChip) navChip.dataset.status = status;
    if (navLabel) navLabel.textContent = label;
  } catch (_) {}
}

// Refresh status bar periodically
setInterval(refreshRunStatus, 3000);

// ── Global model selector ─────────────────────────────────────────────────────

async function loadModels() {
  const sel = $('global-model-select');
  if (!sel) return;

  try {
    const [status, settings] = await Promise.all([
      fetch('/api/chat/status').then(r => r.json()).catch(() => ({})),
      fetch('/api/settings').then(r => r.json()).catch(() => ({})),
    ]);

    const models   = status.available_models || [];
    const current  = settings.aider_model || '';

    // Strip "ollama/" prefix for display; raw value is the full model string
    sel.innerHTML = models.length
      ? models.map(m => {
          const label = m.replace(/^ollama\//, '');
          const sel_  = m === current || label === current ? ' selected' : '';
          return `<option value="${esc(m)}"${sel_}>${esc(label)}</option>`;
        }).join('')
      : `<option value="${esc(current)}">${esc(current || 'No models found')}</option>`;

    // If current setting not in list but non-empty, prepend it
    if (current && !models.includes(current)) {
      const label = current.replace(/^ollama\//, '');
      sel.insertAdjacentHTML('afterbegin',
        `<option value="${esc(current)}" selected>${esc(label)}</option>`);
    }
  } catch (_) {}
}

async function saveModel(value) {
  if (!value) return;
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    settings.aider_model = value;
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  } catch (_) {}
}

// ── Init ──────────────────────────────────────────────────────────────────────

export async function initProjectBar() {
  await fetchProjects();
  await fetchCurrentPath();
  renderProjectName();
  loadModels();
  refreshGitStatus();
  refreshRunStatus();

  // Switcher button toggles dropdown
  $('project-switcher')?.addEventListener('click', e => {
    e.stopPropagation();
    toggleDropdown();
  });

  // Add project button
  $('btn-project-add')?.addEventListener('click', e => {
    e.stopPropagation();
    addProject();
  });

  // Click outside → close
  document.addEventListener('click', e => {
    if (_open && !e.target.closest('#project-bar')) closeDropdown();
  });

  // Esc → close
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && _open) closeDropdown();
  });

  // Global model select — save on change
  $('global-model-select')?.addEventListener('change', e => {
    saveModel(e.target.value);
  });
}
