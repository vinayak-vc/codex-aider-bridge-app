// pages/run.js — Run page controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';
import { play }      from '/static/js/core/sounds.js';

// ── Supervisor presets ────────────────────────────────────────────────────────

const SUPERVISOR_CMDS = {
  codex:    'codex.cmd exec --skip-git-repo-check --color never',
  claude:   'claude',
  cursor:   'cursor',
  windsurf: 'windsurf',
  manual:   null,   // sets manual_supervisor: true
  custom:   '',     // freeform
};

// ── Compatibility info ────────────────────────────────────────────────────────

const SUPERVISOR_COMPAT = {
  codex: {
    level: 'warning',
    message:
      'Codex CLI requires <strong>OPENAI_API_KEY</strong> set in your environment. ' +
      'ChatGPT Plus / Pro does NOT include API access — ' +
      'sign up separately at platform.openai.com and add credits.',
  },
  claude: {
    level: 'success',
    message:
      'Claude Code works with your <strong>Claude Pro subscription</strong> via ' +
      '<code>claude login</code> (OAuth — no raw API key needed).',
  },
  cursor: {
    level: 'info',
    message:
      'Cursor runs as an IDE and uses your <strong>Cursor subscription</strong>. ' +
      'Cursor IDE must be installed and licensed on this machine.',
  },
  windsurf: {
    level: 'info',
    message:
      'Windsurf runs as an IDE and uses your <strong>Windsurf subscription</strong>. ' +
      'Windsurf IDE must be installed and licensed on this machine.',
  },
  manual: {
    level: 'success',
    message:
      '<strong>No account or API key required.</strong> ' +
      'You provide supervisor responses manually — works fully offline.',
  },
  custom: {
    level: 'info',
    message:
      'Custom command: ensure the binary is on PATH and any required ' +
      'environment variables (API keys, tokens) are set before launching.',
  },
};

const MODEL_COMPAT = {
  'gpt-': {
    level: 'warning',
    message:
      'OpenAI models (gpt-*) require <strong>OPENAI_API_KEY</strong>. ' +
      'ChatGPT Plus / Pro does NOT include API access.',
  },
  'claude-': {
    level: 'warning',
    message:
      'Anthropic models (claude-*) require <strong>ANTHROPIC_API_KEY</strong>. ' +
      'Claude Pro (claude.ai) does NOT include API access — ' +
      'API billing is separate at console.anthropic.com.',
  },
  'o1': {
    level: 'warning',
    message:
      'OpenAI o1 models require <strong>OPENAI_API_KEY</strong>. ' +
      'ChatGPT Plus / Pro does NOT include API access.',
  },
};

function makeBanner(level, html) {
  // level: 'success' | 'info' | 'warning'
  const icons = {
    success: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="15" height="15"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
    info:    '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="15" height="15"><path stroke-linecap="round" stroke-linejoin="round" d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/></svg>',
    warning: '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="15" height="15"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>',
  };
  const colorMap = {
    success: 'var(--color-success)',
    info:    'var(--color-info)',
    warning: 'var(--color-warning)',
  };
  const bgMap = {
    success: 'rgba(34,197,94,.08)',
    info:    'rgba(6,182,212,.08)',
    warning: 'rgba(245,158,11,.08)',
  };
  return `<div style="display:flex;align-items:flex-start;gap:8px;padding:10px 12px;border-radius:var(--radius-md);border:1px solid ${colorMap[level]};background:${bgMap[level]};font-size:var(--font-size-sm);color:var(--color-text-muted);line-height:1.5">
    <span style="color:${colorMap[level]};flex-shrink:0;margin-top:1px">${icons[level]}</span>
    <span>${html}</span>
  </div>`;
}

function updateCompatWarnings() {
  // Supervisor banner
  const sup = document.querySelector('input[name="supervisor"]:checked')?.value || '';
  const supBanner = $('supervisor-compat-banner');
  if (supBanner) {
    const compat = SUPERVISOR_COMPAT[sup];
    if (compat) {
      supBanner.innerHTML = makeBanner(compat.level, compat.message);
      supBanner.style.display = '';
    } else {
      supBanner.style.display = 'none';
    }
  }

  // Model banner
  const model = $('f-aider-model')?.value?.trim() || '';
  const modelBanner = $('model-api-banner');
  if (modelBanner) {
    let matched = null;
    for (const [prefix, info] of Object.entries(MODEL_COMPAT)) {
      if (model.startsWith(prefix)) { matched = info; break; }
    }
    if (matched) {
      modelBanner.innerHTML = makeBanner(matched.level, matched.message);
      modelBanner.style.display = '';
    } else {
      modelBanner.style.display = 'none';
    }
  }
}

// ── DOM refs ──────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

// ── Natural Language Mode ─────────────────────────────────────────────────────

let _nlMode = false;
let _currentBrief = null;
let _nlProjectKey  = '';   // repo_root used as project key

// Status chip states
const NL_STATUS = {
  drafting:           { label: 'Drafting',            mod: '' },
  needs_clarification:{ label: 'Needs clarification', mod: '--warning' },
  ready_to_run:       { label: 'Ready to run',        mod: '--success' },
  plan_ready:         { label: 'Plan ready',          mod: '--info' },
  plan_confirmed:     { label: 'Plan confirmed',      mod: '--success' },
};

function _nlStatusFor(brief) {
  if (!brief) return 'drafting';
  return brief.needs_clarification ? 'needs_clarification' : 'ready_to_run';
}

function setNLStatusChip(status) {
  const row  = $('nl-status-row');
  const chip = $('nl-status-chip');
  if (!row || !chip) return;
  const info = NL_STATUS[status] || NL_STATUS.drafting;
  chip.textContent = info.label;
  chip.className = `nl-status-chip${info.mod ? ' ' + info.mod : ''}`;
  row.style.display = '';
}

async function _saveNLState(status, extra = {}) {
  if (!_nlProjectKey) return;
  try {
    await fetch('/api/run/nl/state', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_root: _nlProjectKey,
        message:   $('nl-input')?.value || '',
        brief:     _currentBrief || {},
        status,
        ...extra,
      }),
    });
  } catch (_) {}
}

async function _clearNLState() {
  if (!_nlProjectKey) return;
  try {
    await fetch('/api/run/nl/state', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_root: _nlProjectKey }),
    });
  } catch (_) {}
}

async function _restoreNLState() {
  if (!_nlProjectKey) return;
  try {
    const res   = await fetch(`/api/run/nl/state?repo_root=${encodeURIComponent(_nlProjectKey)}`);
    const state = await res.json();
    if (!state || !state.message) return;

    // Restore textarea
    if ($('nl-input')) $('nl-input').value = state.message;

    // Restore brief
    if (state.brief && state.brief.goal) {
      _currentBrief = state.brief;
      renderBrief(state.brief);
    }

    // Restore plan tasks if present
    if (Array.isArray(state.tasks) && state.tasks.length) {
      renderNLTaskList(state.tasks, state.plan_summary || '');

      if (state.plan_status === 'plan_confirmed') {
        _confirmedPlanFile = state.plan_file || '';
        const banner = $('nl-plan-confirmed-banner');
        if (banner) {
          banner.textContent = _confirmedPlanFile
            ? `Plan saved → ${_confirmedPlanFile}`
            : 'Plan confirmed.';
        }
        $('nl-plan-confirmed-wrap').style.display = '';
        $('nl-plan-actions').style.display = 'none';
      }
    }

    // Restore status chip
    const status = state.status || _nlStatusFor(state.brief || null);
    setNLStatusChip(status);
  } catch (_) {}
}

function setMode(mode) {
  _nlMode = mode === 'nl';
  $('nl-panel').style.display        = _nlMode ? '' : 'none';
  $('structured-panel').style.display = _nlMode ? 'none' : '';
  $('btn-mode-nl').classList.toggle('--active', _nlMode);
  $('btn-mode-structured').classList.toggle('--active', !_nlMode);
  // hide the shortcut hint in NL mode (Ctrl+Enter has different meaning there)
  const hint = $('run-shortcut-hint');
  if (hint) hint.style.display = _nlMode ? 'none' : '';
}

function _renderBriefSection(label, items) {
  if (!items?.length) return '';
  return `<div class="nl-brief-section">
    <div class="nl-brief-label">${label}</div>
    ${items.map(i => `<div class="nl-brief-item">${escHtml(String(i))}</div>`).join('')}
  </div>`;
}

function renderBrief(brief) {
  _currentBrief = brief;
  const card = $('nl-brief-card');
  if (!card) return;

  card.innerHTML = `
    <div class="nl-brief-section">
      <div class="nl-brief-label">Goal</div>
      <div class="nl-brief-goal">${escHtml(brief.goal || '')}</div>
    </div>
    ${_renderBriefSection('Assumptions', brief.assumptions)}
    ${_renderBriefSection('Constraints', brief.constraints)}
    ${_renderBriefSection('Acceptance Criteria', brief.acceptance_criteria)}
  `;

  const qWrap = $('nl-questions-wrap');
  const qCard = $('nl-questions-card');
  if (qWrap && qCard) {
    if (brief.clarification_questions?.length) {
      qCard.innerHTML = `
        <div class="nl-brief-section">
          <div class="nl-brief-label">Clarification Needed</div>
          ${brief.clarification_questions.map(q => `<div class="nl-brief-item">• ${escHtml(q)}</div>`).join('')}
        </div>`;
      qWrap.style.display = '';
    } else {
      qWrap.style.display = 'none';
    }
  }

  $('nl-brief-output').style.display = '';
}

async function generateBrief() {
  const message = $('nl-input')?.value?.trim();
  if (!message) {
    toast('Please describe what you want to build.', 'warning');
    $('nl-input')?.focus();
    return;
  }

  const btn = $('btn-generate-brief');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  $('nl-brief-output').style.display = 'none';

  try {
    const settings = await fetch('/api/settings').then(r => r.json());
    if (!_nlProjectKey) _nlProjectKey = settings.repo_root || '';
    const res = await fetch('/api/run/brief', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, repo_root: _nlProjectKey }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Failed to generate brief.');
    renderBrief(data);
    const status = _nlStatusFor(data);
    setNLStatusChip(status);
    await _saveNLState(status);
  } catch (err) {
    toast(err.message || 'Failed to generate brief.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z"/> </svg> Generate Brief';
    }
  }
}

async function applyBrief() {
  if (!_currentBrief) return;
  setMode('structured');

  $('f-goal').value = _currentBrief.goal || '';

  // Fold constraints + acceptance criteria into the clarifications field
  const extras = [
    ...(_currentBrief.constraints || []).map(c => `Constraint: ${c}`),
    ...(_currentBrief.acceptance_criteria || []).map(a => `Acceptance: ${a}`),
  ];
  if (extras.length) {
    const existing = $('f-clarifications').value.trim();
    $('f-clarifications').value = existing
      ? `${existing}\n${extras.join('\n')}`
      : extras.join('\n');
    // Auto-open advanced accordion so user sees the filled clarifications
    const trigger = $('adv-trigger');
    const body    = $('adv-body');
    if (trigger?.getAttribute('aria-expanded') !== 'true') {
      trigger?.setAttribute('aria-expanded', 'true');
      body?.classList.remove('--hidden');
    }
  }

  updateCommandPreview();
  await _saveNLState('ready_to_run');
  $('f-goal')?.focus();
  toast('Brief applied — review the fields and launch when ready.', 'success');
}

// ── Plan generation ───────────────────────────────────────────────────────────

let _confirmedPlanFile = '';

function renderNLTaskList(tasks, summary) {
  const list = $('nl-task-list');
  if (!list) return;

  list.innerHTML = tasks.map(t => {
    const type  = t.type || 'modify';
    const files = (t.files || []).join(', ');
    return `<div class="nl-task-item">
      <div class="nl-task-num">${t.id}</div>
      <div class="nl-task-body">
        <div class="nl-task-head">
          <span class="nl-task-title">${escHtml(t.title || t.instruction?.slice(0, 60) || '')}</span>
          <span class="relay-task-type-badge" data-type="${escHtml(type)}">${escHtml(type)}</span>
        </div>
        <div class="nl-task-instruction">${escHtml(t.instruction || '')}</div>
        ${files ? `<div class="nl-task-files">${escHtml(files)}</div>` : ''}
      </div>
    </div>`;
  }).join('');

  const summaryEl = $('nl-plan-summary');
  if (summaryEl) {
    summaryEl.textContent = summary || '';
    summaryEl.style.display = summary ? '' : 'none';
  }

  $('nl-plan-output').style.display = '';
  $('nl-plan-actions').style.display = 'flex';
  $('nl-plan-confirmed-wrap').style.display = 'none';
}

async function generatePlan() {
  if (!_currentBrief?.goal) {
    toast('Generate a brief first, then generate a plan.', 'warning');
    return;
  }

  const btn = $('btn-generate-plan');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating plan…'; }
  $('nl-plan-output').style.display = 'none';

  try {
    const res  = await fetch('/api/run/nl/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_root: _nlProjectKey, brief: _currentBrief }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Plan generation failed.');

    renderNLTaskList(data.tasks, data.plan_summary);
    setNLStatusChip('plan_ready');
    await _saveNLState('plan_ready', {
      tasks:        data.tasks,
      plan_summary: data.plan_summary,
      plan_status:  'plan_ready',
    });
    _confirmedPlanFile = '';
  } catch (err) {
    toast(err.message || 'Plan generation failed.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"/></svg> Generate Plan';
    }
  }
}

async function confirmPlan() {
  const taskItems = $('nl-task-list')?.querySelectorAll('.nl-task-item');
  if (!taskItems?.length) {
    toast('No plan to confirm.', 'warning');
    return;
  }

  const btn = $('btn-confirm-plan');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  // Extract current tasks from the rendered list (use the saved state)
  try {
    const stateRes = await fetch(`/api/run/nl/state?repo_root=${encodeURIComponent(_nlProjectKey)}`);
    const state    = await stateRes.json();
    const tasks    = state.tasks || [];
    const summary  = state.plan_summary || '';

    const res  = await fetch('/api/run/nl/plan/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_root:    _nlProjectKey,
        tasks,
        plan_summary: summary,
        brief:        _currentBrief || {},
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Confirm failed.');

    _confirmedPlanFile = data.plan_file || '';

    // Show confirmed banner
    const banner = $('nl-plan-confirmed-banner');
    if (banner) {
      banner.textContent = _confirmedPlanFile
        ? `Plan saved → ${_confirmedPlanFile}`
        : 'Plan confirmed.';
    }
    $('nl-plan-confirmed-wrap').style.display = '';
    $('nl-plan-actions').style.display = 'none';

    setNLStatusChip('plan_confirmed');
    toast('Plan confirmed and saved.', 'success');
  } catch (err) {
    toast(err.message || 'Failed to confirm plan.', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Confirm Plan'; }
  }
}

// ── Settings → form ──────────────────────────────────────────────────────────

function populateForm(s) {
  $('f-goal').value              = s.goal              || '';
  $('f-repo-root').value         = s.repo_root         || '';
  $('f-aider-model').value       = s.aider_model       || 'ollama/mistral';
  $('f-dry-run').checked         = !!s.dry_run;
  $('f-validation-cmd').value    = s.validation_command || '';
  $('f-max-retries').value       = s.max_task_retries   ?? 2;
  $('f-max-plan-attempts').value = s.max_plan_attempts  ?? 3;
  $('f-task-timeout').value      = s.task_timeout       ?? 300;
  $('f-idea-file').value         = s.idea_file          || '';
  $('f-plan-output-file').value  = s.plan_output_file   || '';

  // Clarifications (stored as array, shown as one-per-line)
  const clars = s.clarifications;
  $('f-clarifications').value = Array.isArray(clars)
    ? clars.join('\n')
    : (clars || '');

  // Supervisor radio
  const sup = s.supervisor || 'codex';
  const radio = document.querySelector(`input[name="supervisor"][value="${sup}"]`);
  if (radio) radio.checked = true;

  // Custom supervisor command
  if (sup === 'custom') {
    $('f-supervisor-command').value = s.supervisor_command || '';
    $('supervisor-custom-wrap').style.display = '';
  }

  updateCommandPreview();
  updateCompatWarnings();
}

// ── Form → settings object ────────────────────────────────────────────────────

function collectSettings() {
  const sup = document.querySelector('input[name="supervisor"]:checked')?.value || 'codex';
  const clarRaw = $('f-clarifications').value.trim();
  const clarifications = clarRaw
    ? clarRaw.split('\n').map(l => l.trim()).filter(Boolean)
    : [];

  const settings = {
    goal:               $('f-goal').value.trim(),
    repo_root:          $('f-repo-root').value.trim(),
    aider_model:        $('f-aider-model').value.trim(),
    supervisor:         sup,
    manual_supervisor:  sup === 'manual',
    supervisor_command: sup === 'manual'   ? ''
                      : sup === 'custom'   ? $('f-supervisor-command').value.trim()
                      : (SUPERVISOR_CMDS[sup] || ''),
    dry_run:            $('f-dry-run').checked,
    validation_command: $('f-validation-cmd').value.trim(),
    max_task_retries:   parseInt($('f-max-retries').value, 10)      || 2,
    max_plan_attempts:  parseInt($('f-max-plan-attempts').value, 10) || 3,
    task_timeout:       parseInt($('f-task-timeout').value, 10)      || 300,
    idea_file:          $('f-idea-file').value.trim(),
    plan_output_file:   $('f-plan-output-file').value.trim(),
    clarifications,
  };

  return settings;
}

// ── Command preview (mirrors bridge_runner.py build_command) ──────────────────

function updateCommandPreview() {
  const s   = collectSettings();
  const pre = $('cmd-preview');
  if (!pre) return;

  const parts = [];
  parts.push({ cls: 'cmd-exe',  text: 'python main.py' });

  if (s.goal)
    parts.push({ cls: 'cmd-goal', text: JSON.stringify(s.goal) });

  if (s.repo_root)
    parts.push({ cls: 'cmd-flag', text: `--repo-root ${s.repo_root}` });
  if (s.idea_file)
    parts.push({ cls: 'cmd-flag', text: `--idea-file ${s.idea_file}` });
  if (s.aider_model)
    parts.push({ cls: 'cmd-flag', text: `--aider-model ${s.aider_model}` });
  if (s.manual_supervisor)
    parts.push({ cls: 'cmd-flag', text: '--manual-supervisor' });
  else if (s.supervisor_command)
    parts.push({ cls: 'cmd-flag', text: `--supervisor-command "${s.supervisor_command}"` });
  if (s.validation_command)
    parts.push({ cls: 'cmd-flag', text: `--validation-command "${s.validation_command}"` });
  if (s.max_plan_attempts && s.max_plan_attempts !== 3)
    parts.push({ cls: 'cmd-flag', text: `--max-plan-attempts ${s.max_plan_attempts}` });
  if (s.max_task_retries && s.max_task_retries !== 2)
    parts.push({ cls: 'cmd-flag', text: `--max-task-retries ${s.max_task_retries}` });
  if (s.task_timeout && s.task_timeout !== 300)
    parts.push({ cls: 'cmd-flag', text: `--task-timeout ${s.task_timeout}` });
  if (s.plan_output_file)
    parts.push({ cls: 'cmd-flag', text: `--plan-output-file ${s.plan_output_file}` });
  if (s.dry_run)
    parts.push({ cls: 'cmd-flag', text: '--dry-run' });
  for (const c of (s.clarifications || []))
    parts.push({ cls: 'cmd-flag', text: `--clarification "${c}"` });

  parts.push({ cls: '', text: '--log-level INFO' });

  pre.innerHTML = parts
    .map(p => p.cls
      ? `<span class="${p.cls}">${escHtml(p.text)}</span>`
      : escHtml(p.text))
    .join(' \\\n  ');
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ── Log terminal ──────────────────────────────────────────────────────────────

let _lineCount = 0;
let _autoScroll = true;

function appendLog(rawLine) {
  const terminal = $('log-terminal');
  if (!terminal) return;

  // Hide empty-state placeholder
  const empty = $('log-empty');
  if (empty) empty.style.display = 'none';

  _lineCount++;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = `${_lineCount} line${_lineCount !== 1 ? 's' : ''}`;

  // Colour-code common log prefixes
  const line = rawLine;
  let cls = '';
  if (/\|\s*(ERROR|CRITICAL)\s*\|/.test(line))  cls = 'log-error';
  else if (/\|\s*WARNING\s*\|/.test(line))       cls = 'log-warn';
  else if (/supervisor approved|✓|approved/.test(line)) cls = 'log-ok';
  else if (/Bridge start|plan_ready|starting/.test(line)) cls = 'log-info';
  else if (line.trim().startsWith('{"_bridge_event"')) cls = 'log-event';

  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = line + '\n';
  terminal.appendChild(span);

  if (_autoScroll) terminal.scrollTop = terminal.scrollHeight;
}

function clearLog() {
  const terminal = $('log-terminal');
  if (!terminal) return;
  _lineCount = 0;
  const countEl = $('log-line-count');
  if (countEl) countEl.textContent = '0 lines';

  // Keep the empty-state element, remove log spans
  const spans = terminal.querySelectorAll('span.log-error, span.log-warn, span.log-ok, span.log-info, span.log-event, span:not(#log-empty span)');
  spans.forEach(s => s.remove());

  // Re-show empty state
  const empty = $('log-empty');
  if (empty) empty.style.display = '';
}

// ── Run status banner ─────────────────────────────────────────────────────────

function showBanner(type, message) {
  const el = $('run-status-banner');
  if (!el) return;
  el.className = `run-status-banner --${type}`;
  el.textContent = message;
  el.style.display = '';
}

function hideBanner() {
  const el = $('run-status-banner');
  if (el) el.style.display = 'none';
}

// ── Run state ─────────────────────────────────────────────────────────────────

let _sse = null;
let _isRunning = false;

function setRunning(running) {
  _isRunning = running;
  const form  = $('run-form');
  const btnL  = $('btn-launch-run');
  const btnS  = $('btn-stop-run');
  const hint  = $('run-shortcut-hint');

  if (form)  form.querySelectorAll('input, textarea, button.model-preset, button.num-btn, .accordion-trigger').forEach(el => {
    el.disabled = running;
  });
  if (btnL)  { btnL.disabled = running; btnL.style.display = running ? 'none' : ''; }
  if (btnS)  btnS.style.display = running ? '' : 'none';
  if (hint)  hint.style.display = running ? 'none' : '';
}

// ── SSE handling ──────────────────────────────────────────────────────────────

function connectSSE() {
  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log',        d => appendLog(d.line || ''))
    .on('start',      () => showBanner('running', 'Run in progress…'))
    .on('plan_ready', d => {
      const n = d.task_count || d.total_tasks || '?';
      appendLog(`[bridge] Plan ready — ${n} tasks`);
    })
    .on('complete', d => {
      const ok  = d.status === 'success';
      const sec = d.elapsed ? ` in ${d.elapsed}s` : '';
      showBanner(ok ? 'success' : 'failure',
        ok ? `Run completed successfully${sec}.` : `Run finished with failures${sec}.`);
      setRunning(false);
      _sse.disconnect();
    })
    .on('error', d => {
      showBanner('failure', `Error: ${d.message || 'unknown error'}`);
      setRunning(false);
      _sse.disconnect();
    })
    .on('stopped', () => {
      showBanner('stopped', 'Run stopped.');
      setRunning(false);
      _sse.disconnect();
    })
    .connect();
}

// ── Launch ────────────────────────────────────────────────────────────────────

async function launchRun() {
  const s = collectSettings();
  if (!s.goal) {
    toast('Please enter a goal / instruction.', 'warning');
    $('f-goal')?.focus();
    return;
  }

  clearLog();
  hideBanner();
  setRunning(true);
  connectSSE();

  try {
    play('launch');
    await apiPost('/api/run', s);
  } catch (err) {
    showBanner('failure', err.message || 'Failed to start run.');
    setRunning(false);
    _sse?.disconnect();
    toast(err.message || 'Failed to start run.', 'error');
  }
}

// ── Bind controls ─────────────────────────────────────────────────────────────

function bindControls() {
  // Mode toggle
  $('btn-mode-structured')?.addEventListener('click', () => setMode('structured'));
  $('btn-mode-nl')?.addEventListener('click',         () => setMode('nl'));

  // NL brief controls
  $('btn-generate-brief')?.addEventListener('click', generateBrief);
  $('btn-apply-brief')?.addEventListener('click', applyBrief);

  // Regenerate brief — clear brief + plan, keep textarea message
  $('btn-regenerate-brief')?.addEventListener('click', () => {
    $('nl-brief-output').style.display = 'none';
    $('nl-plan-output').style.display = 'none';
    _currentBrief = null;
    _confirmedPlanFile = '';
    $('nl-input')?.focus();
  });

  // Plan controls
  $('btn-generate-plan')?.addEventListener('click', generatePlan);
  $('btn-confirm-plan')?.addEventListener('click', confirmPlan);
  $('btn-regenerate-plan')?.addEventListener('click', () => {
    $('nl-plan-output').style.display = 'none';
    _confirmedPlanFile = '';
    setNLStatusChip(_nlStatusFor(_currentBrief));
  });

  // New Conversation — clear everything including server state
  $('btn-new-conversation')?.addEventListener('click', async () => {
    if ($('nl-input')) $('nl-input').value = '';
    $('nl-brief-output').style.display = 'none';
    $('nl-plan-output').style.display = 'none';
    $('nl-status-row').style.display = 'none';
    _currentBrief = null;
    _confirmedPlanFile = '';
    await _clearNLState();
    $('nl-input')?.focus();
  });

  $('nl-input')?.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      generateBrief();
    }
  });

  // Launch / stop
  $('btn-launch-run')?.addEventListener('click', launchRun);
  $('btn-stop-run')?.addEventListener('click', async () => {
    try {
      await apiPost('/api/run/stop');
    } catch (err) {
      toast(err.message || 'Stop failed.', 'error');
    }
  });

  // Ctrl+Enter to launch (structured mode only; NL mode has its own handler on the textarea)
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !_isRunning && !_nlMode) {
      e.preventDefault();
      launchRun();
    }
  });

  // Browse folder
  $('btn-browse-folder')?.addEventListener('click', async () => {
    try {
      const d = await fetch('/api/browse/folder').then(r => r.json());
      if (d.path) { $('f-repo-root').value = d.path; updateCommandPreview(); }
    } catch (_) {}
  });

  // Browse file
  $('btn-browse-file')?.addEventListener('click', async () => {
    try {
      const d = await fetch('/api/browse/file').then(r => r.json());
      if (d.path) { $('f-idea-file').value = d.path; updateCommandPreview(); }
    } catch (_) {}
  });

  // Model presets
  document.querySelectorAll('.model-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      $('f-aider-model').value = btn.dataset.model;
      updateCommandPreview();
    });
  });

  // Model input change → update compat warning
  $('f-aider-model')?.addEventListener('input', updateCompatWarnings);

  // Number +/- buttons
  document.querySelectorAll('.num-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = $(btn.dataset.target);
      if (!input) return;
      const delta = parseInt(btn.dataset.delta, 10);
      const min   = parseInt(input.min, 10) || 0;
      const max   = parseInt(input.max, 10) || 9999;
      input.value = Math.max(min, Math.min(max, (parseInt(input.value, 10) || 0) + delta));
      updateCommandPreview();
    });
  });

  // Supervisor radio → show/hide custom input + update preview
  document.querySelectorAll('input[name="supervisor"]').forEach(r => {
    r.addEventListener('change', () => {
      const isCustom = r.value === 'custom';
      const wrap = $('supervisor-custom-wrap');
      if (wrap) wrap.style.display = isCustom ? '' : 'none';
      updateCommandPreview();
      updateCompatWarnings();
    });
  });
  $('f-supervisor-command')?.addEventListener('input', updateCommandPreview);

  // All form inputs → live preview update
  ['f-goal','f-repo-root','f-aider-model','f-dry-run','f-validation-cmd',
   'f-max-retries','f-max-plan-attempts','f-task-timeout',
   'f-idea-file','f-plan-output-file','f-clarifications']
    .forEach(id => {
      const el = $(id);
      if (el) el.addEventListener('input', updateCommandPreview);
    });

  // Auto-scroll toggle
  $('log-autoscroll')?.addEventListener('change', e => { _autoScroll = e.target.checked; });

  // Clear log
  $('btn-clear-log')?.addEventListener('click', clearLog);

  // Stdin input — send text to running process
  const stdinInput = $('log-stdin-input');
  const btnSend    = $('btn-log-send');

  async function sendStdin() {
    const text = stdinInput?.value?.trim();
    if (!text) return;
    try {
      await apiPost('/api/run/input', { text });
      if (stdinInput) stdinInput.value = '';
      play('inputSent');
    } catch (err) {
      toast(err.message || 'Could not send input.', 'error');
    }
  }

  stdinInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); sendStdin(); }
  });
  btnSend?.addEventListener('click', sendStdin);

  // Advanced accordion
  const trigger = $('adv-trigger');
  const body    = $('adv-body');
  trigger?.addEventListener('click', () => {
    const expanded = trigger.getAttribute('aria-expanded') === 'true';
    trigger.setAttribute('aria-expanded', String(!expanded));
    body?.classList.toggle('--hidden', expanded);
  });
}

// ── Hydrate from live run (if one is already running when page loads) ─────────

async function hydrateExistingRun() {
  try {
    const status = await fetch('/api/run/status').then(r => r.json());
    const alive    = status.is_running || status.status === 'running' || status.status === 'paused';
    const finished = ['success', 'failure', 'stopped'].includes(status.status);

    if (!alive && !finished) return;

    // Always replay logged lines so the terminal is populated
    const log = await fetch('/api/run/log').then(r => r.json());
    if (Array.isArray(log.lines)) log.lines.forEach(l => appendLog(l));

    if (alive) {
      setRunning(true);
      showBanner('running', 'Run in progress…');
      connectSSE();
    } else {
      // Run already finished — show the final banner so user sees result on return
      setRunning(false);
      showBanner(
        status.status,
        status.status === 'success' ? 'Run completed successfully.' :
        status.status === 'stopped' ? 'Run was stopped.'            : 'Run finished with errors.'
      );
    }
  } catch (_) {}
}

// ── Entry point ───────────────────────────────────────────────────────────────

async function init() {
  bindControls();

  // Load saved settings
  let settings = {};
  try {
    settings = await fetch('/api/settings').then(r => r.json());
    populateForm(settings);
  } catch (_) {
    updateCommandPreview();
  }

  // Set project key for NL persistence
  _nlProjectKey = (settings.repo_root || '').trim();

  // Restore NL conversation state if present
  await _restoreNLState();

  // If a run is already active, show live state
  await hydrateExistingRun();
  updateCompatWarnings();
}

init();
