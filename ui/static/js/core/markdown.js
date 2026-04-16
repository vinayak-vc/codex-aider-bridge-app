// core/markdown.js — Simple markdown renderer shared by chat, knowledge, drawer.

export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function inlineFormat(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

export function renderMarkdown(raw) {
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

    if (/^### /.test(line)) { html += `<p><strong>${inlineFormat(line.slice(4))}</strong></p>`; continue; }
    if (/^## /.test(line)) { html += `<p><strong>${inlineFormat(line.slice(3))}</strong></p>`; continue; }
    if (/^# /.test(line)) { html += `<p><strong>${inlineFormat(line.slice(2))}</strong></p>`; continue; }
    if (/^[*\-] /.test(line)) { html += `<p style="margin-left:12px">• ${inlineFormat(line.slice(2))}</p>`; continue; }
    if (/^\d+\. /.test(line)) { html += `<p style="margin-left:12px">${inlineFormat(line)}</p>`; continue; }
    if (!line.trim()) { html += '<br>'; continue; }
    html += `<p>${inlineFormat(line)}</p>`;
  }

  if (inFence && fenceLines.length > 0) {
    html += `<pre><code>${esc(fenceLines.join('\n'))}</code></pre>`;
  }

  return html;
}
