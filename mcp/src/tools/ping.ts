import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';

const VERSION = '0.1.0';

export function registerPingTool(server: McpServer): void {
  server.tool(
    'bridge_ping',
    'Health check — confirms the bridge MCP server is running and returns its version.',
    {},
    async () => {
      return {
        content: [
          {
            type: 'text' as const,
            text: JSON.stringify({ ok: true, version: VERSION, server: 'bridge-mcp-server' }, null, 2),
          },
        ],
      };
    }
  );
}
