// core/help.js — Per-page contextual help modal

const HELP_CONTENT = {
  dashboard: {
    title: 'Dashboard',
    sections: [
      {
        heading: 'What you see here',
        body: 'The Dashboard is your live run monitor. It shows real-time status of every task the bridge is executing — what\'s running, what passed, what needs rework.'
      },
      {
        heading: 'Status ring',
        body: 'The progress ring shows overall completion. The percentage updates automatically as tasks complete. The colour changes: blue = running, green = success, red = failed.'
      },
      {
        heading: 'Task feed',
        body: 'Each card in the task feed represents one task. Green = approved, yellow = rework requested, red = failed. Click a card to expand its details and diff.'
      },
      {
        heading: 'Review banner',
        body: 'When a task needs human review (manual supervisor mode), a yellow banner appears. Click "Review Changes" to open the diff panel, then choose Approve, Rework, or Fail.'
      },
      {
        heading: 'Controls',
        body: 'Pause/Resume temporarily halts execution between tasks (not mid-task). Stop terminates the run immediately. "New Run" takes you to the Run page.'
      },
    ]
  },
  run: {
    title: 'Run',
    sections: [
      {
        heading: 'What this page does',
        body: 'Configure and launch a bridge run. The bridge uses an AI supervisor to plan tasks, then Aider to execute code changes in your repository.'
      },
      {
        heading: 'Goal / Instruction',
        body: 'Describe what you want built or changed in plain English. Be specific: "Add JWT authentication to the login endpoint, store tokens in Redis, add /auth/refresh route."'
      },
      {
        heading: 'Repository Root',
        body: 'Full path to your git repository. Aider will make code changes here. Example: D:\\MyProject'
      },
      {
        heading: 'Supervisor',
        body: 'The supervisor reviews each task\'s output and decides approve/rework/fail.\n• Claude / Codex / Cursor — AI supervisors (need the CLI installed)\n• Manual — you review each task yourself via the Dashboard review panel\n• AI Relay — paste tasks into any web AI (no API key needed)\n• Custom — any CLI command that outputs decisions'
      },
      {
        heading: 'Aider Model',
        body: 'The AI model Aider uses to write code. Format: ollama/modelname for local models (e.g. ollama/qwen2.5-coder:14b). Local models are free and private.'
      },
      {
        heading: 'Live log & stdin input',
        body: 'The log terminal shows real-time output from the bridge process. If the process asks you a question (e.g. "Press Enter to continue"), type your reply in the input box below the log and press Enter.'
      },
      {
        heading: 'Dry Run',
        body: 'Simulates the full run without making any file changes. Use this to preview the task plan before committing to execution.'
      },
    ]
  },
  chat: {
    title: 'Chat',
    sections: [
      {
        heading: 'What this page does',
        body: 'Chat with an AI assistant about your project. Ask questions, plan features, debug issues, or get architecture advice.'
      },
      {
        heading: 'Requirements',
        body: 'Chat uses your locally running Ollama instance. Ollama must be running (ollama serve) and you must have at least one model pulled (ollama pull mistral).'
      },
      {
        heading: 'Model selector',
        body: 'The dropdown at the top shows all models currently pulled in Ollama. Switch models mid-conversation — the full history is sent with each message so the new model has context.'
      },
      {
        heading: 'Project context',
        body: 'If a repository is configured in Run settings, the assistant loads project knowledge (file list, README, etc.) as context. It knows your codebase.'
      },
      {
        heading: 'History per project',
        body: 'Conversation history is saved in your browser per project. Switch projects in the top bar and you get that project\'s chat history. Clear with the Clear button.'
      },
      {
        heading: 'Limitations',
        body: 'The assistant CANNOT edit files directly — use the Run tab for that. It has no internet access. It runs fully locally on your machine. Responses reflect the model\'s training data plus your project context.'
      },
    ]
  },
  knowledge: {
    title: 'Knowledge',
    sections: [
      {
        heading: 'What this page does',
        body: 'Manage the project knowledge that the AI uses as context. Knowledge is extracted from your repository and given to the supervisor and chat assistant.'
      },
      {
        heading: 'How knowledge is built',
        body: 'Bridge scans your repository for README files, package.json, pyproject.toml, directory structure, and key source files. This gives the AI a map of your project.'
      },
      {
        heading: 'Custom knowledge',
        body: 'You can add custom notes — architecture decisions, coding conventions, "never do X", team context. This is the most powerful way to guide the AI.'
      },
    ]
  },
  history: {
    title: 'History',
    sections: [
      {
        heading: 'What this page does',
        body: 'Browse all past bridge runs — their goals, status, task counts, and logs.'
      },
      {
        heading: 'Re-running',
        body: 'Click any history entry to see the full log. Use the goal text as a starting point for a new Run.'
      },
      {
        heading: 'Cleanup',
        body: 'Delete individual entries or clear all history. History is stored locally in ui/data/history.json.'
      },
    ]
  },
  tokens: {
    title: 'Tokens',
    sections: [
      {
        heading: 'What this page does',
        body: 'See how many AI tokens have been used across all runs, and how much has been saved by the relay architecture (supervisor sees summaries, not full code).'
      },
      {
        heading: 'Token savings',
        body: 'The bridge\'s supervisor only reads compact diffs and validation results — not entire file contents. This dramatically reduces token usage compared to giving the AI full codebase access every turn.'
      },
      {
        heading: 'Sessions',
        body: 'Each bridge run is one session. Expand a session to see per-task token breakdowns.'
      },
    ]
  },
  setup: {
    title: 'Setup',
    sections: [
      {
        heading: 'What this page does',
        body: 'Check that all required tools are installed and working. Green = ready, yellow = warning, red = missing.'
      },
      {
        heading: 'Required tools',
        body: '• Aider — the AI code editing engine\n• Git — for diffing and version control\n• Ollama — for local AI models (optional but recommended)\n• Your supervisor CLI (Claude, Codex, etc.) if not using Manual or AI Relay'
      },
      {
        heading: 'First-time setup',
        body: '1. Install Aider: pip install aider-chat\n2. Install Ollama: ollama.ai\n3. Pull a model: ollama pull qwen2.5-coder:14b\n4. Set your repo path in Run settings\n5. Launch your first run!'
      },
    ]
  },
  relay: {
    title: 'AI Relay',
    sections: [
      {
        heading: 'What is AI Relay?',
        body: 'Use any web-based AI (ChatGPT Plus, Claude.ai, Gemini) as the supervisor — no API keys required. You copy-paste between this app and the web AI.'
      },
      {
        heading: 'Step 1 — Generate prompt',
        body: 'Enter your goal and repo path. Click "Generate Prompt". A structured prompt is created that asks the web AI to produce a JSON task plan.'
      },
      {
        heading: 'Step 2 — Paste into web AI',
        body: 'Open your preferred web AI, paste the prompt, wait for the JSON response, copy the entire response.'
      },
      {
        heading: 'Step 3 — Import plan',
        body: 'Paste the AI\'s response in the "Paste AI response" box and click Import Plan. Bridge parses the tasks and shows them for your review.'
      },
      {
        heading: 'Step 4 — Review loop',
        body: 'After each task executes, Bridge shows a review packet. Copy it to the web AI, get a decision (APPROVED / REWORK: ... / FAILED: ...), paste it back. Repeat until done.'
      },
    ]
  },
};

const MODAL_ID = 'help-modal';

function buildModal() {
  if (document.getElementById(MODAL_ID)) return;
  const el = document.createElement('div');
  el.id = MODAL_ID;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-labelledby', 'help-modal-title');
  el.innerHTML = `
    <div class="help-modal-backdrop"></div>
    <div class="help-modal-panel">
      <div class="help-modal-header">
        <span class="help-modal-title" id="help-modal-title"></span>
        <button class="btn btn--secondary btn--sm" id="help-modal-close" aria-label="Close help">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" width="14" height="14">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <div class="help-modal-body" id="help-modal-body"></div>
    </div>`;
  document.body.appendChild(el);
  el.querySelector('#help-modal-close')?.addEventListener('click', hideHelp);
  el.querySelector('.help-modal-backdrop')?.addEventListener('click', hideHelp);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideHelp(); });
}

function showHelp(page) {
  buildModal();
  const content = HELP_CONTENT[page] || HELP_CONTENT.dashboard;
  const titleEl = document.getElementById('help-modal-title');
  const bodyEl  = document.getElementById('help-modal-body');
  if (titleEl) titleEl.textContent = content.title + ' — Help';
  if (bodyEl) {
    bodyEl.innerHTML = content.sections.map(s => `
      <div class="help-section">
        <div class="help-section-heading">${s.heading}</div>
        <div class="help-section-body">${s.body.replace(/\n/g, '<br>')}</div>
      </div>`).join('');
  }
  const modal = document.getElementById(MODAL_ID);
  if (modal) { modal.style.display = ''; modal.dataset.visible = 'true'; }
}

function hideHelp() {
  const modal = document.getElementById(MODAL_ID);
  if (modal) { modal.style.display = 'none'; modal.dataset.visible = 'false'; }
}

export function initHelp() {
  buildModal();
  document.getElementById(MODAL_ID).style.display = 'none';
}

export function openHelp(page) { showHelp(page); }
