"""Generate a Markdown run report from a session token report.

Writes bridge_progress/RUN_REPORT.md after each run with:
- Run summary
- Token breakdown (Supervisor vs Aider)
- Savings comparison (Direct AI vs Bridge)
- Per-task detail table
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def generate_run_report(session: dict, repo_root: Path) -> str:
    """Build a Markdown report and write it to bridge_progress/RUN_REPORT.md.

    Returns the report text.
    """
    report = _build_report(session)

    out_dir = repo_root / "bridge_progress"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "RUN_REPORT.md"
    out_path.write_text(report, encoding="utf-8")

    return report


def _build_report(s: dict) -> str:
    goal = s.get("goal", "N/A")
    supervisor_cmd = s.get("supervisor_command", "N/A")
    elapsed = s.get("elapsed_seconds", 0)
    timestamp = s.get("timestamp", datetime.now().isoformat(timespec="seconds"))

    sup = s.get("supervisor", {})
    aider = s.get("aider", {})
    savings = s.get("savings", {})
    session = s.get("session", {})

    tasks_executed = aider.get("tasks_executed", 0)
    tasks_skipped = aider.get("tasks_skipped", 0)
    reworks = aider.get("reworks", 0)
    aider_tokens = aider.get("estimated_tokens", 0)
    per_task = aider.get("per_task", [])

    sup_total = sup.get("total", 0)
    total_ai = session.get("total_ai_tokens", sup_total)

    estimated_direct = savings.get("estimated_direct_tokens", 0)
    tokens_saved = savings.get("tokens_saved", 0)
    savings_pct = savings.get("savings_percent", 0)

    lines = []
    lines.append("# Bridge Run Report")
    lines.append("")
    lines.append(f"> Generated: {timestamp}")
    lines.append("")

    # ── Summary ──────────────────────────────────────────────────────
    lines.append("## Run Summary")
    lines.append("")
    lines.append(f"| Field | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Goal | {goal} |")
    lines.append(f"| Supervisor | {supervisor_cmd} |")
    lines.append(f"| Tasks executed | {tasks_executed} |")
    lines.append(f"| Tasks skipped | {tasks_skipped} |")
    lines.append(f"| Reworks | {reworks} |")
    lines.append(f"| Duration | {elapsed}s |")
    lines.append("")

    # ── Token Breakdown ──────────────────────────────────────────────
    lines.append("## Token Breakdown")
    lines.append("")
    lines.append("| Category | In | Out | Total |")
    lines.append("|---|---|---|---|")
    lines.append(f"| Supervisor: Plan | {sup.get('plan_in', 0):,} | {sup.get('plan_out', 0):,} | {sup.get('plan_in', 0) + sup.get('plan_out', 0):,} |")
    lines.append(f"| Supervisor: Review | {sup.get('review_in', 0):,} | {sup.get('review_out', 0):,} | {sup.get('review_in', 0) + sup.get('review_out', 0):,} |")
    if sup.get("subplan_in", 0) or sup.get("subplan_out", 0):
        lines.append(f"| Supervisor: Subplan | {sup.get('subplan_in', 0):,} | {sup.get('subplan_out', 0):,} | {sup.get('subplan_in', 0) + sup.get('subplan_out', 0):,} |")
    lines.append(f"| **Supervisor Total** | {sup.get('total_in', 0):,} | {sup.get('total_out', 0):,} | **{sup_total:,}** |")
    lines.append(f"| Session overhead | — | — | {session.get('tokens', 0):,} {'(est.)' if session.get('is_estimate') else '(exact)'} |")
    lines.append(f"| **Aider (local LLM)** | — | — | **~{aider_tokens:,}** (estimated) |")
    lines.append(f"| **Total AI (cloud)** | — | — | **{total_ai:,}** |")
    lines.append("")

    # ── Savings Comparison ───────────────────────────────────────────
    lines.append("## Savings Comparison")
    lines.append("")
    lines.append("```")
    lines.append(f"WITHOUT BRIDGE (all cloud AI):")
    lines.append(f"  Planning:     {sup.get('plan_in', 0) + sup.get('plan_out', 0):>8,} tokens")
    lines.append(f"  Coding:       {tasks_executed * 5000:>8,} tokens  ({tasks_executed} tasks x 5,000)")
    lines.append(f"  TOTAL:        {estimated_direct:>8,} tokens")
    lines.append(f"")
    lines.append(f"WITH BRIDGE:")
    lines.append(f"  Supervisor:   {total_ai:>8,} tokens  (cloud — costs money)")
    lines.append(f"  Aider:       ~{aider_tokens:>8,} tokens  (local LLM — free)")
    lines.append(f"  TOTAL:        {total_ai + aider_tokens:>8,} tokens  (only {total_ai:,} charged)")
    lines.append(f"")
    lines.append(f"CLOUD AI SAVED: {tokens_saved:>8,} tokens  ({savings_pct}%)")
    lines.append("```")
    lines.append("")

    # ── Per-Task Breakdown ───────────────────────────────────────────
    if per_task:
        lines.append("## Per-Task Token Usage (Aider)")
        lines.append("")
        lines.append("| Task | Estimated Tokens |")
        lines.append("|---|---|")
        for t in per_task:
            tid = t.get("task_id", "?")
            est = t.get("estimated_tokens", 0)
            lines.append(f"| Task {tid} | ~{est:,} |")
        lines.append(f"| **Total** | **~{aider_tokens:,}** |")
        lines.append("")

    # ── Note ─────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("*Token estimates use the approximation: 1 token = 4 characters.*")
    lines.append("*Aider tokens run on local GPU (Ollama) and are free. Savings % reflects cloud AI cost reduction only.*")
    lines.append("")

    return "\n".join(lines)
