# bridge-mcp-server

MCP server for [codex-aider-bridge](https://github.com/vinayak-vc/codex-aider-bridge-app) — connects Claude Code to the bridge pipeline via the Model Context Protocol.

## Install

```bash
npm install -g bridge-mcp-server
```

That's it. The postinstall script automatically:

- Clones the bridge runtime to `~/.bridge/`
- Installs the Claude Code skill to `~/.claude/skills/codex-aider-bridge/`
- Registers the MCP server in `~/.claude/settings.json`

Then **restart Claude Code**, open any project, and type:

```
/codex-aider-bridge
```

## What this package does

The MCP server exposes the bridge pipeline as tools that Claude Code calls directly:

| Tool | Description |
|---|---|
| `bridge_health` | Check all dependencies (Aider, Ollama, memory service, Qdrant) |
| `bridge_ping` | Confirm MCP server is reachable |
| `bridge_dry_run` | Validate a task plan without running Aider |
| `bridge_run_plan` | Start a bridge run in the background |
| `bridge_get_run_output` | Tail logs from the current or last run |
| `bridge_get_status` | Get task progress for a repo |
| `bridge_get_metrics` | Per-task metrics for a completed run |
| `bridge_get_checkpoint` | Read the last checkpoint for resume |
| `bridge_get_project_knowledge` | Load prior file summaries and run history |
| `bridge_cancel` | Cancel a running job |
| `bridge_list_repos` | List repos that have been run through the bridge |
| `memory_save` | Save a memory entry to the bridge memory service |
| `memory_search` | Retrieve relevant memories for a query |
| `memory_ingest` | Process an agent event into memory |
| `memory_enhance` | Enhance a prompt with relevant past context |
| `memory_health` | Check memory service status |

## How it finds the Python runtime

The MCP server locates `main.py` in this order:

1. `BRIDGE_ROOT` environment variable (set automatically by postinstall)
2. `~/.bridge/` (default clone location)
3. Walk up from the MCP server's own file (for local dev / cloned repo)

## Manual MCP registration (if needed)

If you need to register the MCP server manually, add this to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "bridge-mcp-server": {
      "command": "npx",
      "args": ["-y", "bridge-mcp-server"],
      "env": {
        "BRIDGE_ROOT": "/path/to/.bridge"
      }
    }
  }
}
```

## Requirements

- Node.js 18+
- Git (for the postinstall clone)
- Python 3.10+ in the target environment
- `aider-chat` installed (`pip install aider-chat`)
- Ollama running locally (or any Aider-compatible model)

## Links

- [Full documentation](https://github.com/vinayak-vc/codex-aider-bridge-app)
- [Report issues](https://github.com/vinayak-vc/codex-aider-bridge-app/issues)
