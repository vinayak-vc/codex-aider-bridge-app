# Pipeline Reference

## Task JSON Schema

The plan you produce in Stage 2 must be valid JSON matching this exact schema.
Save it to `TASK_PLAN_active.json` — the bridge parser (`parser/task_parser.py`) is strict.

```json
{
  "goal": "Short description of the overall goal",
  "tasks": [
    {
      "id": 1,
      "type": "create | modify | delete | validate",
      "files": ["relative/path/to/file.py"],
      "instruction": "Self-contained instruction. Name exact function/class/variable. Describe data shapes inline.",
      "context_files": ["relative/path/to/read_only_ref.py"],
      "must_exist": ["relative/path/to/file.py"],
      "must_not_exist": [],
      "model": null
    }
  ]
}
```

### Field rules

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Sequential integers starting at 1 |
| `type` | yes | `create`, `modify`, `delete`, or `validate` |
| `files` | yes | Files Aider may edit. One file per task (MICRO-TASK profile) |
| `instruction` | yes | Must be self-contained — see MICRO-TASK rules below |
| `context_files` | no | Read-only references Aider can consult but not edit |
| `must_exist` | no | Post-task assertion: these paths must exist after task runs |
| `must_not_exist` | no | Post-task assertion: these paths must NOT exist after task runs |
| `model` | no | Override Aider model for this task only. Leave null for auto-routing |

---

## MICRO-TASK Profile Rules

Always use these rules when producing plans. They are optimised for small local LLMs (7–14B params).

1. **One file per task.** Never put more than one path in `files`.
2. **One concern per task.** Don't combine unrelated changes.
3. **Name the exact symbol.** Say `"add parameter max_retries (int) to function run_task in executor/aider_runner.py"`, not `"update the runner"`.
4. **Describe data shapes inline.** If task B reads output from task A, describe the shape in task B's instruction — don't say "read the other file".
5. **Every `create` task needs `must_exist`.** Every `delete` task needs `must_not_exist`.
6. **Every `modify` task should include at least one observable assertion** when the output is testable.
7. **Keep instructions under 200 words.** If you can't fit it in 200 words, split the task further.
8. **No vague verbs.** Banned: "refactor", "clean up", "improve", "update the module". Required: "add", "remove", "rename", "replace X with Y", "set field Z to value W".

### Good vs bad instruction examples

**Bad:** `"Refactor the executor module to handle errors better"`
**Good:** `"In executor/aider_runner.py, in function _run_aider, wrap the subprocess.run call in a try/except subprocess.TimeoutExpired. On timeout, set exit_code=124, stdout='', stderr='TIMEOUT'. Return an ExecutionResult with success=False."`

**Bad:** `"Add retry logic"`
**Good:** `"In executor/aider_runner.py, add parameter max_retries (int, default=3) to AiderRunner.__init__. Store as self.max_retries. In run(), add a for-loop from 1 to max_retries that calls _run_aider() and breaks if result.success is True."`

---

## Review Criteria

When you receive a diff for review in Stage 4, evaluate it against these criteria in order:

### Automatic REWORK triggers (any one → rework immediately)

- The diff modifies files **not in the task's `files` list**
- A `must_exist` file was specified but does not exist after the task
- A `must_not_exist` file still exists after the task
- The diff is empty (no changes made) — unless the task was a `validate` type
- Syntax error is visible in the diff (e.g. unclosed bracket, mismatched indent)
- The instruction said to add/modify a specific function but the diff touches a different function

### Judgment-based REWORK triggers (use your discretion)

- The change is superficially correct but logically wrong (e.g. condition is inverted)
- The change introduces an import that doesn't exist in the codebase
- The change removes something that other tasks will need (check later tasks in the plan)
- The instruction asked for a specific value/parameter and the diff uses a different one

### PASS criteria

- The diff makes exactly the change described in the instruction
- All assertions (`must_exist`, `must_not_exist`) pass
- No unrelated files were touched
- The change looks syntactically correct
- If a `validate` task: the validation output confirms the expected state

### Writing good REWORK reasons

A rework reason must be specific enough for a 7B LLM to fix it without reading anything else.

**Bad:** `"The change is incorrect"`
**Bad:** `"This doesn't match the instruction"`
**Good:** `"The function signature added parameter timeout but instruction requires max_retries (int, default=3). Remove timeout, add max_retries with default value 3."`
**Good:** `"The diff modifies executor/aider_runner.py but also changed models/task.py (line 44). Revert models/task.py change — it was not in scope for this task."`

---

## Failure Taxonomy

When a task fails, the bridge logs a failure type. Match the type to the correct action:

| Failure type | Meaning | Your action |
|---|---|---|
| `pattern_mismatch` | LLM output didn't match diff format | Let bridge retry (it switches to `whole` edit format automatically) |
| `no_change` | LLM made no changes | Simplify the instruction — it may be too abstract |
| `context_overflow` | LLM context window exceeded | Split task into smaller pieces or remove `context_files` |
| `useless_response` | LLM returned irrelevant text | Consider switching model (`--aider-model`) or escalate to user |
| `syntax_error` | Syntax error in generated code | Let bridge retry once; if it fails again, provide explicit fix in rework reason |
| `timeout` | LLM took too long | Check Ollama is running; consider a faster model |
| `unknown` | Unclassified | Read `bridge_progress/` logs and report full error to user |

After 3 failures on the same task → **pause and report to user** before more retries.
