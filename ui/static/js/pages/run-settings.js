// run-settings.js — Settings panel, collect/populate/save for Run page.
// Extracted from run.js for maintainability.

import { apiPost } from '/static/js/core/api.js';

const $ = id => document.getElementById(id);

// ── Settings Panel ──────────────────────────────────────────────────────────

export function openSettings() {
  $('settings-overlay').style.display = '';
  refreshFirebaseUI();
}

export function closeSettings(getPlanFile) {
  $('settings-overlay').style.display = 'none';
  saveSettings(getPlanFile);
}

export async function refreshFirebaseUI() {
  try {
    const status = await fetch('/api/firebase/status').then(r => r.json());
    const notSetup = $('firebase-not-setup');
    const wizard = $('firebase-wizard');
    const connected = $('firebase-connected');
    if (status.configured && status.authenticated) {
      if (notSetup) notSetup.style.display = 'none';
      if (wizard) wizard.style.display = 'none';
      if (connected) {
        connected.style.display = '';
        $('firebase-email').textContent = status.email || '';
        $('firebase-project-id').textContent = status.project_id || '';
      }
    } else if (status.configured) {
      if (notSetup) notSetup.style.display = 'none';
      if (wizard) wizard.style.display = '';
      if (connected) connected.style.display = 'none';
    } else {
      if (notSetup) notSetup.style.display = '';
      if (wizard) wizard.style.display = 'none';
      if (connected) connected.style.display = 'none';
    }
  } catch (_) {}
}

export function showSetupStatus(el, msg, success) {
  if (!el) return;
  el.style.display = '';
  el.style.background = success
    ? 'color-mix(in srgb, var(--color-success) 10%, transparent)'
    : 'color-mix(in srgb, var(--color-danger) 10%, transparent)';
  el.style.color = success ? 'var(--color-success)' : 'var(--color-danger)';
  el.textContent = msg;
}

export function collectSettings(planFile) {
  const sup = document.querySelector('input[name="supervisor"]:checked')?.value || 'claude';
  return {
    repo_root: $('f-repo-root')?.value?.trim() || '',
    aider_model: $('f-aider-model')?.value?.trim() || '',
    supervisor: sup,
    manual_supervisor: true,
    supervisor_command: sup === 'custom' ? $('f-supervisor-command')?.value?.trim() || '' : '',
    validation_command: $('f-validation-cmd')?.value?.trim() || '',
    task_timeout: parseInt($('f-task-timeout')?.value || '600', 10),
    max_task_retries: parseInt($('f-max-retries')?.value || '10', 10),
    dry_run: $('f-dry-run')?.checked || false,
    model_lock: $('f-model-lock')?.checked || false,
    auto_commit: ($('sb-auto-commit')?.checked !== false),
    goal: $('wiz-goal')?.value?.trim() || '',
    plan_file: planFile || '',
  };
}

export function populateSettings(s) {
  if ($('f-repo-root')) $('f-repo-root').value = s.repo_root || '';
  if ($('f-aider-model')) $('f-aider-model').value = s.aider_model || '';
  const sup = s.supervisor || 'claude';
  const radio = document.querySelector(`input[name="supervisor"][value="${sup}"]`);
  if (radio) radio.checked = true;
  if ($('f-supervisor-command')) {
    $('f-supervisor-command').value = s.supervisor_command || '';
    $('f-supervisor-command').style.display = sup === 'custom' ? '' : 'none';
  }
  if ($('f-validation-cmd')) $('f-validation-cmd').value = s.validation_command || '';
  if ($('f-task-timeout')) $('f-task-timeout').value = s.task_timeout || 600;
  if ($('f-max-retries')) $('f-max-retries').value = s.max_task_retries || 10;
  if ($('f-dry-run')) $('f-dry-run').checked = !!s.dry_run;
  if ($('f-model-lock')) $('f-model-lock').checked = !!s.model_lock;
}

export async function saveSettings(getPlanFile) {
  try {
    const planFile = typeof getPlanFile === 'function' ? getPlanFile() : '';
    await apiPost('/api/settings', collectSettings(planFile));
  } catch (_) {}
}
