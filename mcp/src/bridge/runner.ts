/**
 * runner.ts
 * Manages a single long-running `python main.py` subprocess.
 * Only one job can run at a time (the bridge itself is single-job per repo).
 */

import { spawn, ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export interface RunFlags {
  aider_model?:          string;
  validation_command?:   string;
  auto_split_threshold?: number;
  manual_supervisor?:    boolean;
  aider_no_map?:         boolean;
  task_timeout?:         number;
  workflow_profile?:     string;
  log_level?:            string;
}

export interface JobInfo {
  pid:          number;
  repo_root:    string;
  plan_file:    string;
  log_file:     string;
  started_at:   string;
  status:       'running' | 'done' | 'failed' | 'cancelled';
  exit_code:    number | null;
}

// ── module-level state ────────────────────────────────────────────────────────

let _proc:   ChildProcess | null = null;
let _job:    JobInfo       | null = null;

export function currentJob(): JobInfo | null { return _job; }

// ── helpers ───────────────────────────────────────────────────────────────────

/** Detect bridge root — checks BRIDGE_ROOT env, ~/.bridge/, then walks up from this file */
export function detectBridgeRoot(): string {
  // 1. Explicit env variable (set by postinstall into ~/.claude/settings.json)
  const envRoot = process.env.BRIDGE_ROOT;
  if (envRoot && fs.existsSync(path.join(envRoot, 'main.py'))) return envRoot;

  // 2. Default auto-install location
  const homeClone = path.join(os.homedir(), '.bridge');
  if (fs.existsSync(path.join(homeClone, 'main.py'))) return homeClone;

  // 3. Walk up from this file (local dev / cloned repo)
  let dir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(dir, 'main.py'))) return dir;
    dir = path.dirname(dir);
  }

  throw new Error(
    'Could not locate bridge main.py.\n' +
    'Run: npm install -g bridge-mcp-server  (auto-installs everything)\n' +
    'Or set the BRIDGE_ROOT environment variable to the bridge repo path.'
  );
}

function buildArgs(goal: string, repoRoot: string, planFile: string, flags: RunFlags, extra: string[]): string[] {
  const args: string[] = [
    'main.py', goal,
    '--repo-root',   repoRoot,
    '--plan-file',   planFile,
    '--skip-onboarding-scan',
    ...extra,
  ];

  if (flags.aider_model)          args.push('--aider-model',          flags.aider_model);
  if (flags.validation_command)   args.push('--validation-command',   flags.validation_command);
  if (flags.auto_split_threshold) args.push('--auto-split-threshold', String(flags.auto_split_threshold));
  if (flags.manual_supervisor)    args.push('--manual-supervisor');
  if (flags.aider_no_map)         args.push('--aider-no-map');
  if (flags.task_timeout)         args.push('--task-timeout',         String(flags.task_timeout));
  if (flags.workflow_profile)     args.push('--workflow-profile',     flags.workflow_profile);
  if (flags.log_level)            args.push('--log-level',            flags.log_level);

  return args;
}

// ── dry run ───────────────────────────────────────────────────────────────────

export interface DryRunResult {
  valid:         boolean;
  task_count:    number;
  tasks_preview: Array<{ id: number; instruction: string }>;
  errors:        string[];
  rollback_sha:  string | null;
  raw_exit_code: number;
}

export async function runDryRun(
  repoRoot: string,
  planFile: string,
  goal = 'dry-run-check',
  flags: RunFlags = {}
): Promise<DryRunResult> {
  const bridgeRoot = detectBridgeRoot();
  const args = buildArgs(goal, repoRoot, planFile, flags, ['--dry-run', '--auto-approve']);

  return new Promise((resolve) => {
    const stdout_lines: string[] = [];
    const stderr_lines: string[] = [];

    const proc = spawn('python', args, {
      cwd:   bridgeRoot,
      stdio: ['pipe', 'pipe', 'pipe'],
      env:   { ...process.env, PYTHONUNBUFFERED: '1' },
    });

    // Feed newlines so interactive prompts auto-accept
    proc.stdin?.write('\n\n\n\n\n');
    proc.stdin?.end();

    proc.stdout?.on('data', (chunk: Buffer) => {
      stdout_lines.push(...chunk.toString().split('\n'));
    });
    proc.stderr?.on('data', (chunk: Buffer) => {
      stderr_lines.push(...chunk.toString().split('\n'));
    });

    proc.on('close', (code) => {
      const allLines = [...stdout_lines, ...stderr_lines];

      // Parse task previews from dry-run log lines
      const tasks_preview: Array<{ id: number; instruction: string }> = [];
      const taskRe = /\[dry-run\] Task (\d+): (.+)/;
      for (const line of allLines) {
        const m = line.match(taskRe);
        if (m) tasks_preview.push({ id: Number(m[1]), instruction: m[2].trim() });
      }

      // Parse rollback SHA
      let rollback_sha: string | null = null;
      const rollbackRe = /git reset --hard ([0-9a-f]{40})/;
      for (const line of allLines) {
        const m = line.match(rollbackRe);
        if (m) { rollback_sha = m[1]; break; }
      }

      // Parse error from final JSON line or ERROR log line
      const errors: string[] = [];
      const finalJson = allLines.reverse().find(l => l.trim().startsWith('{"status"'));
      if (finalJson) {
        try {
          const j = JSON.parse(finalJson);
          if (j.error) errors.push(j.error);
        } catch { /* ignore */ }
      }
      // Also collect ERROR log lines
      const errorRe = /ERROR \| bridge_app \| Bridge run failed.*?: (.+)/;
      for (const line of allLines) {
        const m = line.match(errorRe);
        if (m && !errors.includes(m[1].trim())) errors.push(m[1].trim());
      }

      resolve({
        valid:         code === 0,
        task_count:    tasks_preview.length,
        tasks_preview,
        errors,
        rollback_sha,
        raw_exit_code: code ?? -1,
      });
    });
  });
}

// ── background run ────────────────────────────────────────────────────────────

export interface StartResult {
  started:     boolean;
  pid:         number;
  log_file:    string;
  rollback_sha: string | null;
  error?:      string;
}

export function startRun(
  repoRoot:    string,
  planFile:    string,
  goal:        string,
  flags:       RunFlags = {}
): StartResult {
  if (_proc && _job?.status === 'running') {
    return { started: false, pid: _job.pid, log_file: _job.log_file, rollback_sha: null,
             error: `A job is already running (PID ${_job.pid}). Cancel it first.` };
  }

  const bridgeRoot = detectBridgeRoot();
  const logFile    = path.join(repoRoot, 'bridge_progress', 'mcp_run.log');
  fs.mkdirSync(path.dirname(logFile), { recursive: true });

  const args = buildArgs(goal, repoRoot, planFile, flags, ['--auto-approve']);
  const logStream = fs.createWriteStream(logFile, { flags: 'a' });
  logStream.write(`\n${'='.repeat(60)}\n[MCP] Job started at ${new Date().toISOString()}\n${'='.repeat(60)}\n`);

  const proc = spawn('python', args, {
    cwd:   bridgeRoot,
    stdio: ['pipe', 'pipe', 'pipe'],
    env:   { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  // Feed newlines so interactive prompts auto-accept, then close stdin
  proc.stdin?.write('\n\n\n\n\n');
  proc.stdin?.end();

  proc.stdout?.pipe(logStream, { end: false });
  proc.stderr?.pipe(logStream, { end: false });

  _proc = proc;
  _job  = {
    pid:        proc.pid!,
    repo_root:  repoRoot,
    plan_file:  planFile,
    log_file:   logFile,
    started_at: new Date().toISOString(),
    status:     'running',
    exit_code:  null,
  };

  proc.on('close', (code) => {
    if (_job) {
      _job.status    = code === 0 ? 'done' : 'failed';
      _job.exit_code = code;
    }
    logStream.write(`\n[MCP] Job finished — exit ${code} at ${new Date().toISOString()}\n`);
    logStream.end();
    _proc = null;
  });

  return { started: true, pid: proc.pid!, log_file: logFile, rollback_sha: null };
}

// ── cancel ────────────────────────────────────────────────────────────────────

export function cancelRun(): { cancelled: boolean; pid: number | null; error?: string } {
  if (!_proc || !_job) {
    return { cancelled: false, pid: null, error: 'No job is currently running.' };
  }
  const pid = _job.pid;
  try {
    process.kill(pid, 'SIGTERM');
    _job.status = 'cancelled';
    _proc = null;
    return { cancelled: true, pid };
  } catch (e) {
    return { cancelled: false, pid, error: String(e) };
  }
}

// ── log tail ──────────────────────────────────────────────────────────────────

export function tailLog(logFile: string, lines = 50): string[] {
  if (!fs.existsSync(logFile)) return [];
  const content = fs.readFileSync(logFile, 'utf-8');
  const all = content.split('\n');
  return all.slice(Math.max(0, all.length - lines));
}
