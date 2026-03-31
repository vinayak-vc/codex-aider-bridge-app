// theme.js — dark/light mode toggle with localStorage persistence

const STORAGE_KEY = 'bridge-theme';

export function initTheme() {
  const saved = localStorage.getItem(STORAGE_KEY) || 'dark';
  applyTheme(saved);
  document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);
}

export function toggleTheme() {
  const current = document.documentElement.dataset.theme || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  localStorage.setItem(STORAGE_KEY, next);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  btn.innerHTML = theme === 'dark' ? iconSun() : iconMoon();
}

function iconSun() {
  return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2m0 14v2M3 12H1m4.22-6.36L3.86 4.22M19.78 4.22l-1.36 1.42M20.14 19.78l-1.42-1.36M4.22 19.78l1.36-1.36M21 12h2M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"/>
  </svg>`;
}

function iconMoon() {
  return `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998z"/>
  </svg>`;
}
