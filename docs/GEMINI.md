## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current

## Antigravity Skills

This project has Antigravity skills at `.gemini/skills/`. These define automated workflows for the codex-aider-bridge pipeline.

### Available Skills

| Skill | File | Trigger |
|---|---|---|
| **codex-aider-bridge** | `.gemini/skills/codex-aider-bridge/SKILL.md` | "use the bridge", "implement X", "build X", any coding goal |
| **build** | `.gemini/skills/build.md` | `/build <goal>`, "build X" with smart tiny/medium/large classification |

### Rules

- When the user asks to implement, build, or fix anything as a goal → read `.gemini/skills/codex-aider-bridge/SKILL.md` and follow it stage-by-stage
- When the user says `/build <goal>` → read `.gemini/skills/build.md` and follow it
- Reference files are at `.gemini/skills/codex-aider-bridge/references/` (pipeline.md, flags.md)
- **Never write source code directly** when the bridge skill is triggered — all code goes through the bridge+Aider pipeline
- Use the MCP `code-review-graph` tools for codebase indexing (replaces external tool calls)
- Use Knowledge Items (KIs) for cross-session memory (replaces claude-mem)

### Bridge Quick Reference

```
bridge_root = cwd (always run from inside this repo)
REPO_ROOT   = target project path (can be this repo or external)

Standard run:
  python main.py --repo-root "<REPO_ROOT>" --plan-file "TASK_PLAN_active.json" --manual-supervisor --workflow-profile micro

Dry run (validate plan):
  python main.py --repo-root "<REPO_ROOT>" --plan-file "TASK_PLAN_active.json" --dry-run

Resume interrupted:
  python main.py --repo-root "<REPO_ROOT>" --resume --manual-supervisor
```
