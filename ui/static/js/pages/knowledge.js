// pages/knowledge.js — Knowledge page controller

// ── Minimal markdown renderer (~80 lines, no library) ─────────────────────────

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
      <tr>
        <td><span class="file-path">${esc(f.path)}</span></td>
        <td><span class="file-role">${esc(f.role)}</span></td>
        <td><span class="file-type-badge">${esc(f.task_type)}</span></td>
        <td><span class="file-date">${esc(f.last_modified)}</span></td>
      </tr>
    `).join('');
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

// ── Data loading ──────────────────────────────────────────────────────────────

async function loadAll() {
  const [knowledge, understanding] = await Promise.allSettled([
    fetch('/api/reports/knowledge').then(r => r.json()),
    fetch('/api/reports/understanding').then(r => r.json()),
  ]);

  const k = knowledge.status === 'fulfilled' ? (knowledge.value || {}) : {};
  const u = understanding.status === 'fulfilled' ? (understanding.value || {}) : {};

  renderOverview(k);
  renderUnderstanding(u, k);
  buildFileRows(k.files);
}

// ── Entry point ───────────────────────────────────────────────────────────────

function init() {
  initTabs();
  initFileTable();

  $('btn-refresh-knowledge')?.addEventListener('click', loadAll);

  loadAll();
}

init();
