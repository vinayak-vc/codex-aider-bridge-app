// pages/knowledge.js — Knowledge page controller
// Uses its own advanced markdown renderer (handles lists, blockquotes, HR)
// unlike the simpler core/markdown.js used by chat.

// ── Markdown renderer ─────────────────────────────────────────────────────────

function renderMarkdown(raw) {
  if (!raw) return '';

  const lines  = raw.split('\n');
  const out    = [];
  let inPre    = false;
  let inUl     = false;
  let inOl     = false;
  let preBuf   = [];

  const closeLists = () => {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  };

  const inlineEscape = s =>
    s.replace(/&/g, '&amp;')
     .replace(/</g, '&lt;')
     .replace(/>/g, '&gt;');

  const inlineFormat = s => {
    s = inlineEscape(s);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code blocks
    if (line.startsWith('```')) {
      if (!inPre) {
        closeLists();
        inPre = true;
        preBuf = [];
      } else {
        out.push(`<pre><code>${inlineEscape(preBuf.join('\n'))}</code></pre>`);
        inPre = false;
        preBuf = [];
      }
      continue;
    }
    if (inPre) { preBuf.push(line); continue; }

    // Horizontal rule
    if (/^---+$/.test(line.trim())) {
      closeLists(); out.push('<hr>'); continue;
    }

    // Headings
    const hm = line.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      closeLists();
      const lvl = hm[1].length;
      out.push(`<h${lvl}>${inlineFormat(hm[2])}</h${lvl}>`);
      continue;
    }

    // Blockquote
    if (line.startsWith('> ')) {
      closeLists();
      out.push(`<blockquote>${inlineFormat(line.slice(2))}</blockquote>`);
      continue;
    }

    // Unordered list
    const ulm = line.match(/^[-*+]\s+(.*)/);
    if (ulm) {
      if (!inUl) { if (inOl) { out.push('</ol>'); inOl = false; } out.push('<ul>'); inUl = true; }
      out.push(`<li>${inlineFormat(ulm[1])}</li>`);
      continue;
    }

    // Ordered list
    const olm = line.match(/^\d+\.\s+(.*)/);
    if (olm) {
      if (!inOl) { if (inUl) { out.push('</ul>'); inUl = false; } out.push('<ol>'); inOl = true; }
      out.push(`<li>${inlineFormat(olm[1])}</li>`);
      continue;
    }

    // Blank line → close lists / paragraph break
    if (line.trim() === '') {
      closeLists();
      out.push('<p></p>');
      continue;
    }

    // Paragraph line
    closeLists();
    out.push(`<p>${inlineFormat(line)}</p>`);
  }

  closeLists();
  if (inPre) out.push(`<pre><code>${inlineEscape(preBuf.join('\n'))}</code></pre>`);

  // Collapse consecutive empty paragraphs
  return out.join('\n').replace(/(<p><\/p>\n?){2,}/g, '<p></p>\n');
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

const $  = id => document.getElementById(id);
const esc = s  => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ── Tab switching ─────────────────────────────────────────────────────────────

function initTabs() {
  document.querySelectorAll('.knowledge-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.knowledge-tab').forEach(b => b.classList.remove('--active'));
      document.querySelectorAll('.knowledge-panel').forEach(p => p.classList.remove('--active'));
      btn.classList.add('--active');
      const panel = $(`panel-${btn.dataset.tab}`);
      if (panel) panel.classList.add('--active');
    });
  });
}

// ── Overview rendering ────────────────────────────────────────────────────────

function renderOverview(k) {
  const hasFiles = k.files && Object.keys(k.files).length > 0;

  if (!hasFiles && !(k.patterns?.length) && !(k.features_done?.length)) {
    $('overview-empty').style.display = '';
    $('overview-content').style.display = 'none';
    return;
  }

  $('overview-empty').style.display = 'none';
  $('overview-content').style.display = '';

  const proj = k.project || {};

  // Project badges
  const badgesEl = $('project-badges');
  if (badgesEl) {
    const badges = [];
    if (proj.language) badges.push(`<span class="badge badge--info">${esc(proj.language)}</span>`);
    if (proj.type)     badges.push(`<span class="badge badge--muted">${esc(proj.type)}</span>`);
    if (proj.scanned)  badges.push(`<span class="badge badge--success">scanned</span>`);
    badgesEl.innerHTML = badges.join(' ');
  }

  const summaryEl = $('project-summary');
  if (summaryEl) summaryEl.textContent = proj.summary || '';

  // Stats
  const fileCount    = Object.keys(k.files || {}).length;
  const featureCount = (k.features_done || []).length;
  const patternCount = (k.patterns || []).length;
  const docCount     = (k.docs || []).length;

  $('stat-files').textContent    = fileCount;
  $('stat-features').textContent = featureCount;
  $('stat-patterns').textContent = patternCount;
  $('stat-docs').textContent     = docCount;

  // File count badge on tab
  $('file-count-badge').textContent = fileCount;

  // Patterns
  const patternsEl = $('patterns-list');
  if (patternsEl && patternCount > 0) {
    patternsEl.innerHTML = (k.patterns || [])
      .map(p => `<span class="chip --pattern">${esc(p)}</span>`).join('');
    $('card-patterns').style.display = '';
  }

  // Features done
  const featuresEl = $('features-list');
  if (featuresEl && featureCount > 0) {
    featuresEl.innerHTML = (k.features_done || [])
      .map(f => `<span class="chip --feature">${esc(f)}</span>`).join('');
    $('card-features').style.display = '';
  }

  // Docs
  const docsEl = $('docs-list');
  if (docsEl && docCount > 0) {
    docsEl.innerHTML = (k.docs || []).map(d => {
      const path    = esc(typeof d === 'object' ? (d.path || d.file || '') : String(d));
      const summary = typeof d === 'object' && d.summary ? esc(d.summary) : '';
      return `<span class="chip --doc" title="${summary || path}">${path || '(unknown)'}</span>`;
    }).join('');
    $('card-docs').style.display = '';
  }

  // Clarifications
  const cList = $('clarifications-list');
  if (cList && (k.clarifications || []).length > 0) {
    cList.innerHTML = k.clarifications
      .map(c => `<li>${esc(c)}</li>`).join('');
    $('card-clarifications').style.display = '';
  }
}

// ── Understanding rendering ───────────────────────────────────────────────────

function renderUnderstanding(data, knowledge) {
  if (!data.exists || !data.content) {
    $('understanding-empty').style.display = '';
    $('understanding-content').style.display = 'none';
    return;
  }

  $('understanding-empty').style.display = 'none';
  $('understanding-content').style.display = '';

  const mdEl = $('understanding-md');
  if (mdEl) mdEl.innerHTML = renderMarkdown(data.content);

  const pathEl = $('understanding-path');
  if (pathEl) pathEl.textContent = data.path || '';

  // Confirmed badge
  const badge = $('understanding-confirmed-badge');
  if (badge) {
    const confirmed = knowledge?.understanding_confirmed;
    badge.textContent  = confirmed ? 'Confirmed' : 'Unconfirmed';
    badge.className    = `badge ${confirmed ? 'badge--success' : 'badge--warning'}`;
    badge.style.display = '';
  }
}

// ── File registry ─────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;
let _allFiles   = [];   // [{ path, role, task_type, last_modified }]
let _filtered   = [];
let _sortCol    = 'path';
let _sortDir    = 'asc';
let _page       = 0;

// File type icons (SVG inline, 14x14)
const _FILE_ICONS = {
  py:   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#3572A5" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"/></svg>',
  js:   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#f7df1e" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"/></svg>',
  ts:   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#3178c6" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"/></svg>',
  cs:   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#178600" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"/></svg>',
  css:  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#563d7c" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.53 16.122a3 3 0 0 0-5.78 1.128 2.25 2.25 0 0 1-2.4 2.245 4.5 4.5 0 0 0 8.4-2.245c0-.399-.078-.78-.22-1.128Zm0 0a15.998 15.998 0 0 0 3.388-1.62m-5.043-.025a15.994 15.994 0 0 1 1.622-3.395m3.42 3.42a15.995 15.995 0 0 0 4.764-4.648l3.876-5.814a1.151 1.151 0 0 0-1.597-1.597L14.146 6.32a15.996 15.996 0 0 0-4.649 4.763m3.42 3.42a6.776 6.776 0 0 0-3.42-3.42"/></svg>',
  html: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#e34c26" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582"/></svg>',
  json: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#f5a623" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5"/></svg>',
  md:   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#519aba" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"/></svg>',
};
const _FILE_ICON_DEFAULT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"/></svg>';

function _fileIcon(path) {
  const ext = (path || '').split('.').pop().toLowerCase();
  return _FILE_ICONS[ext] || _FILE_ICON_DEFAULT;
}

async function _openInVSCode(filePath) {
  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const repo = settings.repo_root || '';
    const res = await fetch('/api/vscode/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: filePath, repo_root: repo }),
    }).then(r => r.json());
    if (res.error) {
      alert(res.error);
    }
  } catch (_) {}
}

function buildFileRows(files) {
  _allFiles = Object.entries(files || {}).map(([path, meta]) => ({
    path,
    role:          meta.role          || '',
    task_type:     meta.task_type     || '',
    last_modified: meta.last_modified || '',
  }));
  applyFileFilter();
}

function applyFileFilter() {
  const q    = ($('file-search')?.value || '').toLowerCase();
  const type = $('file-type-filter')?.value || '';

  _filtered = _allFiles.filter(f => {
    const matchQ = !q || f.path.toLowerCase().includes(q) || f.role.toLowerCase().includes(q);
    const matchT = !type || f.task_type === type;
    return matchQ && matchT;
  });

  // Sort
  _filtered.sort((a, b) => {
    const av = (a[_sortCol] || '').toLowerCase();
    const bv = (b[_sortCol] || '').toLowerCase();
    if (av < bv) return _sortDir === 'asc' ? -1 : 1;
    if (av > bv) return _sortDir === 'asc' ?  1 : -1;
    return 0;
  });

  _page = 0;
  renderFilePage();
}

function renderFilePage() {
  const tbody  = $('file-table-body');
  const pagEl  = $('file-pagination');
  const label  = $('file-showing-label');
  const pgLbl  = $('file-page-label');
  if (!tbody) return;

  const start  = _page * PAGE_SIZE;
  const slice  = _filtered.slice(start, start + PAGE_SIZE);
  const pages  = Math.ceil(_filtered.length / PAGE_SIZE) || 1;

  if (_filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="file-table-empty">No files match the filter.</td></tr>`;
  } else {
    tbody.innerHTML = slice.map(f => `
      <tr class="file-row" data-path="${esc(f.path)}" title="Click to open in VS Code">
        <td><span class="file-icon">${_fileIcon(f.path)}</span><span class="file-path">${esc(f.path)}</span></td>
        <td><span class="file-role">${esc(f.role)}</span></td>
        <td><span class="file-type-badge">${esc(f.task_type)}</span></td>
        <td><span class="file-date">${esc(f.last_modified)}</span></td>
      </tr>
    `).join('');

    // Click to open in VS Code
    tbody.querySelectorAll('.file-row').forEach(row => {
      row.addEventListener('click', () => _openInVSCode(row.dataset.path));
    });
  }

  if (label) label.textContent = `${_filtered.length} file${_filtered.length !== 1 ? 's' : ''}`;
  if (pgLbl) pgLbl.textContent = `Page ${_page + 1} of ${pages}`;
  if (pagEl) pagEl.style.display = _filtered.length > PAGE_SIZE ? '' : 'none';

  // Prev/Next state
  const prev = $('btn-file-prev');
  const next = $('btn-file-next');
  if (prev) prev.disabled = _page === 0;
  if (next) next.disabled = _page >= pages - 1;
}

function initFileTable() {
  // Sort on header click
  document.querySelectorAll('.file-table th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (_sortCol === col) {
        _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        _sortCol = col;
        _sortDir = 'asc';
      }
      document.querySelectorAll('.file-table th').forEach(h => {
        h.classList.remove('--asc', '--desc');
      });
      th.classList.add(`--${_sortDir}`);
      renderFilePage();
    });
  });

  // Search + filter
  $('file-search')?.addEventListener('input',    applyFileFilter);
  $('file-type-filter')?.addEventListener('change', applyFileFilter);

  // Pagination
  $('btn-file-prev')?.addEventListener('click', () => { _page--; renderFilePage(); });
  $('btn-file-next')?.addEventListener('click', () => { _page++; renderFilePage(); });
}

// ── Tree view ────────────────────────────────────────────────────────────────

let _viewMode = 'table'; // 'table' | 'tree'

function _buildTree(files) {
  const root = {};
  for (const f of files) {
    const parts = f.path.replace(/\\/g, '/').split('/');
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      if (i === parts.length - 1) {
        // Leaf (file)
        node[name] = { __file: true, __data: f };
      } else {
        // Directory
        if (!node[name] || node[name].__file) node[name] = {};
        node = node[name];
      }
    }
  }
  return root;
}

function _renderTreeNode(name, node, depth, parentPath) {
  const fullPath = parentPath ? `${parentPath}/${name}` : name;
  const indent = depth * 16;

  if (node.__file) {
    const f = node.__data;
    const ext = (name.split('.').pop() || '').toLowerCase();
    return `<div class="tree-file" data-path="${esc(f.path)}" style="padding-left:${indent + 4}px" title="${esc(f.role || f.path)}">
      <span class="file-icon">${_fileIcon(f.path)}</span>
      <span class="tree-name">${esc(name)}</span>
      ${f.role ? `<span class="tree-role">${esc(f.role.slice(0, 40))}</span>` : ''}
    </div>`;
  }

  // Directory
  const children = Object.keys(node).sort((a, b) => {
    const aIsDir = !node[a].__file;
    const bIsDir = !node[b].__file;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.localeCompare(b);
  });

  const childHtml = children.map(k => _renderTreeNode(k, node[k], depth + 1, fullPath)).join('');

  return `<div class="tree-dir">
    <div class="tree-dir-label" style="padding-left:${indent}px" data-expanded="true">
      <svg class="tree-chevron" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="12" height="12">
        <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5"/>
      </svg>
      <span class="tree-dir-name">${esc(name)}</span>
    </div>
    <div class="tree-children">${childHtml}</div>
  </div>`;
}

function renderTree() {
  const el = $('file-tree');
  if (!el) return;

  const tree = _buildTree(_filtered.length ? _filtered : _allFiles);
  const keys = Object.keys(tree).sort((a, b) => {
    const aIsDir = !tree[a].__file;
    const bIsDir = !tree[b].__file;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.localeCompare(b);
  });

  el.innerHTML = keys.map(k => _renderTreeNode(k, tree[k], 0, '')).join('');

  // Toggle dir expand/collapse
  el.querySelectorAll('.tree-dir-label').forEach(label => {
    label.addEventListener('click', () => {
      const expanded = label.dataset.expanded === 'true';
      label.dataset.expanded = expanded ? 'false' : 'true';
      const children = label.nextElementSibling;
      if (children) children.style.display = expanded ? 'none' : '';
      label.querySelector('.tree-chevron')?.classList.toggle('--collapsed', expanded);
    });
  });

  // Click file to open in VS Code
  el.querySelectorAll('.tree-file').forEach(file => {
    file.addEventListener('click', () => _openInVSCode(file.dataset.path));
  });
}

function switchView(mode) {
  _viewMode = mode;
  const tableWrap = $('file-table-wrap') || document.querySelector('.file-table-wrap');
  const treeWrap = $('file-tree-wrap');
  const pagWrap = $('file-pagination');

  $('btn-view-table')?.classList.toggle('--active', mode === 'table');
  $('btn-view-tree')?.classList.toggle('--active', mode === 'tree');

  if (tableWrap) tableWrap.style.display = mode === 'table' ? '' : 'none';
  if (treeWrap) treeWrap.style.display = mode === 'tree' ? '' : 'none';
  if (pagWrap) pagWrap.style.display = mode === 'table' && _filtered.length > PAGE_SIZE ? '' : 'none';

  if (mode === 'tree') renderTree();
}

// ── Data loading ──────────────────────────────────────────────────────────────

let _hasKnowledge = false;

async function loadAll() {
  const [knowledge, understanding] = await Promise.allSettled([
    fetch('/api/reports/knowledge').then(r => r.json()),
    fetch('/api/reports/understanding').then(r => r.json()),
  ]);

  const k = knowledge.status === 'fulfilled' ? (knowledge.value || {}) : {};
  const u = understanding.status === 'fulfilled' ? (understanding.value || {}) : {};

  _hasKnowledge = Object.keys(k.files || {}).length > 0;
  _updateRefreshButton();
  _updateLastRefreshed(k);

  renderOverview(k);
  renderUnderstanding(u, k);
  buildFileRows(k.files);
}

// ── Refresh knowledge (re-scan project) ──────────────────────────────────────

async function refreshKnowledge() {
  const btn = $('btn-refresh-knowledge');
  const label = $('btn-refresh-label');
  if (btn) btn.disabled = true;
  if (label) label.textContent = 'Scanning...';

  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    const repo = settings?.repo_root || '';
    if (!repo) {
      alert('No project folder selected. Set one on the Run page first.');
      return;
    }

    const res = await fetch('/api/knowledge/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_root: repo }),
    }).then(r => r.json());

    if (res.error) {
      alert('Refresh failed: ' + res.error);
      return;
    }

    // Reload the page data
    await loadAll();
  } catch (err) {
    alert('Refresh failed: ' + (err.message || err));
  } finally {
    if (btn) btn.disabled = false;
    _updateRefreshButton();
  }
}

function _updateRefreshButton() {
  const label = $('btn-refresh-label');
  if (label) label.textContent = _hasKnowledge ? 'Refresh Knowledge' : 'Generate Knowledge';
}

function _updateLastRefreshed(k) {
  const el = $('knowledge-last-refreshed');
  if (!el) return;
  const ts = k?.project?.last_refreshed || k?.project?.last_updated;
  if (!ts) { el.textContent = 'Never'; return; }
  try {
    const d = new Date(ts);
    const diff = Math.floor((Date.now() - d) / 1000);
    if (diff < 60)    el.textContent = 'just now';
    else if (diff < 3600)  el.textContent = `${Math.floor(diff / 60)}m ago`;
    else if (diff < 86400) el.textContent = `${Math.floor(diff / 3600)}h ago`;
    else el.textContent = d.toLocaleDateString();
  } catch (_) {
    el.textContent = ts;
  }
}

// ── Auto-refresh ─────────────────────────────────────────────────────────────

let _autoRefreshTimer = null;
const _STORAGE_KEY = 'bridge_knowledge_refresh_minutes';

function _getAutoRefreshMinutes() {
  const saved = localStorage.getItem(_STORAGE_KEY);
  return saved !== null ? parseInt(saved, 10) : 10;
}

function _startAutoRefresh() {
  _stopAutoRefresh();
  const minutes = _getAutoRefreshMinutes();
  if (minutes > 0) {
    _autoRefreshTimer = setInterval(() => refreshKnowledge(), minutes * 60 * 1000);
  }
}

function _stopAutoRefresh() {
  if (_autoRefreshTimer) {
    clearInterval(_autoRefreshTimer);
    _autoRefreshTimer = null;
  }
}

// ── Entry point ───────────────────────────────────────────────────────────────

function init() {
  initTabs();
  initFileTable();

  // Reload from disk (quick)
  $('btn-reload-knowledge')?.addEventListener('click', loadAll);

  // Refresh knowledge (re-scan, slow)
  $('btn-refresh-knowledge')?.addEventListener('click', refreshKnowledge);

  // Auto-refresh interval control
  const autoSel = $('knowledge-auto-refresh');
  if (autoSel) {
    autoSel.value = String(_getAutoRefreshMinutes());
    autoSel.addEventListener('change', () => {
      localStorage.setItem(_STORAGE_KEY, autoSel.value);
      _startAutoRefresh();
    });
  }

  // View toggle (table/tree)
  $('btn-view-table')?.addEventListener('click', () => switchView('table'));
  $('btn-view-tree')?.addEventListener('click', () => switchView('tree'));

  // Project switch
  window.addEventListener('bridge:project-switched', () => loadAll());

  loadAll();
  _startAutoRefresh();
}

init();
