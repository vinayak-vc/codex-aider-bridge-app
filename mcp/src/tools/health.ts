import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

// ── helpers ───────────────────────────────────────────────────────────────────

interface ServiceResult {
  up: boolean;
  latency_ms: number | null;
  [key: string]: unknown;
}

async function probe(url: string, timeoutMs = 4000): Promise<{ ok: boolean; latency_ms: number; body: unknown }> {
  const start = Date.now();
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    const latency_ms = Date.now() - start;
    let body: unknown;
    try { body = await res.json(); } catch { body = null; }
    return { ok: res.ok, latency_ms, body };
  } catch {
    return { ok: false, latency_ms: Date.now() - start, body: null };
  }
}

function ok(data: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }] };
}

// ── tool registration ─────────────────────────────────────────────────────────

export function registerHealthTool(server: McpServer): void {

  server.tool(
    'bridge_health',
    [
      'Check all bridge dependencies in one call and return a structured health report.',
      'Covers: Qdrant (vector DB), Ollama (local LLM host), bridge-memory-service, and Aider CLI.',
      'Replaces the separate setup-check curl calls in Stage 1 of the codex-aider-bridge skill.',
    ].join(' '),
    {},
    async () => {
      // Run all probes concurrently
      const [qdrantProbe, ollamaProbe, memProbe, ollamaTagsProbe] = await Promise.all([
        probe('http://localhost:6333/'),
        probe('http://localhost:11434/'),
        probe('http://localhost:3000/health'),
        probe('http://localhost:11434/api/tags'),
      ]);

      // ── Qdrant ────────────────────────────────────────────────────────────────
      const qdrantBody = qdrantProbe.body as Record<string, unknown> | null;
      const qdrant: ServiceResult = {
        up:         qdrantProbe.ok,
        latency_ms: qdrantProbe.ok ? qdrantProbe.latency_ms : null,
        version:    qdrantBody?.version ?? null,
        url:        'http://localhost:6333',
      };

      // ── Ollama ────────────────────────────────────────────────────────────────
      const rawModels = (ollamaTagsProbe.body as { models?: Array<{ name: string }> } | null)?.models ?? [];
      const models = rawModels.map(m => m.name);
      const ollama: ServiceResult = {
        up:         ollamaProbe.ok,
        latency_ms: ollamaProbe.ok ? ollamaProbe.latency_ms : null,
        models,
        model_count: models.length,
        url:        'http://localhost:11434',
      };

      // ── Memory service ────────────────────────────────────────────────────────
      const memBody = memProbe.body as Record<string, unknown> | null;
      const memory_service: ServiceResult = {
        up:         memProbe.ok,
        latency_ms: memProbe.ok ? memProbe.latency_ms : null,
        status:     memBody?.status ?? null,
        mode:       qdrantProbe.ok ? 'vector+sqlite' : 'sqlite-only',
        url:        'http://localhost:3000',
      };

      // ── Aider CLI ─────────────────────────────────────────────────────────────
      const aiderResult = spawnSync('aider', ['--version'], { encoding: 'utf-8', timeout: 5000 });
      const aiderVersion = (aiderResult.stdout ?? '').trim() || (aiderResult.stderr ?? '').trim();
      const aider: ServiceResult = {
        up:         aiderResult.status === 0,
        latency_ms: null,   // CLI check, not a network call
        version:    aiderResult.status === 0 ? aiderVersion : null,
        error:      aiderResult.status !== 0 ? 'aider not found in PATH — run: pip install aider-chat' : null,
      };

      // ── MCP server itself ─────────────────────────────────────────────────────
      const mcp_server: ServiceResult = {
        up:         true,
        latency_ms: 0,
        version:    '0.1.0',
        note:       'This response proves the MCP server is running',
      };

      // ── main.py presence ─────────────────────────────────────────────────────
      // Detect bridge root by walking up from this file's location
      let bridgeRoot: string | null = null;
      let dir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));
      for (let i = 0; i < 6; i++) {
        if (fs.existsSync(path.join(dir, 'main.py'))) { bridgeRoot = dir; break; }
        dir = path.dirname(dir);
      }

      const overall_up = qdrant.up && ollama.up && memory_service.up && aider.up;

      return ok({
        overall_up,
        services: { qdrant, ollama, memory_service, aider, mcp_server },
        bridge_root: bridgeRoot,
        main_py_found: bridgeRoot !== null,
      });
    }
  );
}
