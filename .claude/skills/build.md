---
name: build
description: Smart builder — tiny tasks done directly, medium/large via bridge+Aider. Usage: /build <goal> --project <path>
command: build
---

# /build — Smart Automated Builder

When the user says `/build <goal>` or "build X", execute this workflow:

## Step 1: Parse Arguments

Extract:
- **goal**: what to build/fix/implement
- **project**: repo root path (use `--project <path>` or ask if not provided)

If no project path given, check settings:
```
Read <bridge_root>/ui/data/settings.json → repo_root
```

## Step 2: Understand the Project

Read from the **target project**:
1. `<project>/bridge_progress/project_knowledge.json`
2. `<project>/bridge_progress/LATEST_REPORT.md` (if exists)
3. Feature spec .md files if referenced in goal

Read the actual target files that the goal mentions — use Read tool to see exact code structure.

## Step 3: Classify & Execute Each Task

For each change needed, classify and execute:

### TINY (1 file, < 50 lines changed, simple property/config/import)
**→ Do it yourself. No bridge. No Aider.**

1. Read the file with Read tool
2. Edit it with Edit tool (exact string replacement)
3. Report: "Task N: done (direct edit) — added maxVideos property to uploadOptions"
4. Move to next task immediately

Examples of tiny tasks:
- Add a property to an object: `maxVideos: 1`
- Change a hardcoded value: `"1"` → `String(options.maxVideos || 1)`
- Add an import statement
- Rename a variable
- Toggle a boolean flag

### MEDIUM (1-2 files, 50-500 lines, function-level changes)
**→ Delegate to bridge + Ollama 7b**

1. Write task to plan JSON with `"model": "ollama/qwen2.5-coder:7b"`
2. Include in the bridge run

### LARGE (2+ files, > 500 lines, new components, complex logic)
**→ Delegate to bridge + Ollama 14b**

1. Write task to plan JSON with `"model": "ollama/qwen2.5-coder:14b"`
2. Include in the bridge run

## Step 4: Execute the Plan

After classifying all tasks:

**If ALL tasks are tiny:**
- Execute them all directly with Edit tool
- No bridge needed at all
- Report results immediately
- Fastest path — seconds, not minutes

**If there are medium/large tasks:**
1. Do the tiny tasks yourself first (Edit tool)
2. Write remaining tasks to `<project>/taskJsons/plan_<timestamp>.json`
3. Launch bridge subprocess in background:
   ```bash
   python main.py "<goal>" \
     --repo-root "<project>" \
     --plan-file "<project>/taskJsons/plan_<timestamp>.json" \
     --manual-supervisor \
     --aider-model "ollama/qwen2.5-coder:7b" \
     --task-timeout 600 \
     --max-task-retries 10
   ```
4. Set up auto-review cron (every minute):
   ```
   CronCreate "* * * * *":
     Poll <project>/bridge_progress/manual_supervisor/requests/
     Read each new request → analyze diff → write PASS/REWORK decision
   ```

## Step 5: Log Activity to History

After completing tasks, log them to the bridge history so they appear
in the UI History page:

```bash
curl -X POST http://127.0.0.1:7823/api/history -H "Content-Type: application/json" -d '{
  "goal": "<what was built>",
  "repo_root": "<project path>",
  "status": "success",
  "performer": "claude",
  "tasks": <number of tasks>,
  "files_changed": ["file1.js", "file2.jsx"],
  "source": "build_skill"
}'
```

Or use apiPost if bridge is running:
```python
apiPost('/api/history', { goal, repo_root, status: 'success', performer: 'claude', tasks: N, files_changed: [...], source: 'build_skill' })
```

## Step 6: Report

For direct edits:
- "Done: edited 3 files directly (0 tokens, 2 seconds)"

For bridge tasks:
- "Bridge running with N tasks. Tiny tasks already done."
- Report when bridge finishes

## Example

User: `/build implement max-videos-control --project D:\BridgeProjectExperiment`

I classify:
```
Task 1: Add maxVideos:1 to uploadOptions → TINY (1 property add)
  → I read useAppStore.js → Edit tool → done in 2 seconds

Task 2: Change "1" to options.maxVideos in uploadService.js → TINY (2 value changes)
  → I read uploadService.js → Edit tool twice → done in 3 seconds

Task 3: Add number input + wire to store in Upload.jsx → MEDIUM (JSX + state)
  → Delegate to bridge + 7b model
```

Result:
- Tasks 1-2: done instantly (direct edit, $0, 0 tokens)
- Task 3: bridge + Aider (~22 seconds, $0 Ollama)
- Total: ~25 seconds vs ~8 minutes if all went through Aider
