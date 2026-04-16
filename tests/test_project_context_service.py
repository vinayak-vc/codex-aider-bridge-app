from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.project_context_service import ProjectContextService


class ProjectContextServiceTests(unittest.TestCase):
    def test_load_returns_graphify_backed_context(self) -> None:
        repo_root = self._fixture_root("with_graphify")

        service = ProjectContextService(repo_root)
        ctx = service.load()

        self.assertEqual("graphify", ctx.source)
        self.assertEqual("fresh", ctx.freshness)
        self.assertTrue(ctx.graphify.available)
        self.assertEqual(4, ctx.graphify.nodes)
        self.assertEqual(3, ctx.graphify.edges)
        self.assertEqual(2, ctx.graphify.communities)
        self.assertIn("SupervisorAgent", ctx.graphify.god_nodes)
        self.assertTrue(ctx.docs)
        self.assertTrue(ctx.file_roles)
        self.assertIn("Ship cloud dashboard", ctx.features_done)
        self.assertIn("Prefer existing Firebase auth flow.", ctx.clarifications)
        self.assertIn("PROJECT:", ctx.planner_text)
        self.assertIn("KEY FILES:", ctx.relay_text)
        self.assertIn("REVIEW QUESTIONS:", ctx.review_text)

    def test_graph_status_reads_report_sections(self) -> None:
        repo_root = self._fixture_root("with_graphify")

        service = ProjectContextService(repo_root)
        status = service.graph_status()

        self.assertTrue(status.available)
        self.assertEqual(["SupervisorAgent", "RepoScanner"], status.god_nodes)
        self.assertEqual(
            ["Why does `RepoScanner` connect planning to relay?"],
            status.suggested_questions,
        )
        self.assertEqual(
            ["`Relay API` --uses--> `RepoScanner`  [INFERRED]"],
            status.surprising_connections,
        )

    def test_knowledge_fallback_returns_knowledge_context(self) -> None:
        repo_root = self._fixture_root("with_knowledge_only")

        service = ProjectContextService(repo_root)
        ctx = service.load()

        self.assertEqual("knowledge", ctx.source)
        self.assertEqual("fresh", ctx.freshness)
        self.assertFalse(ctx.graphify.available)
        self.assertTrue(ctx.docs)
        self.assertTrue(ctx.file_roles)
        self.assertIn("PROJECT: knowledge-only-repo", ctx.planner_text)
        self.assertIn("FILE ROLES:", ctx.planner_text)
        self.assertIn("KEY DOCS:", ctx.relay_text)
        self.assertIn("REVIEW ANCHORS:", ctx.review_text)

    def test_scan_fallback_returns_scan_context(self) -> None:
        repo_root = self._fixture_root("with_scan_only")

        service = ProjectContextService(repo_root)
        ctx = service.load()

        self.assertEqual("scan", ctx.source)
        self.assertEqual("fresh", ctx.freshness)
        self.assertFalse(ctx.graphify.available)
        self.assertIn("Repo snapshot available from lightweight scan.", ctx.metadata.summary)
        self.assertIn("REPO SNAPSHOT:", ctx.planner_text)
        self.assertIn("app.py", ctx.planner_text)

    def test_missing_graphify_returns_empty_context(self) -> None:
        repo_root = self._fixture_root("missing_repo")

        service = ProjectContextService(repo_root)
        ctx = service.load()

        self.assertEqual("empty", ctx.source)
        self.assertEqual("missing", ctx.freshness)
        self.assertFalse(ctx.graphify.available)
        self.assertIn("No project context is available yet.", ctx.planner_text)

    def _fixture_root(self, name: str) -> Path:
        return Path(__file__).resolve().parent / "fixtures" / "project_context_service" / name
