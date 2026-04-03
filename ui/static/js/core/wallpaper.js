// core/wallpaper.js — Per-page space wallpapers, refreshed daily

const CLIENT_ID  = '15MDKvUVv4HMJ3DzyeRIwxCFvEB70QeNcKzOCX_Puf0';
const DATE_KEY   = 'wp_date';
const PREFIX     = 'wp_';

// Space query per page — varied so each page feels different
const PAGE_QUERY = {
  dashboard : 'milky+way+galaxy',
  run       : 'nebula+colorful',
  chat      : 'cosmos+stars+night',
  knowledge : 'astronomy+deep+space',
  history   : 'universe+spiral+galaxy',
  tokens    : 'space+station+orbit+earth',
  setup     : 'satellite+earth+from+space',
  relay     : 'deep+space+stars+dark',
};

// ── Day-change guard ──────────────────────────────────────────────────────────

function todayStr() {
  return new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"
}

function evictIfNewDay() {
  const stored = localStorage.getItem(DATE_KEY);
  const today  = todayStr();
  if (stored === today) return;

  // New day — clear all cached wallpaper URLs
  const toRemove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(PREFIX) && k !== DATE_KEY) toRemove.push(k);
  }
  toRemove.forEach(k => localStorage.removeItem(k));
  localStorage.setItem(DATE_KEY, today);
}

// ── Fetch from Unsplash ───────────────────────────────────────────────────────

async function fetchWallpaperUrl(page) {
  const query = PAGE_QUERY[page] || 'space+galaxy+dark';
  const url   = `https://api.unsplash.com/photos/random?query=${query}&orientation=landscape&client_id=${CLIENT_ID}`;
  try {
    const res  = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    // Use raw URL with explicit 4K params (w=3840, q=85, fm=jpg, fit=crop)
    const raw = data?.urls?.raw;
    if (!raw) return null;
    const sep = raw.includes('?') ? '&' : '?';
    return `${raw}${sep}w=3840&q=85&fm=jpg&fit=crop&crop=entropy`;
  } catch (_) {
    return null;
  }
}

// ── Apply to DOM ──────────────────────────────────────────────────────────────

function applyWallpaper(imageUrl) {
  const el = document.getElementById('page-wallpaper');
  if (!el || !imageUrl) return;
  // Pre-load so there's no flash — swap only once loaded
  const img = new Image();
  img.onload = () => {
    el.style.backgroundImage = `url("${imageUrl}")`;
    el.style.opacity = '1';     // fade in (CSS transition handles the animation)
    el.dataset.loaded = 'true';
  };
  img.src = imageUrl;
}

// ── Public init ───────────────────────────────────────────────────────────────

export async function initWallpaper() {
  const page   = document.body.dataset.page || 'dashboard';
  const cacheKey = PREFIX + page;

  evictIfNewDay();

  const cached = localStorage.getItem(cacheKey);
  if (cached) {
    applyWallpaper(cached);
    return;
  }

  // Fetch fresh URL, cache it for the rest of the day, then apply
  const imageUrl = await fetchWallpaperUrl(page);
  if (imageUrl) {
    localStorage.setItem(cacheKey, imageUrl);
    applyWallpaper(imageUrl);
  }
}
