import { z } from 'zod';
// ── config ────────────────────────────────────────────────────────────────────
function baseUrl() {
    return process.env.MEMORY_SERVICE_URL ?? 'http://localhost:3000';
}
// ── helpers ───────────────────────────────────────────────────────────────────
async function memFetch(method, path, body) {
    const url = `${baseUrl()}${path}`;
    const init = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body !== undefined) {
        init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    let data;
    try {
        data = await res.json();
    }
    catch {
        data = await res.text();
    }
    return { ok: res.ok, status: res.status, data };
}
function ok(data) {
    return { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
}
function err(message) {
    return {
        content: [{ type: 'text', text: JSON.stringify({ error: message }) }],
        isError: true,
    };
}
// MemoryType numeric values (mirrors the service enum)
const MEMORY_TYPES = {
    episodic: 0,
    semantic: 1,
    procedural: 2,
};
const MEMORY_TYPE_NAMES = {
    0: 'episodic',
    1: 'semantic',
    2: 'procedural',
};
// ── tool registrations ────────────────────────────────────────────────────────
export function registerMemoryTools(server) {
    // ── memory_health ────────────────────────────────────────────────────────────
    server.tool('memory_health', 'Check whether the bridge-memory-service is running and which storage mode it is in (vector+sqlite or sqlite-only).', {}, async () => {
        try {
            const health = await memFetch('GET', '/health');
            if (!health.ok) {
                return err(`Memory service returned ${health.status}`);
            }
            // Infer mode by probing Qdrant directly
            let mode = 'unknown';
            try {
                const qdrant = await fetch('http://localhost:6333/health', { signal: AbortSignal.timeout(2000) });
                mode = qdrant.ok ? 'vector+sqlite' : 'sqlite-only';
            }
            catch {
                mode = 'sqlite-only';
            }
            return ok({
                status: 'ok',
                url: baseUrl(),
                mode,
                service: health.data?.service ?? 'bridge-memory-service',
            });
        }
        catch (e) {
            return err(`Memory service unreachable at ${baseUrl()} — ${e}`);
        }
    });
    // ── memory_search ────────────────────────────────────────────────────────────
    server.tool('memory_search', 'Search the bridge memory store for entries relevant to a query. Returns ranked results with scores. Use this at session start (Stage 1.5) to retrieve past run patterns.', {
        query: z.string().describe('Search query — e.g. "pipeline.py rework failures"'),
        limit: z.number().int().min(1).max(50).optional().describe('Max results to return (default 10)'),
        type: z.enum(['episodic', 'semantic', 'procedural']).optional()
            .describe('Filter by memory type: episodic=events, semantic=facts, procedural=patterns'),
    }, async ({ query, limit = 10, type }) => {
        try {
            const params = new URLSearchParams({ query, limit: String(limit) });
            if (type !== undefined) {
                params.set('type', String(MEMORY_TYPES[type]));
            }
            const res = await memFetch('GET', `/memory/search?${params}`);
            if (!res.ok) {
                return err(`Search failed (${res.status}): ${JSON.stringify(res.data)}`);
            }
            // Annotate each result with human-readable type name.
            // SQLite serialises the REAL column as a string like "2.0", so normalise
            // via Number() + Math.round() before lookup.
            const results = res.data.map(r => ({
                ...r,
                entry: {
                    ...r.entry,
                    type_name: MEMORY_TYPE_NAMES[Math.round(Number(r.entry.type))] ?? 'unknown',
                },
            }));
            return ok({ query, count: results.length, results });
        }
        catch (e) {
            return err(`memory_search failed: ${e}`);
        }
    });
    // ── memory_save ──────────────────────────────────────────────────────────────
    server.tool('memory_save', 'Save a new entry to the bridge memory store. Use at end of session (Stage 5-E) to record what worked, what failed, model preferences, and repo-specific quirks.', {
        content: z.string().describe('The full text content to remember'),
        summary: z.string().optional().describe('Short one-line summary (auto-generated from first 80 chars if omitted)'),
        type: z.enum(['episodic', 'semantic', 'procedural'])
            .describe('episodic=a specific event/run, semantic=a fact about the repo, procedural=a pattern/rule to follow'),
        tags: z.array(z.string()).optional().describe('Tags for filtering — e.g. ["repo:my-project", "aider-model", "rework"]'),
    }, async ({ content, summary, type, tags = [] }) => {
        try {
            const res = await memFetch('POST', '/memory/save', {
                type: MEMORY_TYPES[type],
                content,
                summary: summary ?? content.slice(0, 80),
                tags,
            });
            if (!res.ok) {
                return err(`Save failed (${res.status}): ${JSON.stringify(res.data)}`);
            }
            const entry = res.data;
            return ok({ saved: true, id: entry.id, type, tags });
        }
        catch (e) {
            return err(`memory_save failed: ${e}`);
        }
    });
    // ── memory_enhance ───────────────────────────────────────────────────────────
    server.tool('memory_enhance', 'Send a task instruction to the memory service for enhancement — relevant past context is injected into the prompt before Aider sees it.', {
        prompt: z.string().describe('The raw task instruction to enhance'),
        limit: z.number().int().min(1).max(20).optional().describe('Max memory entries to inject (default 5)'),
    }, async ({ prompt, limit = 5 }) => {
        try {
            const res = await memFetch('POST', '/bridge/enhance', { prompt, limit });
            if (!res.ok) {
                return err(`Enhance failed (${res.status}): ${JSON.stringify(res.data)}`);
            }
            const enhanced = res.data?.enhancedPrompt ?? prompt;
            const injected = enhanced !== prompt;
            return ok({
                original: prompt,
                enhanced,
                context_injected: injected,
                char_delta: enhanced.length - prompt.length,
            });
        }
        catch (e) {
            return err(`memory_enhance failed: ${e}`);
        }
    });
    // ── memory_ingest ────────────────────────────────────────────────────────────
    server.tool('memory_ingest', 'Ingest a completed task result (input instruction + output diff) into the memory store so future sessions can learn from it.', {
        input: z.string().describe('The task instruction that was executed'),
        output: z.string().describe('The resulting diff or output (can be truncated to ~1000 chars)'),
        agent: z.string().optional().describe('Agent identifier — defaults to "aider-bridge"'),
    }, async ({ input, output, agent = 'aider-bridge' }) => {
        try {
            const res = await memFetch('POST', '/bridge/ingest', { input, output, agent });
            if (!res.ok) {
                return err(`Ingest failed (${res.status}): ${JSON.stringify(res.data)}`);
            }
            return ok({ ingested: true, agent });
        }
        catch (e) {
            return err(`memory_ingest failed: ${e}`);
        }
    });
}
//# sourceMappingURL=memory.js.map