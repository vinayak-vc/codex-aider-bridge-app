#!/usr/bin/env node
/**
 * postinstall.js — runs automatically after: npm install -g bridge-mcp-server
 *
 * Does three things:
 *   1. Clones (or updates) the bridge Python runtime to ~/.bridge/
 *   2. Copies the Claude Code skill to ~/.claude/skills/codex-aider-bridge/
 *   3. Writes the MCP server entry + BRIDGE_ROOT into ~/.claude/settings.json
 */

import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const REPO_URL   = 'https://github.com/vinayak-vc/codex-aider-bridge-app.git';
const BRIDGE_DIR = path.join(os.homedir(), '.bridge');
const SKILL_SRC  = path.join(BRIDGE_DIR, '.claude', 'skills', 'codex-aider-bridge');
const SKILL_DEST = path.join(os.homedir(), '.claude', 'skills', 'codex-aider-bridge');
const CLAUDE_CFG = path.join(os.homedir(), '.claude', 'settings.json');

function run(cmd, opts = {}) {
  execSync(cmd, { stdio: 'inherit', ...opts });
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

// ── Step 1: Clone or update the bridge repo ───────────────────────────────────

console.log('\n[bridge] Setting up bridge runtime...');

if (fs.existsSync(path.join(BRIDGE_DIR, 'main.py'))) {
  console.log(`[bridge] Updating existing clone at ${BRIDGE_DIR}`);
  try { run(`git -C "${BRIDGE_DIR}" pull --ff-only --quiet`); }
  catch { console.log('[bridge] Pull skipped (local changes or no network).'); }
} else {
  console.log(`[bridge] Cloning bridge repo to ${BRIDGE_DIR}`);
  run(`git clone --depth 1 "${REPO_URL}" "${BRIDGE_DIR}"`);
}

// ── Step 2: Install the Claude Code skill ─────────────────────────────────────

console.log('[bridge] Installing Claude Code skill...');
fs.mkdirSync(path.dirname(SKILL_DEST), { recursive: true });
if (fs.existsSync(SKILL_SRC)) {
  copyDir(SKILL_SRC, SKILL_DEST);
  console.log(`[bridge] Skill installed at ${SKILL_DEST}`);
} else {
  console.warn('[bridge] Skill source not found in cloned repo — skipping.');
}

// ── Step 3: Write MCP config into ~/.claude/settings.json ────────────────────

console.log('[bridge] Configuring Claude Code MCP server...');
fs.mkdirSync(path.dirname(CLAUDE_CFG), { recursive: true });

let settings = {};
if (fs.existsSync(CLAUDE_CFG)) {
  try { settings = JSON.parse(fs.readFileSync(CLAUDE_CFG, 'utf-8')); }
  catch { console.warn('[bridge] Could not parse existing settings.json — will overwrite mcpServers entry only.'); }
}

settings.mcpServers = settings.mcpServers ?? {};
settings.mcpServers['bridge-mcp-server'] = {
  command: 'npx',
  args:    ['-y', 'bridge-mcp-server'],
  env:     { BRIDGE_ROOT: BRIDGE_DIR },
};

fs.writeFileSync(CLAUDE_CFG, JSON.stringify(settings, null, 2));
console.log(`[bridge] MCP server registered in ${CLAUDE_CFG}`);

// ── Done ──────────────────────────────────────────────────────────────────────

console.log(`
╔══════════════════════════════════════════════════════════╗
║           bridge-mcp-server setup complete               ║
╠══════════════════════════════════════════════════════════╣
║  Runtime  → ${BRIDGE_DIR.padEnd(44)}║
║  Skill    → ${SKILL_DEST.padEnd(44)}║
╠══════════════════════════════════════════════════════════╣
║  NEXT STEP: Restart Claude Code                          ║
║  Then open any project and type: /codex-aider-bridge     ║
╚══════════════════════════════════════════════════════════╝
`);
