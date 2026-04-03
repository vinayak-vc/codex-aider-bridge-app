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

async function loadChanged(repo) {
  try {
    const data = await fetch(`/api/git/diff?repo_root=${encodeURIComponent(repo)}`).then(r => r.json());
    const wrap = $('git-changed-wrap');
    const clist = $('git-changed-list');
    const countEl = $('git-changed-count');
    if (!wrap || !clist) return;

    const files = data.files || [];
    if (countEl) countEl.textContent = files.length;

    if (!files.length) {
      wrap.style.display = 'none';
      return;
    }

    clist.innerHTML = files.map(f =>
      `<div class="git-changed-file" data-file="${esc(f.path)}" title="Click to view diff">
         <span class="git-file-path">${esc(f.path)}</span>
         <span class="git-file-stat">+${f.insertions} -${f.deletions}</span>
       </div>`
    ).join('');
    wrap.style.display = '';

    clist.querySelectorAll('.git-changed-file').forEach(el => {
      el.addEventListener('click', () => {
        showDiff(null, el.dataset.file, el.dataset.file);
      });
    });
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
