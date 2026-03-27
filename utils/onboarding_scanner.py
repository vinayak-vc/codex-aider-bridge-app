"""Onboarding scanner — one-time source analysis for existing projects.

On the first bridge run against an existing codebase, project_knowledge.json
is empty.  The supervisor therefore receives only a bare directory tree and
must guess at every file's purpose.  This scanner reads source files once,
extracts roles from docstrings / class names / function names, detects the
dominant language and architectural patterns, and writes an enriched
project_knowledge.json so the supervisor generates a correct plan immediately.

The scan runs automatically when knowledge["files"] is empty and the
--skip-onboarding-scan flag is not set.  It uses the stdlib only (ast, re,
pathlib) and completes in < 30 s for projects up to 500 files.
"""
from __future__ import annotations

import ast
import logging
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

# Mirrors context/repo_scanner.py RepoScanner._IGNORE plus bridge-specific dirs.
_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "aider-env",
    "logs", ".vs", "obj", "bin", "Library", "Temp", "Packages",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".tox", "coverage", ".eggs", "bridge_progress", "taskJsons",
    # Claude Code internal dirs (worktrees, settings, plans, etc.)
    ".claude",
})

# Only attempt role extraction for these extensions.
_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".cs", ".go", ".java",
    ".cpp", ".c", ".h", ".rs", ".rb", ".php", ".swift", ".kt",
    ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".xml",
    ".html", ".css", ".scss", ".sh", ".bat", ".ps1",
})

# Maximum number of files to scan (prevents runaway on huge monorepos).
_MAX_SCAN_FILES: int = 500

# Role strings are truncated to this length (matches project_knowledge.py convention).
_ROLE_SUMMARY_CHARS: int = 200

# Max file size — skip anything larger (likely generated / binary disguised as text).
_MAX_FILE_BYTES: int = 1_000_000  # 1 MB

# Framework import names that signal a well-known dependency.
_FRAMEWORK_IMPORTS: dict[str, str] = {
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "aiohttp": "aiohttp",
    "pytest": "pytest",
    "unittest": "unittest",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "react": "React",
    "vue": "Vue",
    "angular": "Angular",
    "express": "Express",
    "nextjs": "Next.js",
}


def _today() -> str:
    return date.today().isoformat()


# ── Per-language role extractors ───────────────────────────────────────────────

def _extract_python_role(path: Path) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """Return (role_string, class_list, import_list) for a Python file.

    class_list entries: (class_name, [base_names])
    import_list: top-level module names (e.g. "flask", "django")
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _generic_role(path), [], []

    # Module docstring → first sentence.
    doc = ast.get_docstring(tree)
    if doc:
        first = doc.split(".")[0].strip()
        role = first[:_ROLE_SUMMARY_CHARS]
    else:
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        funcs = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
        ]
        parts: list[str] = []
        if classes:
            parts.append(f"defines {', '.join(classes[:4])}")
        if funcs:
            parts.append(f"exposes {', '.join(funcs[:5])}")
        role = ("; ".join(parts) or "no description")[:_ROLE_SUMMARY_CHARS]

    # Collect class inheritance info.
    class_list: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases: list[str] = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
            class_list.append((node.name, bases))

    # Collect top-level imports.
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".")[0].lower())

    return role, class_list, imports


def _extract_js_ts_role(path: Path) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """Return (role, class_list, import_list) for JS/TS files via regex."""
    source = path.read_text(encoding="utf-8", errors="replace")

    # File-level JSDoc comment.
    jsdoc = re.search(r'/\*\*\s*(.*?)\s*\*/', source, re.DOTALL)
    if jsdoc:
        first = jsdoc.group(1).split("\n")[0].strip().lstrip("*").strip()
        if first:
            role = first[:_ROLE_SUMMARY_CHARS]
        else:
            role = ""
    else:
        role = ""

    if not role:
        classes = re.findall(r'(?:export\s+)?class\s+(\w+)', source)
        exports = re.findall(r'export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)', source)
        parts = []
        if classes:
            parts.append(f"defines {', '.join(classes[:4])}")
        if exports:
            parts.append(f"exports {', '.join(exports[:5])}")
        role = ("; ".join(parts) or "no description")[:_ROLE_SUMMARY_CHARS]

    # Inheritance.
    class_list: list[tuple[str, list[str]]] = []
    for m in re.finditer(r'class\s+(\w+)\s+extends\s+(\w+)', source):
        class_list.append((m.group(1), [m.group(2)]))

    # Imports (from 'module').
    imports = [m.lower() for m in re.findall(r"from\s+['\"]([^'\"./][^'\"]*)['\"]", source)]

    return role, class_list, imports


def _extract_csharp_role(path: Path) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """Return (role, class_list, import_list) for C# files via regex."""
    source = path.read_text(encoding="utf-8", errors="replace")

    # XML summary comment.
    summary = re.search(r'///\s*<summary>(.*?)</summary>', source, re.DOTALL)
    if summary:
        text = re.sub(r'\s+', ' ', summary.group(1).replace("///", "").strip())
        role = text[:_ROLE_SUMMARY_CHARS]
    else:
        classes = re.findall(r'(?:public|internal)\s+(?:partial\s+)?(?:abstract\s+)?class\s+(\w+)', source)
        parts = []
        if classes:
            parts.append(f"defines {', '.join(classes[:4])}")
        role = ("; ".join(parts) or "no description")[:_ROLE_SUMMARY_CHARS]

    # Class inheritance.
    class_list: list[tuple[str, list[str]]] = []
    for m in re.finditer(r'class\s+(\w+)\s*:\s*([\w,\s]+)', source):
        bases = [b.strip() for b in m.group(2).split(",") if b.strip()]
        class_list.append((m.group(1), bases))

    # Using statements.
    imports = [m.lower() for m in re.findall(r'^using\s+([\w.]+);', source, re.MULTILINE)]

    return role, class_list, imports


def _extract_go_role(path: Path) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """Return (role, class_list, import_list) for Go files via regex."""
    source = path.read_text(encoding="utf-8", errors="replace")

    # Package-level comment block immediately before 'package'.
    pkg_comment = re.search(r'^((?:[ \t]*//[^\n]*\n)+)[ \t]*package\s+\w+', source, re.MULTILINE)
    if pkg_comment:
        comment = re.sub(r'^[ \t]*//\s?', '', pkg_comment.group(1), flags=re.MULTILINE).strip()
        first = comment.split("\n")[0]
        role = first[:_ROLE_SUMMARY_CHARS]
    else:
        structs = re.findall(r'^type\s+(\w+)\s+struct', source, re.MULTILINE)
        funcs = re.findall(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?([A-Z]\w+)\(', source, re.MULTILINE)
        parts = []
        if structs:
            parts.append(f"defines {', '.join(structs[:4])}")
        if funcs:
            parts.append(f"exposes {', '.join(funcs[:5])}")
        role = ("; ".join(parts) or "no description")[:_ROLE_SUMMARY_CHARS]

    # Go has no class inheritance; use structs as "classes" with no bases.
    structs_list = re.findall(r'^type\s+(\w+)\s+struct', source, re.MULTILINE)
    class_list = [(s, []) for s in structs_list]

    # Import paths (last segment as module name).
    imports = [
        m.rstrip('"').split("/")[-1].lower()
        for m in re.findall(r'"([^"]+)"', source)
        if "/" in m or not m.startswith(".")
    ]

    return role, class_list, imports


def _generic_role(path: Path) -> str:
    """First non-blank, non-comment line as a fallback role."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "//", "/*", "*", "<!--", "--", ";")):
                return stripped[:_ROLE_SUMMARY_CHARS]
    except Exception:
        pass
    return "no description"


# ── Language / project detection ───────────────────────────────────────────────

_LANG_GROUPS: list[tuple[str, set[str]]] = [
    ("Python",          {".py"}),
    ("JavaScript/TypeScript", {".js", ".ts", ".tsx", ".jsx"}),
    ("C#",              {".cs"}),
    ("Go",              {".go"}),
    ("Java",            {".java"}),
    ("C/C++",           {".cpp", ".c", ".h"}),
    ("Rust",            {".rs"}),
    ("Ruby",            {".rb"}),
]


def _detect_language(ext_counts: Counter) -> str:
    best_lang, best_count = "unknown", 0
    for lang, exts in _LANG_GROUPS:
        count = sum(ext_counts.get(e, 0) for e in exts)
        if count > best_count:
            best_lang, best_count = lang, count
    return best_lang


def _detect_project_type(repo_root: Path, language: str) -> str:
    if (repo_root / "go.mod").exists():
        return "go"
    if any(repo_root.glob("*.csproj")) or any(repo_root.glob("*.sln")):
        return "unity" if (repo_root / "Assets").is_dir() else "dotnet"
    if (repo_root / "package.json").exists():
        return "node"
    for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
        if (repo_root / marker).exists():
            return "python"
    # Fallback to dominant language name lowercased.
    return language.split("/")[0].lower()


# ── Pattern detection ──────────────────────────────────────────────────────────

def _detect_patterns(intermediate: list[dict]) -> list[str]:
    """Infer architectural patterns from the aggregated scan data."""
    patterns: list[str] = []

    # Count base class occurrences across all files.
    base_counter: Counter = Counter()
    for entry in intermediate:
        for _cls, bases in entry.get("classes", []):
            for base in bases:
                base_counter[base] += 1

    for base, count in base_counter.most_common(5):
        if count >= 3 and base not in ("object", "Object", ""):
            patterns.append(f"Multiple classes inherit {base} ({count} found)")

    # Framework detection from import lists.
    import_counter: Counter = Counter()
    for entry in intermediate:
        for imp in entry.get("imports", []):
            if imp in _FRAMEWORK_IMPORTS:
                import_counter[imp] += 1

    for imp, count in import_counter.most_common(3):
        if count >= 2:
            patterns.append(f"Framework: {_FRAMEWORK_IMPORTS[imp]}")

    # Test layout detection.
    test_files = sum(
        1 for entry in intermediate
        if "test" in str(entry.get("path", "")).lower()
    )
    if test_files >= 3:
        patterns.append(f"Test suite present ({test_files} test files)")

    # Unity MonoBehaviour.
    mono_count = sum(
        1 for entry in intermediate
        for _cls, bases in entry.get("classes", [])
        if "MonoBehaviour" in bases
    )
    if mono_count >= 2:
        patterns.append(f"Unity MonoBehaviour pattern ({mono_count} components)")

    return patterns


# ── File collection ────────────────────────────────────────────────────────────

def _collect_files(repo_root: Path, logger: logging.Logger) -> list[Path]:
    """Walk the repo and return up to _MAX_SCAN_FILES source files."""
    # Group files by parent directory so we can sample deep dirs fairly.
    by_dir: dict[Path, list[Path]] = {}
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        # Skip ignored directory components.
        parts = path.relative_to(repo_root).parts
        if any(p in _IGNORE_DIRS for p in parts):
            continue
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        by_dir.setdefault(path.parent, []).append(path)

    # Sort directories by depth so shallow ones are fully included first.
    dirs_by_depth = sorted(by_dir.keys(), key=lambda p: len(p.relative_to(repo_root).parts))

    collected: list[Path] = []
    for d in dirs_by_depth:
        files = by_dir[d]
        depth = len(d.relative_to(repo_root).parts)
        remaining = _MAX_SCAN_FILES - len(collected)
        if remaining <= 0:
            break
        # Shallow dirs (depth ≤ 2) always fully included; deeper dirs sampled.
        limit = len(files) if depth <= 2 else min(3, remaining)
        collected.extend(files[:limit])

    if len(collected) >= _MAX_SCAN_FILES:
        logger.warning(
            "Onboarding scan: large project — capped at %d files. "
            "Use --skip-onboarding-scan to bypass.",
            _MAX_SCAN_FILES,
        )

    return collected[:_MAX_SCAN_FILES]


# ── Main scanner class ─────────────────────────────────────────────────────────

class OnboardingScanner:
    """Scans an existing project once to pre-populate project knowledge.

    Usage::

        scanner = OnboardingScanner(repo_root, logger)
        knowledge = scanner.run(knowledge)   # mutates and returns knowledge
        save_knowledge(knowledge, repo_root)
    """

    def __init__(self, repo_root: Path, logger: logging.Logger) -> None:
        self._root = repo_root
        self._logger = logger

    def run(self, knowledge: dict) -> dict:
        """Scan source files and enrich the knowledge dict in-place."""
        t0 = time.monotonic()
        files = _collect_files(self._root, self._logger)
        self._logger.info("Onboarding scan: examining %d source files…", len(files))

        # Determine dominant language from extension counts.
        ext_counts: Counter = Counter(f.suffix.lower() for f in files)
        language = _detect_language(ext_counts)
        project_type = _detect_project_type(self._root, language)

        intermediate: list[dict] = []
        skipped = 0

        for path in files:
            result = self._scan_file(path, language)
            if result is None:
                skipped += 1
                continue
            intermediate.append(result)
            rel = path.relative_to(self._root).as_posix()
            knowledge["files"][rel] = {
                "role": result["role"],
                "task_type": "scan",
                "last_modified": _today(),
                "created": _today(),
            }

        if skipped:
            self._logger.debug("Onboarding scan: skipped %d unparseable files", skipped)

        # Detect patterns from aggregated data.
        new_patterns = _detect_patterns(intermediate)
        existing = set(knowledge.get("patterns", []))
        knowledge["patterns"] = sorted(existing | set(new_patterns))

        # Update project metadata.
        pattern_summary = f" Patterns: {', '.join(new_patterns[:3])}." if new_patterns else ""
        knowledge["project"].update({
            "language": language,
            "type": project_type,
            "summary": (
                f"{project_type.capitalize()} project ({language}), "
                f"{len(knowledge['files'])} source files scanned.{pattern_summary}"
            )[:300],
            "scanned": True,
        })

        elapsed = round(time.monotonic() - t0, 1)
        self._logger.info(
            "Onboarding scan complete in %.1fs — %d files registered, "
            "type=%s, language=%s, patterns=%d",
            elapsed,
            len(knowledge["files"]),
            project_type,
            language,
            len(new_patterns),
        )
        return knowledge

    def _scan_file(self, path: Path, dominant_language: str) -> Optional[dict]:
        """Extract role and metadata from a single file. Returns None on failure."""
        try:
            ext = path.suffix.lower()
            if ext == ".py":
                role, classes, imports = _extract_python_role(path)
            elif ext in {".js", ".ts", ".tsx", ".jsx"}:
                role, classes, imports = _extract_js_ts_role(path)
            elif ext == ".cs":
                role, classes, imports = _extract_csharp_role(path)
            elif ext == ".go":
                role, classes, imports = _extract_go_role(path)
            else:
                role = _generic_role(path)
                classes, imports = [], []

            return {
                "path": path,
                "role": role,
                "classes": classes,   # list[tuple[str, list[str]]]
                "imports": imports,   # list[str]
            }
        except Exception as exc:
            self._logger.debug("Onboarding scan: skipping %s — %s", path.name, exc)
            return None
