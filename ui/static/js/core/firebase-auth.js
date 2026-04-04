// core/firebase-auth.js — Firebase Auth state management

const $ = id => document.getElementById(id);

let _authState = { logged_in: false, email: null, display_name: null, sync_enabled: false, configured: false };

export async function checkAuthStatus() {
  try {
    _authState = await fetch('/api/auth/status').then(r => r.json());
  } catch (_) {
    _authState = { logged_in: false, configured: false };
  }
  updateAuthUI();
  return _authState;
}

export async function login() {
  try {
    const data = await fetch('/api/auth/login', { method: 'POST' }).then(r => r.json());
    if (data.error) {
      alert(data.error);
      return data;
    }
    await checkAuthStatus();
    return data;
  } catch (err) {
    alert('Login failed: ' + (err.message || err));
    return { error: err.message };
  }
}

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  _authState = { logged_in: false, configured: false };
  updateAuthUI();
}

export async function enableSync() {
  if (!_authState.logged_in) {
    alert('Login first to enable cloud sync.');
    return;
  }
  const res = await fetch('/api/sync/enable', { method: 'POST' }).then(r => r.json());
  if (res.ok) {
    _authState.sync_enabled = true;
    updateAuthUI();
  }
}

export async function disableSync() {
  await fetch('/api/sync/disable', { method: 'POST' }).catch(() => {});
  _authState.sync_enabled = false;
  updateAuthUI();
}

function updateAuthUI() {
  // Status bar cloud indicator
  const cloudStatus = $('sb-cloud-status');
  const cloudDot = $('sb-cloud-dot');
  const cloudLabel = $('sb-cloud-label');
  if (cloudStatus) {
    if (_authState.logged_in && _authState.sync_enabled) {
      cloudStatus.style.display = '';
      if (cloudDot) cloudDot.dataset.status = 'success';
      if (cloudLabel) cloudLabel.textContent = 'Synced';
    } else if (_authState.logged_in) {
      cloudStatus.style.display = '';
      if (cloudDot) cloudDot.dataset.status = 'idle';
      if (cloudLabel) cloudLabel.textContent = 'Sync off';
    } else {
      cloudStatus.style.display = 'none';
    }
  }

  // Settings panel auth section (if it exists on this page)
  const authBtn = $('auth-btn');
  const authLabel = $('auth-label');
  const syncToggle = $('f-cloud-sync');

  if (authBtn) {
    if (_authState.logged_in) {
      authBtn.textContent = 'Logout';
      authBtn.onclick = logout;
    } else {
      authBtn.textContent = 'Login with Google';
      authBtn.onclick = login;
    }
  }
  if (authLabel) {
    authLabel.textContent = _authState.logged_in ? (_authState.email || '') : (_authState.configured ? '' : 'Firebase not configured');
  }
  if (syncToggle) {
    syncToggle.checked = !!_authState.sync_enabled;
    syncToggle.disabled = !_authState.logged_in;
  }
}

export function getAuthState() { return { ..._authState }; }

// Init on load
checkAuthStatus();
