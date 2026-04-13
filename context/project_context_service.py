from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from context.project_context import (
    DocSummary,
    FileRole,
    FreshnessState,
    GraphifySummary,
    ProjectContext,
    ProjectMetadata,
    RepoSnapshot,
)
from context.prompt_context_formatter import (
    render_knowledge_context,
    render_planner_context,
    render_relay_context,
    render_review_context,
)
from context.repo_scanner import RepoScanner
from utils.project_knowledge import load_knowledge


class ProjectContextService:
    def __init__(
        self,
        repo_root: Path,
        *,
        graphify_dir: str = "graphify-out",
        knowledge_file: str = "bridge_progress/project_knowledge.json",
        prefer_graphify: bool = True,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._graphify_dir = self._repo_root / graphify_dir
        self._knowledge_path = self._repo_root / knowledge_file
        self._prefer_graphify = prefer_graphify

    def exists(self) -> bool:
        return self.graph_status().available or self._knowledge_path.exists() or self._repo_root.exists()

    def load(self, *, allow_stale: bool = True) -> ProjectContext:
        graph_ctx = self._load_graphify_context() if self._prefer_graphify else None
        knowledge_ctx = self._load_knowledge_context()
        scan_ctx = self._load_scan_context()

        if graph_ctx is not None:
            merged = self._merge_contexts(graph_ctx, knowledge_ctx, scan_ctx)
            if merged.freshness == "stale" and not allow_stale:
                return self._merge_contexts(self._build_empty_context(), knowledge_ctx, scan_ctx)
            return merged

        if knowledge_ctx is not None:
            return self._merge_contexts(knowledge_ctx, None, scan_ctx)

        if scan_ctx is not None:
            return self._merge_contexts(scan_ctx, None, None)

        return self._build_empty_context()

    def refresh(self, *, rebuild_graph: bool = False) -> ProjectContext:
        _ = rebuild_graph
        return self.load(allow_stale=True)

    def load_for_planner(self) -> ProjectContext:
        return self.load(allow_stale=True)

    def load_for_relay(self) -> ProjectContext:
        return self.load(allow_stale=True)

    def load_for_review(self) -> ProjectContext:
        return self.load(allow_stale=True)

    def graph_status(self) -> GraphifySummary:
        graph_path = self._graphify_dir / "graph.json"
        report_path = self._graphify_dir / "GRAPH_REPORT.md"
        html_path = self._graphify_dir / "graph.html"
        if not graph_path.exists() or not report_path.exists():
            return GraphifySummary(
                available=False,
                graph_path=graph_path if graph_path.exists() else None,
                report_path=report_path if report_path.exists() else None,
                html_path=html_path if html_path.exists() else None,
                nodes=0,
                edges=0,
                communities=0,
            )

        graph_data = self._read_json(graph_path, default={})
        report_text = report_path.read_text(encoding="utf-8", errors="replace")
        summary = self._parse_summary_counts(report_text)
        return GraphifySummary(
            available=True,
            graph_path=graph_path,
            report_path=report_path,
            html_path=html_path if html_path.exists() else None,
            nodes=summary.get("nodes", len(graph_data.get("nodes", []))),
            edges=summary.get("edges", len(graph_data.get("links", []))),
            communities=summary.get("communities", 0),
            god_nodes=self._parse_god_nodes(report_text),
            surprising_connections=self._parse_bullets(report_text, "## Surprising Connections"),
            suggested_questions=self._parse_suggested_questions(report_text),
        )

    def needs_refresh(self, *, max_age_hours: int = 24) -> bool:
        status = self.graph_status()
        if not status.available or status.graph_path is None or status.report_path is None:
            return True
        newest = max(status.graph_path.stat().st_mtime, status.report_path.stat().st_mtime)
        current = self._repo_root.stat().st_mtime if self._repo_root.exists() else newest
        return (current - newest) > (max_age_hours * 3600)

    def render_planner_text(self, ctx: ProjectContext) -> str:
        return render_planner_context(ctx)

    def render_relay_text(self, ctx: ProjectContext) -> str:
        return render_relay_context(ctx)

    def render_review_text(self, ctx: ProjectContext) -> str:
        return render_review_context(ctx)

    def _load_graphify_context(self) -> Optional[ProjectContext]:
        status = self.graph_status()
        if not status.available or status.graph_path is None or status.report_path is None:
            return None

        graph_data = self._read_json(status.graph_path, default={})
        nodes = graph_data.get("nodes", [])
        report_text = status.report_path.read_text(encoding="utf-8", errors="replace")

        docs = self._build_doc_summaries(nodes)
        file_roles = self._build_file_roles(nodes)
        metadata = ProjectMetadata(
            name=self._repo_root.name,
            repo_root=self._repo_root,
            project_type="",
            language=self._infer_language(nodes),
            summary=self._parse_project_summary(report_text, docs),
        )
        ctx = ProjectContext(
            metadata=metadata,
            source="graphify",
            freshness=self._compute_freshness(status),
            graphify=status,
            repo_snapshot=RepoSnapshot(
                tree=self._build_repo_snapshot(file_roles),
                generated_by="graphify",
            ),
            docs=docs,
            file_roles=file_roles,
            patterns=self._parse_community_hubs(report_text),
            features_done=[],
            clarifications=[],
            recent_runs=self._read_cost_runs(),
            planner_text="",
            relay_text="",
            review_text="",
        )
        return self._with_rendered_text(ctx)

    def _load_knowledge_context(self) -> Optional[ProjectContext]:
        if not self._knowledge_path.exists():
            return None

        knowledge = load_knowledge(self._repo_root)
        project = knowledge.get("project", {})
        docs = [
            DocSummary(
                path=str(doc.get("path", "")),
                title=str(doc.get("title") or Path(str(doc.get("path", ""))).name),
                summary=str(doc.get("summary", "")),
                score=int(doc.get("score", 0) or 0),
            )
            for doc in knowledge.get("docs", [])
            if str(doc.get("path", "")).strip()
        ]
        file_roles = [
            FileRole(path=path, role=str(meta.get("role", "no description")), source="knowledge")
            for path, meta in sorted(knowledge.get("files", {}).items())
        ]
        ctx = ProjectContext(
            metadata=ProjectMetadata(
                name=str(project.get("name", self._repo_root.name)),
                repo_root=self._repo_root,
                project_type=str(project.get("type", "")),
                language=str(project.get("language", "")),
                summary=str(project.get("summary", "")).strip() or "Project knowledge cache available.",
            ),
            source="knowledge",
            freshness="fresh",
            graphify=GraphifySummary(
                available=False,
                graph_path=None,
                report_path=None,
                html_path=None,
                nodes=0,
                edges=0,
                communities=0,
            ),
            repo_snapshot=RepoSnapshot(tree="", generated_by="knowledge"),
            docs=docs,
            file_roles=file_roles,
            patterns=[str(item) for item in knowledge.get("patterns", [])],
            features_done=[str(item) for item in knowledge.get("features_done", [])],
            clarifications=[str(item) for item in knowledge.get("clarifications", [])],
            recent_runs=[run for run in knowledge.get("runs", []) if isinstance(run, dict)],
            planner_text=render_knowledge_context(knowledge),
            relay_text="",
            review_text="",
        )
        return self._with_rendered_text(ctx)

    def _load_scan_context(self) -> Optional[ProjectContext]:
        if not self._repo_root.exists():
            return None
        tree = RepoScanner(self._repo_root).scan()
        if not tree.strip():
            return None
        ctx = ProjectContext(
            metadata=ProjectMetadata(
                name=self._repo_root.name,
                repo_root=self._repo_root,
                project_type="",
                language="",
                summary="Repo snapshot available from lightweight scan.",
            ),
            source="scan",
            freshness="fresh",
            graphify=GraphifySummary(
                available=False,
                graph_path=None,
                report_path=None,
                html_path=None,
                nodes=0,
                edges=0,
                communities=0,
            ),
            repo_snapshot=RepoSnapshot(tree=tree, generated_by="scan"),
            docs=[],
            file_roles=[],
            patterns=[],
            features_done=[],
            clarifications=[],
            recent_runs=[],
            planner_text="",
            relay_text="",
            review_text="",
        )
        return self._with_rendered_text(ctx)

    def _build_empty_context(self) -> ProjectContext:
        ctx = ProjectContext(
            metadata=ProjectMetadata(
                name=self._repo_root.name,
                repo_root=self._repo_root,
                project_type="",
                language="",
                summary="No project context is available yet.",
            ),
            source="empty",
            freshness="missing",
            graphify=GraphifySummary(
                available=False,
                graph_path=None,
                report_path=None,
                html_path=None,
                nodes=0,
                edges=0,
                communities=0,
            ),
            repo_snapshot=RepoSnapshot(tree="", generated_by="empty"),
            docs=[],
            file_roles=[],
            patterns=[],
            features_done=[],
            clarifications=[],
            recent_runs=[],
            planner_text="",
            relay_text="",
            review_text="",
        )
        return self._with_rendered_text(ctx)

    def _with_rendered_text(self, ctx: ProjectContext) -> ProjectContext:
        return ProjectContext(
            metadata=ctx.metadata,
            source=ctx.source,
            freshness=ctx.freshness,
            graphify=ctx.graphify,
            repo_snapshot=ctx.repo_snapshot,
            docs=ctx.docs,
            file_roles=ctx.file_roles,
            patterns=ctx.patterns,
            features_done=ctx.features_done,
            clarifications=ctx.clarifications,
            recent_runs=ctx.recent_runs,
            planner_text=render_planner_context(ctx),
            relay_text=render_relay_context(ctx),
            review_text=render_review_context(ctx),
        )

    def _merge_contexts(
        self,
        primary: ProjectContext,
        secondary: Optional[ProjectContext],
        tertiary: Optional[ProjectContext],
    ) -> ProjectContext:
        docs = self._dedupe_docs(primary.docs + (secondary.docs if secondary else []))
        file_roles = self._dedupe_file_roles(primary.file_roles + (secondary.file_roles if secondary else []))
        patterns = self._dedupe_strings(primary.patterns + (secondary.patterns if secondary else []))
        features_done = self._dedupe_strings(primary.features_done + (secondary.features_done if secondary else []))
        clarifications = self._dedupe_strings(primary.clarifications + (secondary.clarifications if secondary else []))
        recent_runs = list(primary.recent_runs)
        if secondary:
            for run in secondary.recent_runs:
                if run not in recent_runs:
                    recent_runs.append(run)

        repo_snapshot = primary.repo_snapshot
        if not repo_snapshot.tree and secondary and secondary.repo_snapshot.tree:
            repo_snapshot = secondary.repo_snapshot
        if not repo_snapshot.tree and tertiary and tertiary.repo_snapshot.tree:
            repo_snapshot = tertiary.repo_snapshot

        metadata = ProjectMetadata(
            name=primary.metadata.name or (secondary.metadata.name if secondary else self._repo_root.name),
            repo_root=self._repo_root,
            project_type=primary.metadata.project_type or (secondary.metadata.project_type if secondary else ""),
            language=primary.metadata.language or (secondary.metadata.language if secondary else ""),
            summary=primary.metadata.summary or (secondary.metadata.summary if secondary else ""),
        )
        ctx = ProjectContext(
            metadata=metadata,
            source=primary.source,
            freshness=primary.freshness,
            graphify=primary.graphify,
            repo_snapshot=repo_snapshot,
            docs=docs,
            file_roles=file_roles,
            patterns=patterns,
            features_done=features_done,
            clarifications=clarifications,
            recent_runs=recent_runs,
            planner_text="",
            relay_text="",
            review_text="",
        )
        return self._with_rendered_text(ctx)

    def _read_json(self, path: Path, *, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _read_cost_runs(self) -> list[dict]:
        cost_path = self._graphify_dir / "cost.json"
        data = self._read_json(cost_path, default={})
        runs = data.get("runs", [])
        if isinstance(runs, list):
            return [r for r in runs if isinstance(r, dict)]
        return []

    def _compute_freshness(self, status: GraphifySummary) -> FreshnessState:
        if not status.available:
            return "missing"
        return "fresh"

    def _parse_section(self, report_text: str, heading: str) -> list[str]:
        lines = report_text.splitlines()
        start_idx: Optional[int] = None
        for idx, line in enumerate(lines):
            if line.strip() == heading or line.strip().startswith(heading):
                start_idx = idx + 1
                break
        if start_idx is None:
            return []
        end_idx = len(lines)
        for idx in range(start_idx, len(lines)):
            if lines[idx].startswith("## "):
                end_idx = idx
                break
        return lines[start_idx:end_idx]

    def _parse_summary_counts(self, report_text: str) -> dict[str, int]:
        lines = self._parse_section(report_text, "## Summary")
        counts: dict[str, int] = {}
        for line in lines:
            match = re.search(r"(\d+)\s+nodes.+?(\d+)\s+edges.+?(\d+)\s+communities", line)
            if match:
                counts["nodes"] = int(match.group(1))
                counts["edges"] = int(match.group(2))
                counts["communities"] = int(match.group(3))
                break
        return counts

    def _parse_god_nodes(self, report_text: str) -> list[str]:
        items = []
        for line in self._parse_section(report_text, "## God Nodes (most connected - your core abstractions)"):
            match = re.search(r"`([^`]+)`", line)
            if match:
                items.append(match.group(1))
        return items

    def _parse_bullets(self, report_text: str, heading: str) -> list[str]:
        items = []
        for line in self._parse_section(report_text, heading):
            clean = line.strip()
            if clean.startswith("- "):
                items.append(clean[2:].strip())
        return items

    def _parse_suggested_questions(self, report_text: str) -> list[str]:
        items = []
        for line in self._parse_section(report_text, "## Suggested Questions"):
            clean = line.strip()
            match = re.match(r"- \*\*(.+?)\*\*", clean)
            if match:
                items.append(match.group(1))
        return items

    def _parse_community_hubs(self, report_text: str) -> list[str]:
        hubs = []
        for line in self._parse_section(report_text, "## Community Hubs (Navigation)"):
            match = re.search(r"\|([^\]]+)\]\]", line)
            if match:
                hubs.append(match.group(1))
        return hubs

    def _build_doc_summaries(self, nodes: list[dict]) -> list[DocSummary]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            if node.get("file_type") != "document":
                continue
            source_file = str(node.get("source_file") or "").strip()
            label = str(node.get("label") or "").strip()
            if source_file and label:
                grouped[source_file].append(label)

        docs: list[DocSummary] = []
        for path, labels in sorted(grouped.items()):
            unique: list[str] = []
            seen: set[str] = set()
            for label in labels:
                if label not in seen:
                    seen.add(label)
                    unique.append(label)
            title = Path(path).name
            summary_labels = [label for label in unique if label != title][:3]
            summary = ", ".join(summary_labels) if summary_labels else "Graphify document summary available."
            docs.append(DocSummary(path=path, title=title, summary=summary, score=len(unique)))
        docs.sort(key=lambda item: (-item.score, item.path))
        return docs[:12]

    def _build_file_roles(self, nodes: list[dict]) -> list[FileRole]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            source_file = str(node.get("source_file") or "").strip()
            label = str(node.get("label") or "").strip()
            if source_file:
                grouped[source_file].append(label)

        roles: list[FileRole] = []
        for path, labels in sorted(grouped.items()):
            basename = Path(path).name
            interesting = [
                label for label in labels
                if label and label != basename and label != path and len(label) > 3
            ]
            top_labels = [label for label, _ in Counter(interesting).most_common(3)]
            role = "Graphify concepts: " + ", ".join(top_labels) if top_labels else "Observed by graphify."
            roles.append(FileRole(path=path, role=role, source="graphify"))
        return roles[:50]

    def _infer_language(self, nodes: list[dict]) -> str:
        counts = Counter()
        for node in nodes:
            source_file = str(node.get("source_file") or "")
            suffix = Path(source_file).suffix.lower()
            if suffix:
                counts[suffix] += 1
        if not counts:
            return ""
        suffix = counts.most_common(1)[0][0]
        return {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".md": "markdown",
        }.get(suffix, suffix.lstrip("."))

    def _parse_project_summary(self, report_text: str, docs: list[DocSummary]) -> str:
        lines = self._parse_section(report_text, "## Summary")
        summary_bits = [line[2:].strip() for line in lines if line.strip().startswith("- ")]
        if summary_bits:
            return " ".join(summary_bits)
        if docs:
            return docs[0].summary
        return "Graphify context available."

    def _build_repo_snapshot(self, file_roles: list[FileRole]) -> str:
        if not file_roles:
            return ""
        lines = [self._repo_root.name + "/"]
        top_level: dict[str, list[str]] = defaultdict(list)
        for role in file_roles[:40]:
            parts = Path(role.path).parts
            head = parts[0] if parts else role.path
            top_level[head].append(role.path)
        for key in sorted(top_level):
            lines.append(f"- {key}")
            for path in sorted(top_level[key])[:5]:
                lines.append(f"  - {path}")
        return "\n".join(lines)

    def _dedupe_docs(self, docs: list[DocSummary]) -> list[DocSummary]:
        seen: set[str] = set()
        result: list[DocSummary] = []
        for doc in docs:
            if doc.path and doc.path not in seen:
                seen.add(doc.path)
                result.append(doc)
        return result

    def _dedupe_file_roles(self, roles: list[FileRole]) -> list[FileRole]:
        seen: set[str] = set()
        result: list[FileRole] = []
        for role in roles:
            if role.path and role.path not in seen:
                seen.add(role.path)
                result.append(role)
        return result

    def _dedupe_strings(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            clean = str(item).strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result
