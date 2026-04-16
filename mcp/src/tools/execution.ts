import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runDryRun, startRun, cancelRun, currentJob, tailLog } from '../bridge/runner.js';

function ok(data: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }] };
}
function err(message: string) {
  return { content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }], isError: true as const };
}

// Shared flags schema reused across dry-run and real run
const flagsSchema = {
  aider_model:          z.string().optional().describe('Ollama model to use, e.g. "ollama/qwen2.5-coder:14b"'),
  validation_command:   z.string().optional().describe('Command to run after each task, e.g. "pytest --tb=short -q"'),
  auto_split_threshold: z.number().int().optional().describe('Split tasks that touch >= N files into individual tasks'),
  manual_supervisor:    z.boolean().optional().describe('Enable manual supervisor mode (bridge writes review JSONs and waits)'),
  aider_no_map:         z.boolean().optional().describe('Pass --map-tokens 0 to Aider (use for large repos with node_modules etc.)'),
  task_timeout:         z.number().int().optional().describe('Max seconds per Aider task (default 600)'),
  workflow_profile:     z.enum(['standard', 'micro']).optional().describe('"micro" enforces one-file atomic tasks'),
  log_level:            z.enum(['DEBUG', 'INFO', 'WARNING', 'ERROR']).optional(),
};

export function registerExecutionTools(server: McpServer): void {

  // ── bridge_dry_run ──────────────────────────────────────────────────────────
  server.tool(
    'bridge_dry_run',
    [
      'Validate a task plan against a repo without invoking Aider.',
      'Parses the plan JSON, resolves file paths, and logs each task instruction.',
      'Use this before every real run to catch schema errors and wrong repo paths.',
    ].join(' '),
    {
      plan_file: z.string().describe('Absolute path to the plan JSON file to validate'),
      repo_root: z.string().describe('Absolute path to the target repository root'),
      goal:      z.string().optional().describe('Short goal description passed to the bridge (default: "dry-run-check")'),
      ...flagsSchema,
    },
    async ({ plan_file, repo_root, goal, ...flags }) => {
      if (!fs.existsSync(plan_file)) {
        return err(`Plan file not found: ${plan_file}`);
      }
      if (!fs.existsSync(repo_root)) {
        return err(`repo_root not found: ${repo_root}`);
      }

      try {
        const result = await runDryRun(repo_root, plan_file, goal ?? 'dry-run-check', flags);
        return ok(result);
      } catch (e) {
        return err(`bridge_dry_run failed: ${e}`);
      }
    }
  );

  // ── bridge_run_plan ─────────────────────────────────────────────────────────
  server.tool(
    'bridge_run_plan',
    [
      'Start a full bridge run in the background.',
      'Returns immediately with PID and log file path.',
      'Monitor progress with bridge_get_run_output (log tail) and bridge_get_status (task counts).',
      'Only one job can run at a time.',
    ].join(' '),
    {
      plan_file: z.string().describe('Absolute path to the plan JSON file to execute'),
      repo_root: z.string().describe('Absolute path to the target repository root'),
      goal:      z.string().describe('Short goal description, e.g. "Add health endpoint to API"'),
      ...flagsSchema,
    },
    async ({ plan_file, repo_root, goal, ...flags }) => {
      if (!fs.existsSync(plan_file)) {
        return err(`Plan file not found: ${plan_file}`);
      }
      if (!fs.existsSync(repo_root)) {
        return err(`repo_root not found: ${repo_root}`);
      }

      try {
        const result = startRun(repo_root, plan_file, goal, flags);
        if (!result.started) {
          return err(result.error ?? 'Failed to start job');
        }
        return ok({
          started:    true,
          pid:        result.pid,
          log_file:   result.log_file,
          status:     'running',
          tip:        'Use bridge_get_run_output to tail logs, bridge_get_status to check task progress.',
        });
      } catch (e) {
        return err(`bridge_run_plan failed: ${e}`);
      }
    }
  );

  // ── bridge_cancel ───────────────────────────────────────────────────────────
  server.tool(
    'bridge_cancel',
    'Cancel the currently running bridge job (sends SIGTERM to the python main.py process).',
    {},
    async () => {
      const result = cancelRun();
      if (!result.cancelled && result.error) {
        return err(result.error);
      }
      return ok(result);
    }
  );

  // ── bridge_get_run_output ───────────────────────────────────────────────────
  server.tool(
    'bridge_get_run_output',
    [
      'Tail the log file from the most recent bridge run.',
      'Returns the last N lines plus any structured bridge events found in the log.',
      'Use this to monitor a running job or read the result of a completed one.',
    ].join(' '),
    {
      lines: z.number().int().min(1).max(500).optional()
               .describe('Number of log lines to return (default 80)'),
      log_file: z.string().optional()
                 .describe('Override the log file path (defaults to current job log or last known log)'),
    },
    async ({ lines = 80, log_file }) => {
      // Resolve log file path
      const job = currentJob();
      const targetLog = log_file ?? job?.log_file ?? null;

      if (!targetLog) {
        return err('No log file available — start a job first with bridge_run_plan.');
      }
      if (!fs.existsSync(targetLog)) {
        return err(`Log file not found: ${targetLog}`);
      }

      const tail = tailLog(targetLog, lines);

      // Parse structured bridge events from the log
      const events: unknown[] = [];
      for (const line of tail) {
        const trimmed = line.trim();
        if (trimmed.startsWith('{"_bridge_event"') || trimmed.startsWith('{"status"')) {
          try { events.push(JSON.parse(trimmed)); } catch { /* ignore */ }
        }
      }

      // Summarise current task progress from events
      let latest_task: number | null = null;
      const taskRe = /Task (\d+) —/;
      for (const line of tail) {
        const m = line.match(taskRe);
        if (m) latest_task = Number(m[1]);
      }

      return ok({
        job_status:   job?.status ?? 'unknown',
        log_file:     targetLog,
        latest_task,
        line_count:   tail.length,
        tail:         tail.join('\n'),
        bridge_events: events,
      });
    }
  );
}
