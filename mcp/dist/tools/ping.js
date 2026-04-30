const VERSION = '0.1.0';
export function registerPingTool(server) {
    server.tool('bridge_ping', 'Health check — confirms the bridge MCP server is running and returns its version.', {}, async () => {
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify({ ok: true, version: VERSION, server: 'bridge-mcp-server' }, null, 2),
                },
            ],
        };
    });
}
//# sourceMappingURL=ping.js.map