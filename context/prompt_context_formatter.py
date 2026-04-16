from __future__ import annotations

from typing import Any

from context.project_context import ProjectContext


def render_knowledge_context(knowledge: dict[str, Any]) -> str:
    """Produce a compact human-readable summary for AI prompt injection."""
    proj = knowledge.get("project", {})
    files = knowledge.get("files", {})
    patterns = knowledge.get("patterns", [])
    done = knowledge.get("features_done", [])
    suggested = knowledge.get("suggested_next", [])
    docs = knowledge.get("docs", [])
    clarifications = knowledge.get("clarifications", [])

    lines: list[str] = []

    name = proj.get("name", "Unknown Project")
    lang = proj.get("language", "")
    ptype = proj.get("type", "")
    type_str = f" ({ptype}/{lang})" if ptype and lang else f" ({lang or ptype})" if (lang or ptype) else ""
    lines.append(f"PROJECT: {name}{type_str}")
    if proj.get("scanned"):
        lines.append("(roles inferred by static scan - not task-authored)")

    summary = proj.get("summary", "")
    if summary:
        lines.append(f"SUMMARY: {summary}")

    if docs:
        lines.append("")
        lines.append("DOCUMENTATION SIGNALS:")
        for doc in docs[:5]:
            doc_path = str(doc.get("path", "")).strip()
            doc_summary = str(doc.get("summary", "")).strip()
            if doc_path and doc_summary:
                lines.append(f"  {doc_path}")
                lines.append(f"    -> {doc_summary}")

    if files:
        lines.append("")
        lines.append("FILE REGISTRY (what each file does):")
        for file_path, meta in sorted(files.items()):
            role = meta.get("role", "no description")
            lines.append(f"  {file_path}")
            lines.append(f"    -> {role}")

    if patterns:
        lines.append("")
        lines.append("CODE PATTERNS:")
        for pattern in patterns:
            lines.append(f"  -{pattern}")

    if done:
        lines.append("")
        lines.append(f"ALREADY IMPLEMENTED: {', '.join(done)}")

    if suggested:
        lines.append("")
        lines.append("POSSIBLE NEXT STEPS:")
        for item in suggested:
            lines.append(f"  -{item}")

    if clarifications:
        lines.append("")
        lines.append("USER CLARIFICATIONS:")
        for item in clarifications:
            lines.append(f"  -{item}")

    runs = knowledge.get("runs", [])
    if runs:
        last = runs[-1]
        lines.append("")
        lines.append(
            f"LAST RUN: {last.get('date', '?')} | "
            f"{last.get('tasks_completed', 0)} tasks | "
            f"\"{last.get('goal', '')}\""
        )

    return "\n".join(lines)


def render_planner_context(ctx: ProjectContext) -> str:
    lines = [
        f"PROJECT: {ctx.metadata.name}",
        f"CONTEXT SOURCE: {ctx.source}",
        f"SUMMARY: {ctx.metadata.summary}",
    ]
    if ctx.graphify.available:
        lines.append(
            f"GRAPHIFY: {ctx.graphify.nodes} nodes, {ctx.graphify.edges} edges, {ctx.graphify.communities} communities"
        )
    if ctx.graphify.god_nodes:
        lines.append("GOD NODES: " + ", ".join(ctx.graphify.god_nodes[:5]))
    if ctx.patterns:
        lines.append("COMMUNITIES: " + ", ".join(ctx.patterns[:8]))
    if ctx.file_roles:
        lines.append("FILE ROLES:")
        for role in ctx.file_roles[:10]:
            lines.append(f"  {role.path}: {role.role}")
    if ctx.repo_snapshot.tree:
        lines.append("REPO SNAPSHOT:")
        lines.extend(f"  {line}" for line in ctx.repo_snapshot.tree.splitlines()[:20])
    return "\n".join(lines)


def render_relay_context(ctx: ProjectContext) -> str:
    lines = [
        f"PROJECT: {ctx.metadata.name}",
        f"SUMMARY: {ctx.metadata.summary}",
    ]
    if ctx.graphify.god_nodes:
        lines.append("KEY CONCEPTS: " + ", ".join(ctx.graphify.god_nodes[:4]))
    if ctx.docs:
        lines.append("KEY DOCS:")
        for doc in ctx.docs[:4]:
            lines.append(f"  {doc.path}: {doc.summary}")
    if ctx.file_roles:
        lines.append("KEY FILES:")
        for role in ctx.file_roles[:8]:
            lines.append(f"  {role.path}: {role.role}")
    return "\n".join(lines)


def render_review_context(ctx: ProjectContext) -> str:
    lines = [
        f"PROJECT: {ctx.metadata.name}",
        f"ARCHITECTURE SUMMARY: {ctx.metadata.summary}",
    ]
    if ctx.graphify.surprising_connections:
        lines.append("SURPRISING CONNECTIONS:")
        for item in ctx.graphify.surprising_connections[:3]:
            lines.append(f"  - {item}")
    if ctx.graphify.suggested_questions:
        lines.append("REVIEW QUESTIONS:")
        for item in ctx.graphify.suggested_questions[:3]:
            lines.append(f"  - {item}")
    elif ctx.patterns:
        lines.append("REVIEW ANCHORS:")
        for item in ctx.patterns[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)
