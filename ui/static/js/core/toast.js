// toast.js — lightweight notification toasts

const ICONS = {
  success: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/></svg>`,
  error:   `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/></svg>`,
  warning: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0zm-9 3.75h.008v.008H12v-.008z"/></svg>`,
  info:    `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zm-9-3.75h.008v.008H12V8.25z"/></svg>`,
};

const CLOSE_ICON = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg>`;

let _container = null;

function getContainer() {
  if (!_container) {
    _container = document.getElementById('toast-container');
    if (!_container) {
      _container = document.createElement('div');
      _container.id = 'toast-container';
      document.body.appendChild(_container);
    }
  }
  return _container;
}

/**
 * Show a toast notification.
 * @param {string} message - Main message text
 * @param {'success'|'error'|'warning'|'info'} [type='info']
 * @param {number} [duration=4000] - Auto-dismiss after ms (0 = no auto-dismiss)
 * @param {string} [title] - Optional bold title
 */
export function toast(message, type = 'info', duration = 4000, title = '') {
  const container = getContainer();
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.innerHTML = `
    <div class="toast-icon">${ICONS[type] || ICONS.info}</div>
    <div class="toast-body">
      ${title ? `<div class="toast-title">${escHtml(title)}</div>` : ''}
      <div class="toast-msg">${escHtml(message)}</div>
    </div>
    <button class="toast-close" aria-label="Dismiss">${CLOSE_ICON}</button>
  `;

  const dismiss = () => {
    el.style.opacity = '0';
    el.style.transform = 'translateX(20px)';
    el.style.transition = 'opacity .15s, transform .15s';
    setTimeout(() => el.remove(), 160);
  };

  el.querySelector('.toast-close').addEventListener('click', dismiss);
  container.appendChild(el);

  if (duration > 0) setTimeout(dismiss, duration);
  return dismiss;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
