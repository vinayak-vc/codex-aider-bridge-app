from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from utils.onboarding_scanner import OnboardingScanner
from utils.project_knowledge import save_knowledge, to_context_text
from utils.code_review_graph_sync import refresh_project_knowledge_with_code_review_graph

_UNDERSTANDING_FILENAME: str = "AI_UNDERSTANDING.md"
_MAX_DOC_FILES: int = 8
_MAX_DOC_CHARS: int = 2000
_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "aider-env",
        "logs",
        ".vs",
        "obj",
        "bin",
        "Library",
        "Temp",
        "Packages",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "coverage",
        ".eggs",
        "bridge_progress",
        "taskJsons",
        ".claude",
    }
)
_LOW_VALUE_DOC_NAMES: tuple[str, ...] = (
    "changelog",
    "work_log",
    "latest_report",
    "release_notes",
)


@dataclass(frozen=True)
class ProjectDoc:
    path: str
    title: str
    summary: str
    score: int


def ensure_project_understanding(
    repo_root: Path,
    knowledge: dict,
    logger: logging.Logger,
    skip_source_scan: bool,
    allow_user_confirm: bool,
    input_func: Callable[[str], str] = input,
) -> dict:
    progress_dir = repo_root / "bridge_progress"
    progress_dir.mkdir(parents=True, exist_ok=True)

    original_project_type = str(knowledge.get("project", {}).get("type", "")).strip()
    docs = _discover_project_docs(repo_root)
    knowledge["docs"] = [
        {
            "path": doc.path,
            "title": doc.title,
            "summary": doc.summary,
            "score": doc.score,
        }
        for doc in docs
    ]

    if docs and not knowledge["project"].get("summary"):
        knowledge["project"]["summary"] = docs[0].summary[:300]

    if not knowledge.get("files") and not skip_source_scan:
        try:
            knowledge = OnboardingScanner(repo_root, logger).run(knowledge)
            if original_project_type:
                knowledge["project"]["type"] = original_project_type
        except Exception as ex:
            logger.warning("Project understanding scan failed (continuing without): %s", ex)

    # External deep scanner enrichment (code-review-graph).
    # Runs incrementally once initial knowledge exists, and refreshes graph-derived
    # architecture context when enough time has elapsed.
    try:
        refresh_result = refresh_project_knowledge_with_code_review_graph(
            repo_root,
            knowledge,
            logger,
            force_full_rebuild=False,
            min_interval_seconds=1800,
        )
        if refresh_result.get("ok") and refresh_result.get("changed"):
            logger.info(
                "code-review-graph sync complete (%s): nodes=%s edges=%s files=%s",
                refresh_result.get("mode", "unknown"),
                refresh_result.get("snapshot", {}).get("nodes", 0),
                refresh_result.get("snapshot", {}).get("edges", 0),
                refresh_result.get("snapshot", {}).get("files", 0),
            )
    except Exception as ex:
        logger.warning("code-review-graph sync failed (continuing): %s", ex)

    _write_understanding_file(repo_root, knowledge)
    save_knowledge(knowledge, repo_root)

    if allow_user_confirm and not knowledge["project"].get("understanding_confirmed", False):
        knowledge = _confirm_understanding(repo_root, knowledge, logger, input_func)
        _write_understanding_file(repo_root, knowledge)
        save_knowledge(knowledge, repo_root)

    return knowledge


def understanding_file_path(repo_root: Path) -> Path:
    return repo_root / "bridge_progress" / _UNDERSTANDING_FILENAME


def _discover_project_docs(repo_root: Path) -> list[ProjectDoc]:
    candidates: list[ProjectDoc] = []
    for path in sorted(repo_root.rglob("*.md")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(repo_root).parts
        if any(part in _IGNORED_DIRS for part in relative_parts[:-1]):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        doc = _build_project_doc(repo_root, path, content)
        if doc is None:
            continue
        candidates.append(doc)

    candidates.sort(key=lambda item: (-item.score, item.path))
    return candidates[:_MAX_DOC_FILES]


def _build_project_doc(repo_root: Path, path: Path, content: str) -> Optional[ProjectDoc]:
    meaningful_text = _normalize_markdown(content)
    if len(meaningful_text) < 80:
        return None

    relative_path = path.relative_to(repo_root).as_posix()
    path_lower = relative_path.lower()
    score = 0

    if path.name.lower() == "readme.md":
        score += 120
    if path.parent == repo_root:
        score += 35
    if "docs" in path.parts:
        score += 20

    heading_count = content.count("#")
    score += min(heading_count * 5, 20)

    line_count = len([line for line in content.splitlines() if line.strip()])
    score += min(line_count, 20)

    if any(marker in path_lower for marker in _LOW_VALUE_DOC_NAMES):
        score -= 60

    title = _extract_title(path, content)
    summary = _summarize_markdown(meaningful_text)
    if not summary:
        return None

    return ProjectDoc(
        path=relative_path,
        title=title,
        summary=summary,
        score=score,
    )


def _extract_title(path: Path, content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or path.stem
    return path.stem


def _normalize_markdown(content: str) -> str:
    text = re.sub(r"```.*?```", " ", content, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _summarize_markdown(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected: list[str] = []
    total_chars = 0

    for sentence in sentences:
        clean = sentence.strip()
        if len(clean) < 30:
            continue
        selected.append(clean)
        total_chars += len(clean)
        if total_chars >= _MAX_DOC_CHARS or len(selected) >= 3:
            break

    if selected:
        return " ".join(selected)[:_MAX_DOC_CHARS].strip()

    return text[: min(len(text), 280)].strip()


def _write_understanding_file(repo_root: Path, knowledge: dict) -> None:
    path = understanding_file_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    project = knowledge.get("project", {})
    docs = knowledge.get("docs", [])
    patterns = knowledge.get("patterns", [])
    files = knowledge.get("files", {})
    clarifications = knowledge.get("clarifications", [])
    confirmed = bool(project.get("understanding_confirmed", False))

    lines: list[str] = [
        "# AI Understanding",
        "",
        f"Status: `{'confirmed' if confirmed else 'pending confirmation'}`",
        f"Project: `{project.get('name', repo_root.name)}`",
    ]

    project_type = str(project.get("type", "")).strip()
    language = str(project.get("language", "")).strip()
    if project_type or language:
        lines.append(f"Type: `{project_type or 'unknown'}` | Language: `{language or 'unknown'}`")

    summary = str(project.get("summary", "")).strip()
    if summary:
        lines.extend(["", "## Summary", "", summary])

    if docs:
        lines.extend(["", "## Important Docs", ""])
        for doc in docs[:5]:
            lines.append(f"- `{doc['path']}`: {doc['summary']}")

    if files:
        lines.extend(["", "## Key Files", ""])
        for file_path, meta in list(sorted(files.items()))[:12]:
            role = str(meta.get("role", "")).strip() or "No description"
            lines.append(f"- `{file_path}`: {role}")

    if patterns:
        lines.extend(["", "## Architecture Signals", ""])
        for pattern in patterns[:8]:
            lines.append(f"- {pattern}")

    if clarifications:
        lines.extend(["", "## User Clarifications", ""])
        for item in clarifications:
            lines.append(f"- {item}")

    open_questions = _build_open_questions(knowledge)
    if open_questions:
        lines.extend(["", "## Open Questions", ""])
        for question in open_questions:
            lines.append(f"- {question}")

    lines.extend(
        [
            "",
            "## Context Text",
            "",
            "This is the compact context summary that can be reused in later bridge sessions.",
            "",
            "```text",
            to_context_text(knowledge),
            "```",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def _confirm_understanding(
    repo_root: Path,
    knowledge: dict,
    logger: logging.Logger,
    input_func: Callable[[str], str],
) -> dict:
    summary = _build_terminal_summary(repo_root, knowledge)
    print(summary)

    answer = _safe_prompt(input_func, "Is this understanding correct? [y]es / [n]o: ")
    if answer in {"y", "yes"}:
        knowledge["project"]["understanding_confirmed"] = True
        logger.info("Project understanding confirmed by user.")
        return knowledge

    clarifications = _collect_clarifications(knowledge, input_func)
    if clarifications:
        knowledge.setdefault("clarifications", []).extend(clarifications)

    updated_summary = _synthesize_summary_from_clarifications(knowledge)
    if updated_summary:
        knowledge["project"]["summary"] = updated_summary[:300]

    print(_build_terminal_summary(repo_root, knowledge))
    second_answer = _safe_prompt(
        input_func,
        "Does this updated understanding look correct now? [y]es / [n]o: ",
    )
    knowledge["project"]["understanding_confirmed"] = second_answer in {"y", "yes"}

    if knowledge["project"]["understanding_confirmed"]:
        logger.info("Project understanding confirmed after clarification.")
    else:
        logger.warning(
            "Project understanding is still pending confirmation. The saved understanding includes user clarifications."
        )

    return knowledge


def _build_terminal_summary(repo_root: Path, knowledge: dict) -> str:
    project = knowledge.get("project", {})
    docs = knowledge.get("docs", [])
    patterns = knowledge.get("patterns", [])
    files = knowledge.get("files", {})

    lines: list[str] = [
        "",
        "=" * 60,
        "PROJECT UNDERSTANDING",
        "=" * 60,
        f"Repo: {repo_root}",
    ]

    summary = str(project.get("summary", "")).strip() or "No summary inferred yet."
    lines.append(f"Summary: {summary}")

    if docs:
        lines.append("Docs used:")
        for doc in docs[:3]:
            lines.append(f"  - {doc['path']}")

    if files:
        lines.append("Key files inferred:")
        for file_path, meta in list(sorted(files.items()))[:5]:
            role = str(meta.get("role", "")).strip() or "No description"
            lines.append(f"  - {file_path}: {role}")

    if patterns:
        lines.append("Architecture signals:")
        for pattern in patterns[:3]:
            lines.append(f"  - {pattern}")

    open_questions = _build_open_questions(knowledge)
    if open_questions:
        lines.append("Open questions:")
        for question in open_questions[:3]:
            lines.append(f"  - {question}")

    lines.append(
        f"Saved understanding: {understanding_file_path(repo_root)}"
    )
    lines.append("=" * 60)
    return "\n".join(lines)


def _build_open_questions(knowledge: dict) -> list[str]:
    questions: list[str] = []
    docs = knowledge.get("docs", [])
    files = knowledge.get("files", {})
    summary = str(knowledge.get("project", {}).get("summary", "")).strip()

    if not docs:
        questions.append("No meaningful Markdown docs were found. The project purpose may need manual confirmation.")
    if len(files) < 3:
        questions.append("Only a small number of source files were identified. Confirm the main implementation folders.")
    if not summary:
        questions.append("The project summary is weak. Confirm the primary user-facing purpose of the repository.")

    return questions


def _collect_clarifications(
    knowledge: dict,
    input_func: Callable[[str], str],
) -> list[str]:
    questions: list[str] = []
    if not knowledge.get("project", {}).get("summary"):
        questions.append("What is the main purpose of this project? ")
    questions.append("Which folders or files are most important for future work? ")
    questions.append("Are there any docs, folders, or generated files the bridge should ignore? ")

    clarifications: list[str] = []
    for question in questions:
        answer = _safe_prompt(input_func, question)
        if answer:
            clarifications.append(answer)

    return clarifications


def _synthesize_summary_from_clarifications(knowledge: dict) -> str:
    docs = knowledge.get("docs", [])
    clarifications = knowledge.get("clarifications", [])
    parts: list[str] = []

    if clarifications:
        parts.append(" ".join(str(item).strip() for item in clarifications if str(item).strip()))
    if docs:
        parts.append(str(docs[0].get("summary", "")).strip())

    combined = " ".join(part for part in parts if part).strip()
    if not combined:
        return ""
    return combined[:300]


def _safe_prompt(input_func: Callable[[str], str], prompt: str) -> str:
    if not sys.stdin.isatty():
        return ""

    try:
        return input_func(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
