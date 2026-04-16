import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import fs from 'node:fs';
import path from 'node:path';

// ── helpers ───────────────────────────────────────────────────────────────────

function readJson<T>(filePath: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as T;
  } catch {
    return null;
  }
}

function progressDir(repoRoot: string): string {
  return path.join(repoRoot, 'bridge_progress');
}

function progressFile(repoRoot: string, name: string): string {
  return path.join(progressDir(repoRoot), name);
}

function ok(data: unknown) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }],
  };
}

function err(message: string) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }],
    isError: true,
  };
}

// ── tool registrations ────────────────────────────────────────────────────────

export function registerStateTools(server: McpServer): void {

  // ── bridge_get_status ────────────────────────────────────────────────────────
  server.tool(
    'bridge_get_status',
    'Read the current bridge run status for a repo. Returns status, task counts, last commit, and token usage from last_run.json and task_metrics.json.',
    { repo_root: z.string().describe('Absolute path to the target repository root') },
    async ({ repo_root }) => {
      const metrics = readJson<Record<string, unknown>>(progressFile(repo_root, 'task_metrics.json'));
      const lastRun = readJson<Record<string, unknown>>(progressFile(repo_root, 'last_run.json'));

      if (!metrics && !lastRun) {
        return err(`No bridge_progress/ found at ${repo_root} — has this repo been run through the bridge?`);
      }

      const result = {
        repo_root,
        status:          metrics?.status ?? lastRun?.status ?? 'unknown',
        planned_tasks:   metrics?.planned_tasks ?? null,
        completed_tasks: metrics?.completed_tasks ?? null,
        skipped_tasks:   metrics?.skipped_tasks ?? null,
        failed_task_id:  metrics?.failed_task_id ?? null,
        last_commit:     (() => {
          const tasks = (metrics?.tasks as Array<{ commit_sha?: string }>) ?? [];
          const last = [...tasks].reverse().find(t => t.commit_sha);
          return last?.commit_sha ?? null;
        })(),
        elapsed_seconds: lastRun?.elapsed_seconds ?? null,
        tokens: lastRun?.tokens ?? null,
      };

      return ok(result);
    }
  );

  // ── bridge_get_checkpoint ────────────────────────────────────────────────────
  server.tool(
    'bridge_get_checkpoint',
    'Read the bridge checkpoint for a repo — which task IDs have already completed so the next run can resume from where it left off.',
    { repo_root: z.string().describe('Absolute path to the target repository root') },
    async ({ repo_root }) => {
      const ckpt = readJson<{ completed: number[]; plan_hash: string }>(
        progressFile(repo_root, 'checkpoint.json')
      );

      if (!ckpt) {
        return ok({ repo_root, checkpoint_exists: false, completed_ids: [], plan_hash: null });
      }

      return ok({
        repo_root,
        checkpoint_exists: true,
        completed_ids: ckpt.completed ?? [],
        plan_hash: ckpt.plan_hash ?? null,
      });
    }
  );

  // ── bridge_get_metrics ───────────────────────────────────────────────────────
  server.tool(
    'bridge_get_metrics',
    'Return the full task_metrics.json for a repo — per-task completion status, commit SHAs, types, and file lists.',
    { repo_root: z.string().describe('Absolute path to the target repository root') },
    async ({ repo_root }) => {
      const metrics = readJson<unknown>(progressFile(repo_root, 'task_metrics.json'));
      if (!metrics) {
        return err(`task_metrics.json not found at ${progressDir(repo_root)}`);
      }
      return ok(metrics);
    }
  );

  // ── bridge_get_project_knowledge ─────────────────────────────────────────────
  server.tool(
    'bridge_get_project_knowledge',
    'Return the accumulated project knowledge for a repo — project type, file summaries, patterns, past run history, and suggested next steps.',
    { repo_root: z.string().describe('Absolute path to the target repository root') },
    async ({ repo_root }) => {
      const knowledge = readJson<unknown>(progressFile(repo_root, 'project_knowledge.json'));
      if (!knowledge) {
        return err(`project_knowledge.json not found at ${progressDir(repo_root)}`);
      }
      return ok(knowledge);
    }
  );

  // ── bridge_list_repos ────────────────────────────────────────────────────────
  server.tool(
    'bridge_list_repos',
    'Scan a directory (defaults to user home) for repos that have a bridge_progress/ directory, indicating they have been run through the bridge before.',
    {
      scan_dir: z.string()
        .optional()
        .describe('Directory to scan for bridged repos. Defaults to the user home directory.'),
    },
    async ({ scan_dir }) => {
      const base = scan_dir ?? (process.env.HOME ?? process.env.USERPROFILE ?? 'C:\\');

      let entries: fs.Dirent[];
      try {
        entries = fs.readdirSync(base, { withFileTypes: true });
      } catch (e) {
        return err(`Cannot read directory: ${base} — ${e}`);
      }

      const repos: Array<{ path: string; status: string | null; last_run: string | null }> = [];

      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const candidate = path.join(base, entry.name);
        const bp = path.join(candidate, 'bridge_progress');
        if (!fs.existsSync(bp)) continue;

        const metrics = readJson<{ status?: string }>(path.join(bp, 'task_metrics.json'));
        const lastRun = readJson<{ status?: string }>(path.join(bp, 'last_run.json'));

        repos.push({
          path: candidate,
          status: metrics?.status ?? lastRun?.status ?? null,
          last_run: (() => {
            const runs = readJson<{ runs?: Array<{ date: string }> }>(
              path.join(bp, 'project_knowledge.json')
            );
            const r = runs?.runs;
            return r && r.length > 0 ? r[r.length - 1].date : null;
          })(),
        });
      }

      return ok({ scanned: base, bridged_repos: repos, count: repos.length });
    }
  );
}
