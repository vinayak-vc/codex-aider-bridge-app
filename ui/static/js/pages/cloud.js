// pages/cloud.js — Cloud Sync page controller

import { apiPost } from '/static/js/core/api.js';
import { toast }    from '/static/js/core/toast.js';

const $ = id => document.getElementById(id);

// ── State ────────────────────────────────────────────────────────────────────

async function loadStatus() {
  try {
    const data = await fetch('/api/firebase/status').then(r => r.json());
    if (data.configured && data.authenticated) {
      showConnected(data);
    } else if (data.configured) {
      showSetup(); // Configured but not logged in
    } else {
      showNotConfigured();
    }
  } catch (_) {
    showNotConfigured();
  }
}

function showNotConfigured() {
  $('cloud-not-configured').style.display = '';
  $('cloud-setup').style.display = 'none';
  $('cloud-connected').style.display = 'none';
}

function showSetup() {
  $('cloud-not-configured').style.display = 'none';
  $('cloud-setup').style.display = '';
  $('cloud-connected').style.display = 'none';
}

function showConnected(data) {
  $('cloud-not-configured').style.display = 'none';
  $('cloud-setup').style.display = 'none';
  $('cloud-connected').style.display = '';
  $('cloud-user-email').textContent = data.email || '';
  $('cloud-project-id').textContent = data.project_id || '';
}

function showSetupMsg(msg, success) {
  const el = $('cloud-setup-msg');
  if (!el) return;
  el.style.display = '';
  el.style.background = success
    ? 'color-mix(in srgb, var(--color-success) 10%, transparent)'
    : 'color-mix(in srgb, var(--color-danger) 10%, transparent)';
  el.style.color = success ? 'var(--color-success)' : 'var(--color-danger)';
  el.textContent = msg;
}

// ── Setup Flow ───────────────────────────────────────────────────────────────

async function validateAndConnect() {
  const raw = ($('cloud-config-input')?.value || '').trim();
  if (!raw) { toast('Paste your Firebase config JSON.', 'warning'); return; }

  let config;
  try { config = JSON.parse(raw); }
  catch (e) { showSetupMsg('Invalid JSON: ' + e.message, false); return; }

  const btn = $('btn-validate-config');
  if (btn) { btn.disabled = true; btn.textContent = 'Validating...'; }

  try {
    // 1. Save config
    showSetupMsg('Saving config...', true);
    const saveRes = await apiPost('/api/firebase/setup', config);
    if (saveRes.error) { showSetupMsg(saveRes.error, false); return; }

    // 2. Test connection
    showSetupMsg('Testing Firestore connection...', true);
    const testRes = await apiPost('/api/firebase/test', {});
    if (!testRes.ok) { showSetupMsg(testRes.error || 'Connection failed', false); return; }

    // 3. Login
    showSetupMsg('Opening Google login... (check your browser)', true);
    const loginRes = await apiPost('/api/firebase/login', {});
    if (loginRes.error) { showSetupMsg(loginRes.error, false); return; }

    showSetupMsg('Connected as ' + loginRes.email, true);
    toast('Cloud sync configured!', 'success');
    setTimeout(loadStatus, 1500);
  } catch (err) {
    showSetupMsg(err.message || 'Setup failed', false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Validate & Connect'; }
  }
}

// ── Actions ──────────────────────────────────────────────────────────────────

async function syncNow() {
  const btn = $('btn-cloud-sync-now');
  if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
  try {
    const res = await apiPost('/api/sync/push', {});
    toast(res.ok ? 'Synced successfully!' : (res.error || 'Sync failed'), res.ok ? 'success' : 'error');
  } catch (err) {
    toast(err.message || 'Sync failed', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Sync Now'; }
  }
}

// ── Bind ─────────────────────────────────────────────────────────────────────

function init() {
  $('btn-cloud-refresh')?.addEventListener('click', loadStatus);
  $('btn-start-setup')?.addEventListener('click', showSetup);
  $('btn-cancel-setup')?.addEventListener('click', showNotConfigured);
  $('btn-validate-config')?.addEventListener('click', validateAndConnect);

  $('btn-cloud-logout')?.addEventListener('click', async () => {
    await fetch('/api/firebase/logout', { method: 'POST' });
    toast('Logged out', 'info');
    loadStatus();
  });

  $('btn-cloud-remove')?.addEventListener('click', async () => {
    if (!confirm('Remove Firebase config? This stops all cloud sync.')) return;
    await fetch('/api/firebase/clear', { method: 'POST' });
    toast('Firebase config removed', 'info');
    loadStatus();
  });

  $('btn-cloud-export')?.addEventListener('click', () => {
    window.open('/api/firebase/export-dashboard', '_blank');
  });

  $('btn-cloud-sync-now')?.addEventListener('click', syncNow);

  $('btn-cloud-export-data')?.addEventListener('click', async () => {
    try {
      const data = await fetch('/api/sync/export').then(r => r.json());
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'bridge_cloud_data_export.json';
      a.click();
      toast('Data exported', 'success');
    } catch (err) {
      toast(err.message || 'Export failed', 'error');
    }
  });

  $('btn-cloud-delete-data')?.addEventListener('click', async () => {
    if (!confirm('Delete ALL your cloud data? This cannot be undone.')) return;
    await apiPost('/api/sync/delete-account', {});
    toast('Cloud data deleted', 'info');
    loadStatus();
  });

  loadStatus();
}

init();
