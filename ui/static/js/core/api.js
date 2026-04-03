// api.js — fetch wrapper with error normalisation

/**
 * Fetch JSON from the bridge API.
 * Always resolves; throws on network error or non-2xx status.
 */
export async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const d = await res.json(); msg = d.error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

/**
 * POST JSON body and return parsed response.
 */
export async function apiPost(url, body = {}) {
  return apiFetch(url, { method: 'POST', body: JSON.stringify(body) });
}

/**
 * DELETE and return parsed response.
 */
export async function apiDelete(url) {
  return apiFetch(url, { method: 'DELETE' });
}
