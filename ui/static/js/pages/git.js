// pages/git.js — Git page controller

const $ = id => document.getElementById(id);

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function fmtAge(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    const diff = Math.floor((Date.now() - d) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString();
  } catch (_) { return ts; }
}

// ── State ────────────────────────────────────────────────────────────────────

let _repo = '';

async function getRepo() {
  if (_repo) return _repo;
  try {
    const s = await fetch('/api/settings').then(r => r.json());
    _repo = s.repo_root || '';
  } catch (_) {}
  return _repo;
}

// ── Load everything ──────────────────────────────────────────────────────────

async function loadAll() {
  const repo = await getRepo();
  if (!repo) return;
  await Promise.all([loadBranches(repo), loadStatus(repo), loadCommits(repo), loadChanged(repo)]);
}

async function loadBranches(repo) {
  try {
    const data = await fetch(`/api/git/branches?repo_root=${encodeURIComponent(repo)}`).then(r => r.json());
    const sel = $('git-branch-select');
    if (sel && data.branches) {
      sel.innerHTML = data.branches.map(b =>
        `<option value="${esc(b)}" ${b === data.current ? 'selected' : ''}>${esc(b)}</option>`
      ).join('');
    }
  } catch (_) {}
}

async function loadStatus(repo) {
  try {
    const data = await fetch(`/api/git/status?repo_root=${encodeURIComponent(repo)}`).then(r => r.json());
    const line = $('git-status-line');
    if (!line || data.error) return;

    if (data.is_clean) {
      line.innerHTML = '<span style="color:var(--color-success)">&#x2713; Clean working tree</span>';
    } else {
      const parts = [];
      if (data.staged) parts.push(`<span style="color:var(--color-success)">${data.staged} staged</span>`);
      if (data.unstaged) parts.push(`<span style="color:var(--color-warning)">${data.unstaged} modified</span>`);
      if (data.untracked) parts.push(`<span style="color:var(--color-text-muted)">${data.untracked} untracked</span>`);
      line.innerHTML = parts.join(' &middot; ');
    }
  } catch (_) {}
}

async function loadCommits(repo) {
  try {
    const data = await fetch(`/api/git/log?repo_root=${encodeURIComponent(repo)}&limit=30`).then(r => r.json());
    const list = $('git-commit-list');
    const countEl = $('git-commit-count');
    if (!list) return;

    const commits = data.commits || [];
    if (countEl) countEl.textContent = commits.length;

    if (!commits.length) {
      list.innerHTML = '<div class="text-subtle" style="font-size:var(--font-size-xs);padding:12px 0;text-align:center">No commits found.</div>';
      return;
    }

    list.innerHTML = commits.map(c => {
      const badge = c.is_bridge_task
        ? '<span class="badge badge--accent" style="font-size:9px;margin-left:4px">bridge</span>'
        : '';
      return `
        <div class="git-commit-row" data-sha="${esc(c.sha)}" title="${esc(c.message)}">
          <code class="git-sha">${esc(c.short_sha)}</code>
          <span class="git-commit-msg">${esc(c.message.slice(0, 70))}</span>
          ${badge}
          <span class="git-commit-age">${fmtAge(c.timestamp)}</span>
        </div>
      `;
    }).join('');

    list.querySelectorAll('.git-commit-row').forEach(row => {
      row.addEventListener('click', () => {
        list.querySelectorAll('.git-commit-row').forEach(r => r.classList.remove('--active'));
        row.classList.add('--active');
        showDiff(row.dataset.sha, `Commit ${row.querySelector('.git-sha')?.textContent || ''}`);
      });
    });
  } catch (_) {}
}

let _changedFiles = [];
let _changedView = 'list'; // 'list' | 'tree'

async function loadChanged(repo) {
  try {
    const data = await fetch(`/api/git/diff?repo_root=${encodeURIComponent(repo)}`).then(r => r.json());
    const wrap = $('git-changed-wrap');
    const countEl = $('git-changed-count');
    if (!wrap) return;

    _changedFiles = data.files || [];
    if (countEl) countEl.textContent = _changedFiles.length;

    if (!_changedFiles.length) {
      wrap.style.display = 'none';
      return;
    }

    wrap.style.display = '';
    _renderChangedView();
  } catch (_) {}
}

function _renderChangedView() {
  if (_changedView === 'tree') {
    _renderChangedTree();
  } else {
    _renderChangedList();
  }
  const listEl = $('git-changed-list');
  const treeEl = $('git-changed-tree');
  if (listEl) listEl.style.display = _changedView === 'list' ? '' : 'none';
  if (treeEl) treeEl.style.display = _changedView === 'tree' ? '' : 'none';
}

function _renderChangedList() {
  const clist = $('git-changed-list');
  if (!clist) return;

  clist.innerHTML = _changedFiles.map(f =>
    `<div class="git-changed-file" data-file="${esc(f.path)}">
       <span class="git-file-path" title="${esc(f.path)}">${esc(f.path)}</span>
       <span class="git-file-stat">+${f.insertions} -${f.deletions}</span>
       <span class="git-file-actions">
         <button class="git-file-action-btn" data-action="ignore" data-path="${esc(f.path)}" title="Add to .gitignore">
           <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="12" height="12">
             <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636"/>
           </svg>
         </button>
       </span>
     </div>`
  ).join('');

  _bindChangedFileEvents(clist);
}

function _renderChangedTree() {
  const treeEl = $('git-changed-tree');
  if (!treeEl) return;

  // Build tree structure
  const root = {};
  for (const f of _changedFiles) {
    const parts = f.path.replace(/\\/g, '/').split('/');
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      if (i === parts.length - 1) {
        node[name] = { __file: true, __data: f };
      } else {
        if (!node[name] || node[name].__file) node[name] = {};
        node = node[name];
      }
    }
  }

  treeEl.innerHTML = _renderTreeNodes(root, 0, '');
  _bindTreeEvents(treeEl);
}

function _renderTreeNodes(node, depth, parentPath) {
  const keys = Object.keys(node).sort((a, b) => {
    const aDir = !node[a].__file;
    const bDir = !node[b].__file;
    if (aDir !== bDir) return aDir ? -1 : 1;
    return a.localeCompare(b);
  });

  return keys.map(name => {
    const child = node[name];
    const fullPath = parentPath ? `${parentPath}/${name}` : name;
    const indent = depth * 14;

    if (child.__file) {
      const f = child.__data;
      return `<div class="git-tree-file" data-file="${esc(f.path)}" style="padding-left:${indent + 16}px" title="${esc(f.path)}">
        ${esc(name)}
        <span class="git-tree-stat">+${f.insertions} -${f.deletions}</span>
        <button class="git-file-action-btn" data-action="ignore" data-path="${esc(f.path)}" title="Add to .gitignore" style="margin-left:4px">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="11" height="11">
            <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636"/>
          </svg>
        </button>
      </div>`;
    }

    const children = _renderTreeNodes(child, depth + 1, fullPath);
    return `<div class="git-tree-dir">
      <div class="git-tree-dir-label" style="padding-left:${indent}px" data-expanded="true" data-dir="${esc(fullPath)}">
        <svg class="git-tree-chevron" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="11" height="11">
          <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5"/>
        </svg>
        ${esc(name)}/
        <button class="git-file-action-btn" data-action="ignore-dir" data-path="${esc(fullPath + '/')}" title="Add folder to .gitignore" style="margin-left:auto">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" width="11" height="11">
            <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636"/>
          </svg>
        </button>
      </div>
      <div class="git-tree-children">${children}</div>
    </div>`;
  }).join('');
}

function _bindChangedFileEvents(container) {
  container.querySelectorAll('.git-changed-file').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.closest('.git-file-action-btn')) return; // don't trigger diff on action click
      showDiff(null, el.dataset.file, el.dataset.file);
    });
  });
  container.querySelectorAll('[data-action="ignore"]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      _addToGitignore(btn.dataset.path);
    });
  });
}

function _bindTreeEvents(container) {
  container.querySelectorAll('.git-tree-dir-label').forEach(label => {
    label.addEventListener('click', e => {
      if (e.target.closest('.git-file-action-btn')) return;
      const expanded = label.dataset.expanded === 'true';
      label.dataset.expanded = expanded ? 'false' : 'true';
      const children = label.nextElementSibling;
      if (children) children.style.display = expanded ? 'none' : '';
      label.querySelector('.git-tree-chevron')?.classList.toggle('--collapsed', expanded);
    });
  });
  container.querySelectorAll('.git-tree-file').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.closest('.git-file-action-btn')) return;
      showDiff(null, el.dataset.file, el.dataset.file);
    });
  });
  container.querySelectorAll('[data-action="ignore"], [data-action="ignore-dir"]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      _addToGitignore(btn.dataset.path);
    });
  });
}

async function _addToGitignore(pattern) {
  if (!confirm(`Add "${pattern}" to .gitignore?`)) return;
  const repo = await getRepo();
  if (!repo) return;
  try {
    await fetch('/api/git/gitignore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_root: repo, pattern }),
    });
    await loadAll(); // Refresh to show updated status
  } catch (_) {}
}

// ── Diff viewer ──────────────────────────────────────────────────────────────

async function showDiff(sha, title, filePath) {
  const repo = await getRepo();
  if (!repo) return;

  const card = $('git-diff-card');
  const pre = $('git-diff-pre');
  const titleEl = $('git-diff-title');
  if (!card || !pre) return;

  if (titleEl) titleEl.textContent = title || 'Diff';

  let url = `/api/git/diff?repo_root=${encodeURIComponent(repo)}`;
  if (sha) url += `&sha=${encodeURIComponent(sha)}`;
  else if (filePath) url += `&file=${encodeURIComponent(filePath)}`;

  try {
    const data = await fetch(url).then(r => r.json());
    pre.textContent = data.diff || '(no changes)';
    card.style.display = '';
  } catch (_) {
    pre.textContent = 'Failed to load diff.';
    card.style.display = '';
  }
}

// ── Controls ─────────────────────────────────────────────────────────────────

function bindControls() {
  $('btn-git-refresh')?.addEventListener('click', loadAll);

  $('btn-git-close-diff')?.addEventListener('click', () => {
    const card = $('git-diff-card');
    if (card) card.style.display = 'none';
    document.querySelectorAll('.git-commit-row').forEach(r => r.classList.remove('--active'));
  });

  $('git-branch-select')?.addEventListener('change', async e => {
    const branch = e.target.value;
    const repo = await getRepo();
    if (!repo || !branch) return;
    try {
      const res = await fetch('/api/git/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_root: repo, branch }),
      }).then(r => r.json());
      if (res.ok) {
        await loadAll();
      } else {
        alert(res.error || 'Failed to switch branch');
        await loadBranches(repo);
      }
    } catch (_) {}
  });

  $('btn-git-new-branch')?.addEventListener('click', async () => {
    const name = prompt('New branch name:');
    if (!name || !name.trim()) return;
    const repo = await getRepo();
    if (!repo) return;
    try {
      const res = await fetch('/api/git/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_root: repo, branch: name.trim(), create: true }),
      }).then(r => r.json());
      if (res.ok) {
        await loadAll();
      } else {
        alert(res.error || 'Failed to create branch');
      }
    } catch (_) {}
  });

  // Changed files view toggle
  $('btn-changed-list')?.addEventListener('click', () => {
    _changedView = 'list';
    $('btn-changed-list')?.classList.add('--active');
    $('btn-changed-tree')?.classList.remove('--active');
    _renderChangedView();
  });
  $('btn-changed-tree')?.addEventListener('click', () => {
    _changedView = 'tree';
    $('btn-changed-tree')?.classList.add('--active');
    $('btn-changed-list')?.classList.remove('--active');
    _renderChangedView();
  });

  window.addEventListener('bridge:project-switched', e => {
    _repo = e?.detail?.path || '';
    loadAll();
  });
}

// ── Entry point ──────────────────────────────────────────────────────────────

function init() {
  bindControls();
  loadAll();
}

init();
