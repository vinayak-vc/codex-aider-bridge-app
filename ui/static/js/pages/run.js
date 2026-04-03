// pages/run.js — Run page controller

import { SSEClient } from '/static/js/core/sse.js';
import { apiPost }   from '/static/js/core/api.js';
import { toast }     from '/static/js/core/toast.js';
import { play }      from '/static/js/core/sounds.js';

// ── Tab switching ────────────────────────────────────────────────────────────

let _activeTab = 'settings';
let _logBadgeCount = 0;

function switchRunTab(tabName) {
  _activeTab = tabName;
  document.querySelectorAll('#run-tabs .tab').forEach(btn => {
    btn.classList.toggle('--active', btn.dataset.tab === tabName);
  });
  const settingsPanel = $('run-tab-settings');
  const logPanel      = $('run-tab-log');
  if (settingsPanel) {
    settingsPanel.classList.toggle('--active', tabName === 'settings');
    settingsPanel.style.display = tabName === 'settings' ? '' : 'none';
  }
  if (logPanel) {
    logPanel.classList.toggle('--active', tabName === 'log');
    logPanel.style.display = tabName === 'log' ? '' : 'none';
  }
  // Clear log badge when switching to log tab
  if (tabName === 'log') {
    _logBadgeCount = 0;
    _updateLogBadge();
    // Scroll to bottom when switching to log
    const terminal = $('log-terminal');
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
  }
}

function _updateLogBadge() {
  const badge = $('run-tab-log-badge');
  if (!badge) return;
  if (_logBadgeCount > 0 && _activeTab !== 'log') {
    badge.textContent = _logBadgeCount > 99 ? '99+' : String(_logBadgeCount);
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

function _incrementLogBadge() {
  if (_activeTab !== 'log') {
    _logBadgeCount++;
    _updateLogBadge();
  }
}

// ── Supervisor presets ────────────────────────────────────────────────────────

const SUPERVISOR_CMDS = {
  codex:    'codex.cmd exec --skip-git-repo-check --color never',
  claude:   'claude',
  cursor:   'cursor',
  windsurf: 'windsurf',
  chatbot:  null,   // inline relay wizard — handled by UI in Milestone B
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
  chatbot: {
    level: 'info',
    message:
      '<strong>No API key required.</strong> ' +
      'At each review point, the UI shows a prompt to copy into any chatbot (ChatGPT, Claude, Gemini…). ' +
      'Paste the response back and the run continues.',
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

// ── Role indicator strip ─────────────────────────────────────────────────────

function _setRoleState(roleId, state, label) {
  const badge = $(roleId);
  const labelEl = $(`${roleId}-label`);
  if (badge) badge.dataset.state = state;  // 'idle' | 'active' | 'done'
  if (labelEl && label) labelEl.textContent = label;
}

function showRoleStrip(visible) {
  const strip = $('role-strip');
  if (strip) strip.style.display = visible ? '' : 'none';
}

function resetRoleStrip() {
  _setRoleState('role-planner',  'idle', 'Planner');
  _setRoleState('role-reviewer', 'idle', 'Reviewer');
  showRoleStrip(false);
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

let _currentBrief = null;
let _nlTasks = [];
let _nlSummary = '';
let _nlProjectKey  = '';   // repo_root used as project key

// Status chip states
const NL_STATUS = {
  drafting:           { label: 'Drafting',            mod: '' },
  generating:         { label: 'Generating…',         mod: '--generating' },
  needs_clarification:{ label: 'Needs clarification', mod: '--warning' },
  ready_to_run:       { label: 'Ready to run',        mod: '--success' },
  plan_ready:         { label: 'Plan ready',          mod: '--info' },
  plan_confirmed:     { label: 'Plan confirmed',      mod: '--success' },
};

function _nlStatusFor(brief) {
  if (!brief) return 'drafting';
  const confidence = brief.confidence_score ?? 100;
  if (brief.needs_clarification || confidence < 60) return 'needs_clarification';
  return 'ready_to_run';
}

function setNLStatusChip(status) {
  const row  = $('nl-status-row');
  const chip = $('nl-status-chip');
  if (!chip) return;
  const info = NL_STATUS[status] || NL_STATUS.drafting;
  chip.textContent = info.label;
  chip.className = `nl-status-chip${info.mod ? ' ' + info.mod : ''}`;
  if (row) row.style.display = '';
}

async function _saveNLState(status, extra = {}) {
  if (!_nlProjectKey) return;
  try {
    const body = {
      repo_root: _nlProjectKey,
      message:   $('nl-input')?.value || '',
      brief:     _currentBrief || {},
      tasks:     _nlTasks || [],
      plan_summary: _nlSummary || '',
      status,
      confidence_score: _currentBrief?.confidence_score,
      risks:            _currentBrief?.risks,
      risk_level:       _currentBrief?.risk_level,
      ...extra,
    };
    await fetch('/api/run/nl/state', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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
    
    // Clear UI before potentially restoring
    if ($('nl-input')) $('nl-input').value = '';
    _currentBrief = null;
    _nlTasks = [];
    _nlSummary = '';
    _confirmedPlanFile = '';
    setNLStatusChip('drafting');
    
    // Hide NL plan sections
    const nlPlanOutput = $('nl-plan-output');
    if (nlPlanOutput) nlPlanOutput.style.display = 'none';
    const nlConfirmed = $('nl-plan-confirmed-wrap');
    if (nlConfirmed) nlConfirmed.style.display = 'none';

    if (!state || !state.message) return;

    // Restore textarea
    if ($('nl-input')) $('nl-input').value = state.message;

    // Restore brief
    if (state.brief && state.brief.goal) {
      _currentBrief = state.brief;
      renderBrief(state.brief);
    }

    _nlTasks = state.tasks || [];
    _nlSummary = state.plan_summary || '';

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

  // Confidence badge
  const confBadge = $('nl-confidence-badge');
  if (confBadge) {
    const score = brief.confidence_score ?? 100;
    confBadge.textContent = `${score}% Confidence`;
    confBadge.style.display = '';
    confBadge.className = 'nl-confidence-badge'; // Reset
    if (score >= 80) confBadge.classList.add('--high');
    else if (score >= 60) confBadge.classList.add('--medium');
    else confBadge.classList.add('--low');
  }

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

  // Risk alert
  const riskAlert = $('nl-risk-alert');
  if (riskAlert) {
    if (brief.risks?.length) {
      riskAlert.innerHTML = `
        <div class="nl-brief-section">
          <div class="nl-brief-label">Potential Risks Detected</div>
          ${brief.risks.map(r => `<div class="nl-brief-item">• ${escHtml(r)}</div>`).join('')}
        </div>`;
      riskAlert.style.display = '';
    } else {
      riskAlert.style.display = 'none';
    }
  }

  validateSafety();
  $('nl-brief-output').style.display = '';
}

function validateSafety() {
  if (!_currentBrief) return;

  const confidence = _currentBrief.confidence_score ?? 100;
  const isConfident = confidence >= 60;
  const isClarified = !(_currentBrief.needs_clarification || _currentBrief.clarification_questions?.length);
  const riskVerified = !_currentBrief.requires_confirmation || $('f-nl-safety-ack')?.checked;

  const canProceed = isConfident && isClarified && riskVerified;

  const btnPlan = $('btn-generate-plan');
  if (btnPlan) btnPlan.disabled = !canProceed;

  // Show/hide safety wrap if risky or low confidence
  const safetyWrap = $('nl-safety-wrap');
  if (safetyWrap) {
    const shouldShow = _currentBrief.requires_confirmation || !isConfident;
    safetyWrap.style.display = shouldShow ? '' : 'none';
    
    // If low confidence and no risks, hide the alert box itself but keep wrap for clarify info
    const risks = _currentBrief.risks || [];
    const riskAlert = $('nl-risk-alert');
    if (riskAlert) riskAlert.style.display = risks.length ? '' : 'none';
  }
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
  setNLStatusChip('generating');
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

  // Update the goal textarea with the refined goal from the brief
  const nlInput = $('nl-input');
  if (nlInput && _currentBrief.goal) {
    nlInput.value = _currentBrief.goal;
  }

  // Fold constraints + acceptance criteria into the clarifications field
  const clarEl = $('f-clarifications');
  const extras = [
    ...(_currentBrief.constraints || []).map(c => `Constraint: ${c}`),
    ...(_currentBrief.acceptance_criteria || []).map(a => `Acceptance: ${a}`),
  ];
  if (extras.length && clarEl) {
    const existing = clarEl.value.trim() || '';
    clarEl.value = existing ? `${existing}\n${extras.join('\n')}` : extras.join('\n');
    // Auto-open advanced accordion
    const trigger = $('adv-trigger');
    const body    = $('adv-body');
    if (trigger?.getAttribute('aria-expanded') !== 'true') {
      trigger?.setAttribute('aria-expanded', 'true');
      body?.classList.remove('--hidden');
    }
  }

  updateCommandPreview();
  await _saveNLState('ready_to_run');
  toast('Goal updated from brief — generate a plan or launch directly.', 'success');
}

// ── Plan generation ───────────────────────────────────────────────────────────

let _confirmedPlanFile = '';

function renderNLTaskList(tasks, summary) {
  _nlTasks = tasks || [];
  _nlSummary = summary || '';
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
  setNLStatusChip('generating');
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
  const nlInput = $('nl-input');
  if (nlInput && s.goal) nlInput.value = s.goal;
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
    goal:               ($('nl-input')?.value || '').trim(),
    repo_root:          $('f-repo-root').value.trim(),
    aider_model:        $('f-aider-model').value.trim(),
    supervisor:         sup,
    manual_supervisor:  true,  // Universal pipeline: all runs use --manual-supervisor
    supervisor_command: sup === 'custom' ? $('f-supervisor-command').value.trim()
                      : (SUPERVISOR_CMDS[sup] || ''),
    dry_run:            $('f-dry-run').checked,
    auto_commit:        ($('sb-auto-commit')?.checked !== false),
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
  // Universal pipeline: all runs use --manual-supervisor
  parts.push({ cls: 'cmd-flag', text: '--manual-supervisor' });
  if (!s.auto_commit)
    parts.push({ cls: 'cmd-flag', text: '--no-auto-commit' });
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
  _incrementLogBadge();
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
    // Keep chatbot wizard interactive elements active during run
    if (el.closest('#chatbot-relay-panel')) return;
    // Keep supervisor radios interactive mid-run (universal pipeline: mid-run switch)
    if (el.name === 'supervisor') return;
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
    .on('start',      () => {
      showBanner('running', 'Run in progress…');
      _setRoleState('role-planner', 'active', 'Planner — generating...');
      showRoleStrip(true);
      switchRunTab('log');
    })
    .on('complete', d => {
      const ok  = d.status === 'success';
      const sec = d.elapsed ? ` in ${d.elapsed}s` : '';
      showBanner(ok ? 'success' : 'failure',
        ok ? `Run completed successfully${sec}.` : `Run finished with failures${sec}.`);
      setRunning(false);
      _setRoleState('role-reviewer', 'done', 'Reviewer');
      _sse.disconnect();
    })
    .on('error', d => {
      showBanner('failure', `Error: ${d.message || 'unknown error'}`);
      setRunning(false);
      _sse.disconnect();
      // Don't auto-switch — user may want to read the log
    })
    .on('stopped', () => {
      showBanner('stopped', 'Run stopped.');
      setRunning(false);
      _sse.disconnect();
    })
    .on('supervisor_review_requested', d => {
      const tid = d.task_id || '?';
      if (d.manual) {
        appendLog(`[proxy] Task ${tid}: manual review required`);
      } else {
        appendLog(`[proxy] Task ${tid}: chatbot review required — copy the prompt above`);
      }
      // Reviewer role is active
      _setRoleState('role-reviewer', 'active', `Reviewer — task ${tid}`);
    })
    .on('supervisor_review_submitted', d => {
      const tid = d.task_id || '?';
      const dec = d.decision?.decision || 'pass';
      const label = dec === 'pass' ? 'PASSED' : `REWORK: ${d.decision?.instruction || ''}`;
      appendLog(`[proxy] Task ${tid}: supervisor auto-reviewed — ${label}`);
    })
    .on('plan_ready', d => {
      const n = d.task_count || d.total_tasks || '?';
      appendLog(`[bridge] Plan ready — ${n} tasks`);
      // Planner role done, reviewer role starts
      _setRoleState('role-planner', 'done', 'Planner');
      _setRoleState('role-reviewer', 'active', 'Reviewer');
      showRoleStrip(true);
    })
    .on('planner_active', d => {
      _setRoleState('role-planner', 'active', `Planner — ${d.task_count || '?'} tasks`);
      showRoleStrip(true);
    })
    .on('planner_done', () => {
      _setRoleState('role-planner', 'done', 'Planner');
    })
    .on('reviewer_active', d => {
      const idx = d.task_index || '?';
      const tot = d.task_total || '?';
      const title = d.task_title ? ` — ${d.task_title}` : '';
      _setRoleState('role-reviewer', 'active', `Reviewer — task ${idx}/${tot}${title}`);
    })
    .on('reviewer_done', d => {
      const dec = d.decision || '';
      _setRoleState('role-reviewer', 'done', 'Reviewer');
    })
    .connect();
}

// ── Launch ────────────────────────────────────────────────────────────────────

async function launchRun() {
  const s = collectSettings();
  if (!s.goal) {
    toast('Please enter a goal / instruction.', 'warning');
    $('nl-input')?.focus();
    return;
  }

  clearLog();
  hideBanner();
  resetRoleStrip();
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

async function launchNLRun() {
  if (!_nlProjectKey) {
    const settings = await fetch('/api/settings').then(r => r.json());
    _nlProjectKey = settings.repo_root || '';
  }
  if (!_nlProjectKey) {
    toast('No project folder configured.', 'error');
    return;
  }

  clearLog();
  hideBanner();
  resetRoleStrip();
  setRunning(true);
  connectSSE();
  // Tab switches to Log via SSE 'start' event — no manual switch here

  try {
    play('launch');
    const res = await fetch('/api/run/nl/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_root: _nlProjectKey }),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Failed to start run.');

    // Update session state so we know which run was last launched
    await _saveNLState('running', { last_run_id: data.run_id });
  } catch (err) {
    showBanner('failure', err.message || 'Failed to start run.');
    setRunning(false);
    _sse?.disconnect();
    toast(err.message || 'Failed to start run.', 'error');
  }
}

// ── Chatbot Relay Wizard ──────────────────────────────────────────────────────
// Ported from relay.js — handles the inline copy-paste supervisor flow
// All IDs are prefixed with "chatbot-" to avoid conflicts with run page IDs.

let _cbStep = 1;
let _cbTasks = [];
let _cbCurrentTaskId = null;
let _cbCompletedTasks = 0;
let _cbTotalTasks = 0;
let _cbLiveRunActive = false;
let _cbRelaySessionId = '';

function _cbExecTaskCount(tasks = _cbTasks) {
  return (tasks || []).filter(t => String(t?.status || '').toLowerCase() !== 'skipped').length;
}

function _cbGoToStep(n, force = false) {
  if (!force && n === 1 && _cbTasks.length > 0) {
    toast('Use "Discard Tasks" to return to Step 1.', 'warning');
    return;
  }
  _cbStep = n;
  for (let i = 1; i <= 3; i++) {
    const panel = $(`chatbot-panel-${i}`);
    if (panel) panel.style.display = i === n ? '' : 'none';
    const ind = $(`chatbot-step-indicator-${i}`);
    if (ind) {
      ind.dataset.active = String(i === n);
      ind.dataset.done   = String(i < n);
    }
  }
}

function _cbUpdateProgress(done, total) {
  _cbCompletedTasks = done;
  _cbTotalTasks     = total;
  const pct = total > 0 ? Math.round(done / total * 100) : 0;
  const bar = $('chatbot-progress-bar');
  const lbl = $('chatbot-progress-label');
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = `${done} / ${total}`;
}

function _cbSetStatus(status, label) {
  const chip = $('chatbot-status-chip');
  const lbl  = $('chatbot-status-label');
  if (chip) chip.dataset.status = status;
  if (lbl)  lbl.textContent     = label;
}

function _cbUpdateControls() {
  const stopBtn    = $('chatbot-btn-stop');
  const submitBtn  = $('chatbot-btn-submit-decision');
  const confirmBtn = $('chatbot-btn-confirm-tasks');
  const discardBtn = $('chatbot-btn-back-to-step1');
  if (stopBtn)    stopBtn.disabled    = !_cbLiveRunActive;
  if (submitBtn)  submitBtn.disabled  = !_cbLiveRunActive;
  if (confirmBtn) confirmBtn.disabled = _cbLiveRunActive || _cbExecTaskCount() === 0;
  if (discardBtn) discardBtn.disabled = _cbLiveRunActive;
  document.querySelectorAll('[data-relay-task-action]').forEach(btn => {
    btn.disabled = _cbLiveRunActive;
  });
}

async function _cbGeneratePrompt() {
  if (_cbTasks.length > 0) {
    toast('Discard current tasks before generating a new plan.', 'warning');
    return;
  }
  const goal     = ($('nl-input')?.value || '').trim();
  const repoRoot = ($('f-repo-root')?.value || '').trim();
  if (!goal) { toast('Please enter a goal first.', 'warning'); $('nl-input')?.focus(); return; }

  const btn = $('chatbot-btn-generate-prompt');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  try {
    const data = await apiPost('/api/relay/generate-prompt', { goal, repo_root: repoRoot });
    const box  = $('chatbot-prompt-output');
    if (box) box.textContent = data.prompt;
    const wrap = $('chatbot-prompt-output-wrap');
    if (wrap) wrap.style.display = '';
    const pasteWrap = $('chatbot-plan-paste-wrap');
    if (pasteWrap) pasteWrap.style.display = '';
  } catch (err) {
    toast(err.message || 'Failed to generate prompt.', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Generate Prompt'; }
  }
}

async function _cbImportPlan() {
  if (_cbTasks.length > 0) {
    toast('Discard current tasks before importing a new plan.', 'warning');
    return;
  }
  const raw   = ($('chatbot-plan-paste')?.value || '').trim();
  const errEl = $('chatbot-import-plan-error');
  if (errEl) errEl.style.display = 'none';
  if (!raw) { toast('Please paste the AI response first.', 'warning'); return; }

  const btn = $('chatbot-btn-import-plan');
  if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }
  try {
    const data = await apiPost('/api/relay/import-plan', { raw_text: raw });
    _cbTasks          = data.tasks || [];
    _cbRelaySessionId = data.relay_session_id || _cbRelaySessionId;
    _cbCompletedTasks = 0;
    _cbTotalTasks     = _cbExecTaskCount(_cbTasks);
    _cbRenderTaskList(_cbTasks);
    _cbUpdateProgress(0, _cbTotalTasks);
    _cbGoToStep(2);
    _cbUpdateControls();
  } catch (err) {
    const msg = err.message || 'Failed to parse plan.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Import Plan'; }
  }
}

function _cbRenderTaskList(tasks) {
  const list = $('chatbot-relay-task-list');
  if (!list) return;
  if (!tasks?.length) {
    list.innerHTML = '<p class="text-subtle" style="font-size:var(--font-size-sm)">No tasks found.</p>';
    return;
  }
  list.innerHTML = tasks.map(t => {
    const type        = String(t.type || 'modify').toLowerCase();
    const status      = String(t.status || 'not_started').toLowerCase();
    const statusLabel = String(t.status_label || 'Not started');
    const canSkip     = !['running','waiting_review','approved','success','skipped'].includes(status);
    const canRestore  = status === 'skipped';
    const actionHtml  = canSkip
      ? `<button class="btn btn--secondary btn--sm relay-task-action" data-relay-task-action="skip" data-task-id="${escHtml(t.id)}">Skip</button>`
      : canRestore
      ? `<button class="btn btn--secondary btn--sm relay-task-action" data-relay-task-action="restore" data-task-id="${escHtml(t.id)}">Restore</button>`
      : '';
    return `
    <div class="relay-task-item" data-status="${escHtml(status)}">
      <div class="relay-task-num">${t.id}</div>
      <div class="relay-task-body">
        <div class="relay-task-head">
          <div class="relay-task-title">
            <span>${escHtml(t.title || '')}</span>
            <span class="relay-task-type-badge" data-type="${escHtml(type)}">${escHtml(type)}</span>
            <span class="relay-task-status-badge" data-status="${escHtml(status)}">${escHtml(statusLabel)}</span>
          </div>
          <div class="relay-task-actions">${actionHtml}</div>
        </div>
        <div class="relay-task-instruction">${escHtml(t.instruction || '')}</div>
        ${t.files?.length ? `<div class="relay-task-files">${t.files.map(escHtml).join(', ')}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  _cbUpdateControls();
}

async function _cbToggleSkip(taskId, skip) {
  if (_cbLiveRunActive) { toast('Stop the run before changing skipped tasks.', 'warning'); return; }
  try {
    const data = await apiPost('/api/relay/tasks/skip', { task_id: taskId, skip });
    _cbTasks          = data.tasks || [];
    _cbRelaySessionId = data.relay_session_id || _cbRelaySessionId;
    _cbCompletedTasks = data.completed_tasks || 0;
    _cbTotalTasks     = data.total_tasks || _cbExecTaskCount(_cbTasks);
    _cbRenderTaskList(_cbTasks);
    _cbUpdateProgress(_cbCompletedTasks, _cbTotalTasks);
    toast(skip ? `Task ${taskId} skipped.` : `Task ${taskId} restored.`, 'success');
  } catch (err) {
    toast(err.message || 'Could not update task.', 'error');
  }
}

async function _cbDiscardAndReturnToPlan() {
  if (_cbLiveRunActive) { toast('Stop the run before discarding tasks.', 'warning'); return; }
  _cbRelaySessionId = '';
  _cbTasks          = [];
  _cbCurrentTaskId  = null;
  _cbCompletedTasks = 0;
  _cbTotalTasks     = 0;
  const taskList = $('chatbot-relay-task-list');
  if (taskList) taskList.innerHTML = '';
  [$('chatbot-done-panel'), $('chatbot-review-panel')].forEach(el => {
    if (el) el.style.display = 'none';
  });
  await fetch('/api/relay/state', { method: 'DELETE' }).catch(() => {});
  _cbSetStatus('idle', 'Ready');
  _cbUpdateProgress(0, 0);
  _cbGoToStep(1, true);
  _cbUpdateControls();
  toast('Tasks discarded.', 'success');
}

async function _cbLaunchRun() {
  const settings = {
    goal:              ($('nl-input')?.value || '').trim(),
    repo_root:         ($('f-repo-root')?.value || '').trim(),
    aider_model:       ($('f-aider-model')?.value || 'ollama/mistral').trim(),
    supervisor:        'chatbot',
    manual_supervisor: true,
    workflow_profile:  'standard',
    max_task_retries:  Math.max(0, parseInt($('f-max-retries')?.value || 2, 10) || 2),
    relay_session_id:  _cbRelaySessionId,
  };
  _cbGoToStep(3);
  _cbLiveRunActive = true;
  _cbUpdateControls();
  _cbSetStatus('running', 'Running…');
  _cbUpdateProgress(0, _cbExecTaskCount());

  clearLog();
  hideBanner();
  resetRoleStrip();
  setRunning(true);

  if (_sse) _sse.disconnect();
  _sse = new SSEClient('/api/run/stream');
  _sse
    .on('log',                 d => appendLog(d.line || ''))
    .on('review_required',     d => _cbOnReviewNeeded(d))
    .on('progress',  d => _cbUpdateProgress(d.completed, d.total))
    .on('plan_ready', d => { _cbTotalTasks = d.task_count || 0; _cbUpdateProgress(0, _cbTotalTasks); })
    .on('complete',  d => _cbOnRunComplete(d))
    .on('error',     d => _cbOnRunComplete({ status: 'failure', message: d.message }))
    .on('stopped',   () => _cbOnRunComplete({ status: 'stopped' }))
    .connect();

  try {
    play('launch');
    await apiPost('/api/run', settings);
  } catch (err) {
    _cbSetStatus('failure', 'Failed to start');
    toast(err.message || 'Failed to start run.', 'error');
    _sse?.disconnect();
    setRunning(false);
    _cbLiveRunActive = false;
    _cbUpdateControls();
  }
}

async function _cbOnReviewNeeded(data) {
  _cbCurrentTaskId = data.task_id;
  _cbSetStatus('waiting_review', 'Waiting for review…');
  _cbGoToStep(3);

  const tidEl    = $('chatbot-review-task-id');
  const attBadge = $('chatbot-attempt-badge');
  if (tidEl)    tidEl.textContent    = _cbCurrentTaskId;
  if (attBadge) attBadge.textContent = `attempt ${data.attempt || 1}`;

  try {
    const params = new URLSearchParams({
      task_id:          _cbCurrentTaskId,
      repo_root:        ($('f-repo-root')?.value || '').trim(),
      goal:             ($('nl-input')?.value || '').trim(),
      relay_session_id: _cbRelaySessionId,
    });
    const resp    = await fetch(`/api/relay/review-packet?${params}`);
    const payload = await resp.json();
    if (resp.ok) {
      const box = $('chatbot-review-packet');
      if (box) box.textContent = payload.packet;
    }
  } catch (_) {}

  const decPaste = $('chatbot-decision-paste');
  if (decPaste) decPaste.value = '';
  const decErr = $('chatbot-decision-error');
  if (decErr) decErr.style.display = 'none';
  const replanWrap = $('chatbot-replan-wrap');
  if (replanWrap) replanWrap.style.display = 'none';

  const panel = $('chatbot-review-panel');
  if (panel) panel.style.display = '';
}

async function _cbSubmitDecision() {
  if (!_cbLiveRunActive) { toast('No active run.', 'warning'); return; }
  const raw   = ($('chatbot-decision-paste')?.value || '').trim();
  const errEl = $('chatbot-decision-error');
  if (errEl) errEl.style.display = 'none';
  if (!raw)  { toast("Please paste the AI's decision.", 'warning'); return; }

  const btn = $('chatbot-btn-submit-decision');
  if (btn) { btn.disabled = true; btn.textContent = 'Submitting…'; }
  try {
    const data = await apiPost('/api/relay/submit-decision', {
      raw_text:         raw,
      task_id:          _cbCurrentTaskId,
      repo_root:        ($('f-repo-root')?.value || '').trim(),
      relay_session_id: _cbRelaySessionId,
    });
    if (data.decision === 'fail') {
      await _cbLoadReplanPrompt();
    } else {
      const panel = $('chatbot-review-panel');
      if (panel) panel.style.display = 'none';
      _cbSetStatus('running', 'Running…');
      toast(`Decision submitted: ${data.decision}`, 'success');
    }
  } catch (err) {
    const msg = err.message || 'Failed to submit decision.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Submit Decision'; }
  }
}

async function _cbLoadReplanPrompt() {
  try {
    const data = await apiPost('/api/relay/replan-prompt', {
      task_id:          _cbCurrentTaskId,
      repo_root:        ($('f-repo-root')?.value || '').trim(),
      goal:             ($('nl-input')?.value || '').trim(),
      relay_session_id: _cbRelaySessionId,
      failed_reason:    ($('chatbot-decision-paste')?.value || '').replace(/^FAILED:\s*/i, '').trim(),
    });
    const box  = $('chatbot-replan-packet');
    if (box) box.textContent = data.prompt;
    const wrap = $('chatbot-replan-wrap');
    if (wrap) wrap.style.display = '';
  } catch (err) {
    toast('Could not generate replan prompt: ' + err.message, 'error');
  }
}

async function _cbSubmitReplan() {
  if (!_cbLiveRunActive) { toast('No active run.', 'warning'); return; }
  const raw   = ($('chatbot-replan-paste')?.value || '').trim();
  const errEl = $('chatbot-replan-error');
  if (errEl) errEl.style.display = 'none';
  if (!raw)  { toast('Please paste the replacement tasks.', 'warning'); return; }

  const btn = $('chatbot-btn-submit-replan');
  if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }
  try {
    const data = await apiPost('/api/relay/import-replan', {
      raw_text: raw,
      task_id:  _cbCurrentTaskId,
    });
    _cbTasks      = data.tasks || [];
    _cbTotalTasks = _cbExecTaskCount(_cbTasks);
    _cbRenderTaskList(_cbTasks);
    _cbUpdateProgress(_cbCompletedTasks, _cbTotalTasks);
    toast(`Replan imported: ${data.count} tasks remaining.`, 'success');
    const panel = $('chatbot-review-panel');
    if (panel) panel.style.display = 'none';
    _cbSetStatus('running', 'Running…');
  } catch (err) {
    const msg = err.message || 'Failed to import replan.';
    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    toast(msg, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Import Replacement Tasks'; }
  }
}

function _cbOnRunComplete(data) {
  const status  = data.status || 'failure';
  const elapsed = data.elapsed ? ` in ${data.elapsed}s` : '';
  _cbLiveRunActive = false;
  _cbUpdateControls();
  _cbSetStatus(status, _cbStatusLabel(status));
  setRunning(false);
  if (_sse) { _sse.disconnect(); _sse = null; }

  const panel = $('chatbot-review-panel');
  if (panel) panel.style.display = 'none';
  const done = $('chatbot-done-panel');
  if (done)  done.style.display = '';

  const icon = done?.querySelector('.relay-done-icon');
  if (icon) icon.dataset.failed = String(status === 'failure');
  const title = $('chatbot-done-title');
  const sub   = $('chatbot-done-sub');
  if (title) title.textContent = status === 'success' ? 'Run complete!' : status === 'stopped' ? 'Run stopped' : 'Run failed';
  if (sub)   sub.textContent   = `${_cbCompletedTasks} / ${_cbTotalTasks} tasks completed${elapsed}.`;
}

function _cbStatusLabel(status) {
  return String(status || 'idle').replace(/_/g, ' ')
    .split(' ').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
}

function _cbCopyText(elementId, btnId) {
  const text = $(elementId)?.textContent || '';
  navigator.clipboard?.writeText(text).then(() => {
    const btn = $(btnId);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    }
  }).catch(() => toast('Copy failed — please copy manually.', 'warning'));
}

function _cbReset() {
  _cbStep           = 1;
  _cbTasks          = [];
  _cbCurrentTaskId  = null;
  _cbCompletedTasks = 0;
  _cbTotalTasks     = 0;
  _cbLiveRunActive  = false;
  _cbRelaySessionId = '';
  if (_sse) { _sse.disconnect(); _sse = null; }
  ['chatbot-plan-paste','chatbot-decision-paste','chatbot-replan-paste'].forEach(id => {
    const el = $(id);
    if (el) el.value = '';
  });
  ['chatbot-prompt-output-wrap','chatbot-plan-paste-wrap','chatbot-review-panel',
   'chatbot-done-panel','chatbot-replan-wrap','chatbot-import-plan-error',
   'chatbot-decision-error','chatbot-replan-error'].forEach(id => {
    const el = $(id);
    if (el) el.style.display = 'none';
  });
  const log = $('chatbot-log');
  if (log) log.textContent = '';
  fetch('/api/relay/state', { method: 'DELETE' }).catch(() => {});
  _cbGoToStep(1, true);
  _cbUpdateControls();
}

function _cbShowOrHide() {
  const sup   = document.querySelector('input[name="supervisor"]:checked')?.value || '';
  const panel = $('chatbot-relay-panel');
  if (panel) panel.style.display = sup === 'chatbot' ? '' : 'none';
}

function bindChatbotControls() {
  $('chatbot-btn-generate-prompt')?.addEventListener('click',   _cbGeneratePrompt);
  $('chatbot-btn-copy-prompt')?.addEventListener('click',       () => _cbCopyText('chatbot-prompt-output', 'chatbot-btn-copy-prompt'));
  $('chatbot-btn-import-plan')?.addEventListener('click',       _cbImportPlan);
  $('chatbot-btn-back-to-step1')?.addEventListener('click',     _cbDiscardAndReturnToPlan);
  $('chatbot-btn-confirm-tasks')?.addEventListener('click',     _cbLaunchRun);
  $('chatbot-relay-task-list')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-relay-task-action]');
    if (!btn) return;
    const taskId = parseInt(btn.dataset.taskId || '0', 10);
    if (!taskId) return;
    void _cbToggleSkip(taskId, btn.dataset.relayTaskAction === 'skip');
  });
  $('chatbot-btn-stop')?.addEventListener('click', async () => {
    if (!_cbLiveRunActive) { toast('No active run.', 'warning'); return; }
    try { await fetch('/api/run/stop', { method: 'POST' }); } catch (_) {}
  });
  $('chatbot-btn-copy-packet')?.addEventListener('click',   () => _cbCopyText('chatbot-review-packet', 'chatbot-btn-copy-packet'));
  $('chatbot-btn-submit-decision')?.addEventListener('click', _cbSubmitDecision);
  $('chatbot-btn-copy-replan')?.addEventListener('click',   () => _cbCopyText('chatbot-replan-packet', 'chatbot-btn-copy-replan'));
  $('chatbot-btn-submit-replan')?.addEventListener('click',  _cbSubmitReplan);
  $('chatbot-btn-new-run')?.addEventListener('click',        _cbReset);
}

// ── Bind controls ─────────────────────────────────────────────────────────────

function bindControls() {
  // NL brief controls
  $('btn-generate-brief')?.addEventListener('click', generateBrief);

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

  let saveTimeout = null;
  $('nl-input')?.addEventListener('input', () => {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => _saveNLState('drafting'), 1000);
  });

  $('nl-input')?.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      generateBrief();
    }
  });

  window.addEventListener('bridge:project-switched', async e => {
    const newPath = e.detail?.path;
    if (newPath && newPath !== _nlProjectKey) {
      _nlProjectKey = newPath;
      // We might want to clear existing UI if switching projects while on Run page
      _currentBrief = null;
      _nlTasks = [];
      _nlSummary = '';
      _confirmedPlanFile = '';
      
      // Optionally reset form if it doesn't match the new project root in settings
      // but usually settings are updated by the project switcher already.
      await _restoreNLState();
    }
  });

  // Launch / stop
  $('btn-launch-run')?.addEventListener('click', launchRun);
  $('btn-launch-nl-run')?.addEventListener('click', launchNLRun);
  $('f-nl-safety-ack')?.addEventListener('change', validateSafety);
  $('btn-stop-run')?.addEventListener('click', async () => {
    try {
      await apiPost('/api/run/stop');
    } catch (err) {
      toast(err.message || 'Stop failed.', 'error');
    }
  });

  // Ctrl+Enter from anywhere outside the goal textarea launches the run
  // (the goal textarea itself uses Ctrl+Enter for generateBrief)
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !_isRunning
        && document.activeElement?.id !== 'nl-input') {
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

  // Supervisor radio → show/hide custom input, chatbot wizard, update preview, mid-run switch
  document.querySelectorAll('input[name="supervisor"]').forEach(r => {
    r.addEventListener('change', async () => {
      const isCustom = r.value === 'custom';
      const wrap = $('supervisor-custom-wrap');
      if (wrap) wrap.style.display = isCustom ? '' : 'none';
      updateCommandPreview();
      updateCompatWarnings();
      _cbShowOrHide();

      // Mid-run supervisor switch via universal pipeline proxy
      if (_isRunning) {
        try {
          const body = { supervisor: r.value };
          if (isCustom) body.supervisor_command = $('f-supervisor-command')?.value?.trim() || '';
          await apiPost('/api/run/supervisor', body);
          toast(`Supervisor switched to ${r.value}. Takes effect on next review.`, 'info');
        } catch (err) {
          toast(err.message || 'Failed to switch supervisor.', 'error');
        }
      }
    });
  });
  $('f-supervisor-command')?.addEventListener('input', updateCommandPreview);

  // All form inputs → live preview update
  ['nl-input','f-repo-root','f-aider-model','f-dry-run','f-validation-cmd',
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

  // Tab switching
  document.querySelectorAll('#run-tabs .tab').forEach(btn => {
    btn.addEventListener('click', () => switchRunTab(btn.dataset.tab));
  });

  // Chatbot wizard controls
  bindChatbotControls();
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

    // Switch to log tab since there's run data to show
    switchRunTab('log');

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

  // Show/hide chatbot wizard based on saved supervisor setting
  _cbShowOrHide();
  _cbUpdateControls();

  // Restore NL conversation state if present
  await _restoreNLState();

  // If a run is already active, show live state
  await hydrateExistingRun();
  updateCompatWarnings();
}

init();
