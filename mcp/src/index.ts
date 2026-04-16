import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { registerPingTool } from './tools/ping.js';
import { registerStateTools } from './tools/state.js';

async function main() {
  const server = new McpServer({
    name: 'bridge-mcp-server',
    version: '0.1.0',
  });

  // M1 — ping
  registerPingTool(server);

  // M2 — state tools
  registerStateTools(server);

  // Future milestones register here:
  // M3: registerMemoryTools(server);
  // M4: registerHealthTool(server);
  // M5: registerExecutionTools(server);

  const transport = new StdioServerTransport();
  transport.onerror = (err) => process.stderr.write(`[bridge-mcp] transport error: ${err}\n`);

  await server.connect(transport);
  process.stderr.write('[bridge-mcp] Server started on stdio\n');
}

main().catch((err) => {
  process.stderr.write(`[bridge-mcp] Fatal: ${err}\n`);
  process.exit(1);
});
