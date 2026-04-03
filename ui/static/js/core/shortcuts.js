// core/shortcuts.js — Global keyboard shortcuts

// ── Shortcut map ──────────────────────────────────────────────────────────────
// Chord: press g, then within 1.5 s press the second key → navigate

const NAV_CHORDS = {
  d: '/dashboard',
  r: '/run',
  k: '/knowledge',
  h: '/history',
  t: '/tokens',
  s: '/setup',
  c: '/chat',
  a: '/relay',
  g: '/git',
};

const CHORD_TIMEOUT_MS = 1500;

// ── Help overlay HTML ─────────────────────────────────────────────────────────

const OVERLAY_ID = 'kb-help-overlay';

const SHORTCUTS = [
  { keys: ['g', 'd'],     desc: 'Go to Dashboard'  },
  { keys: ['g', 'r'],     desc: 'Go to Run'         },
  { keys: ['g', 'k'],     desc: 'Go to Knowledge'   },
  { keys: ['g', 'h'],     desc: 'Go to History'     },
  { keys: ['g', 't'],     desc: 'Go to Tokens'      },
  { keys: ['g', 's'],     desc: 'Go to Setup'       },
  { keys: ['g', 'c'],     desc: 'Go to Chat'        },
  { keys: ['g', 'a'],     desc: 'Go to AI Relay'    },
  { keys: ['g', 'g'],     desc: 'Go to Git'         },
  { keys: ['H'],          desc: 'Help for this page' },
  { keys: ['Ctrl', '↵'],  desc: 'Launch run (Run page)' },
  { keys: ['Esc'],        desc: 'Close modal / overlay' },
  { keys: ['?'],          desc: 'Show this help'    },
];

function buildOverlay() {
  if (document.getElementById(OVERLAY_ID)) return;

  const rows = SHORTCUTS.map(s => {
    const kbd = s.keys.map(k => `<kbd>${k}</kbd>`).join(' + ');
    return `
      <tr>
        <td style="padding:6px 16px 6px 0;white-space:nowrap">${kbd}</td>
        <td style="padding:6px 0;color:var(--color-text-muted);font-size:var(--font-size-sm)">${s.desc}</td>
      </tr>`;
  }).join('');

  const el = document.createElement('div');
  el.id        = OVERLAY_ID;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'Keyboard shortcuts');
  el.innerHTML = `
    <div class="kb-help-backdrop"></div>
    <div class="kb-help-panel">
      <div class="kb-help-header">
        <span class="kb-help-title">Keyboard shortcuts</span>
        <button class="btn btn--secondary btn--sm" id="kb-help-close" aria-label="Close">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
               stroke-width="2" stroke="currentColor" width="14" height="14">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <table class="kb-help-table"><tbody>${rows}</tbody></table>
    </div>`;

  document.body.appendChild(el);

  el.querySelector('#kb-help-close')?.addEventListener('click', hideHelp);
  el.querySelector('.kb-help-backdrop')?.addEventListener('click', hideHelp);
}

function showHelp() {
  buildOverlay();
  const el = document.getElementById(OVERLAY_ID);
  if (el) el.style.display = '';
}

function hideHelp() {
  const el = document.getElementById(OVERLAY_ID);
  if (el) el.style.display = 'none';
}

function isHelpVisible() {
  const el = document.getElementById(OVERLAY_ID);
  return el && el.style.display !== 'none';
}

// ── Chord state ───────────────────────────────────────────────────────────────

let _waitingForChord = false;
let _chordTimer      = null;

function startChord() {
  _waitingForChord = true;
  clearTimeout(_chordTimer);
  _chordTimer = setTimeout(() => { _waitingForChord = false; }, CHORD_TIMEOUT_MS);
}

function cancelChord() {
  _waitingForChord = false;
  clearTimeout(_chordTimer);
}

// ── Event listener ────────────────────────────────────────────────────────────

function onKeyDown(e) {
  // Ignore when typing in inputs / textareas / contenteditable
  const tag = (e.target?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select' ||
      e.target?.isContentEditable) {
    return;
  }

  // Ignore modifier-heavy combos (let browser / page handlers take those)
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const key = e.key;

  // Esc — close help overlay (page-level Esc handlers deal with their own modals)
  if (key === 'Escape') {
    if (isHelpVisible()) { e.preventDefault(); hideHelp(); }
    cancelChord();
    return;
  }

  // H — open page help
  if (key === 'H' && !_waitingForChord) {
    e.preventDefault();
    document.getElementById('page-help-btn')?.click();
    return;
  }

  // ? — show help
  if (key === '?' && !_waitingForChord) {
    e.preventDefault();
    isHelpVisible() ? hideHelp() : showHelp();
    return;
  }

  // g — start chord
  if (key === 'g' && !_waitingForChord) {
    e.preventDefault();
    startChord();
    return;
  }

  // Second key of g+X chord
  if (_waitingForChord) {
    cancelChord();
    const dest = NAV_CHORDS[key];
    if (dest) {
      e.preventDefault();
      // Don't navigate if already on that page
      if (window.location.pathname !== dest) {
        window.location.href = dest;
      }
    }
    return;
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

export function initShortcuts() {
  document.addEventListener('keydown', onKeyDown);
  buildOverlay();           // build DOM early so first ? press is instant
  document.getElementById(OVERLAY_ID).style.display = 'none';
}
