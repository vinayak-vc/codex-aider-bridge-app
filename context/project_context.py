from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


ContextSource = Literal["graphify", "knowledge", "scan", "empty"]
FreshnessState = Literal["fresh", "stale", "missing"]


@dataclass(frozen=True)
class ProjectMetadata:
    name: str
    repo_root: Path
    project_type: str
    language: str
    summary: str


@dataclass(frozen=True)
class FileRole:
    path: str
    role: str
    source: ContextSource


@dataclass(frozen=True)
class DocSummary:
    path: str
    title: str
    summary: str
    score: int


@dataclass(frozen=True)
class GraphifySummary:
    available: bool
    graph_path: Optional[Path]
    report_path: Optional[Path]
    html_path: Optional[Path]
    nodes: int
    edges: int
    communities: int
    god_nodes: list[str] = field(default_factory=list)
    surprising_connections: list[str] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepoSnapshot:
    tree: str
    generated_by: ContextSource


@dataclass(frozen=True)
class ProjectContext:
    metadata: ProjectMetadata
    source: ContextSource
    freshness: FreshnessState
    graphify: GraphifySummary
    repo_snapshot: RepoSnapshot
    docs: list[DocSummary]
    file_roles: list[FileRole]
    patterns: list[str]
    features_done: list[str]
    clarifications: list[str]
    recent_runs: list[dict]
    planner_text: str
    relay_text: str
    review_text: str
