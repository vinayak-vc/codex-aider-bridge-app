"""Deep scanner — extract function signatures and data shapes from source files.

Enriches project_knowledge.json with structural details so the supervisor
can generate precise task instructions that match the actual code.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Max chars to read per file (prevent OOM on huge files)
_MAX_FILE_CHARS = 50_000

# Languages and their function/class extraction patterns
_PATTERNS: dict[str, list[tuple[str, str]]] = {
    ".js": [
        ("function", r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)"),
        ("arrow", r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?([^)=]*)\)?\s*=>"),
        ("method", r"(\w+)\s*:\s*(?:async\s+)?function\s*\w*\s*\(([^)]*)\)"),
        ("class", r"class\s+(\w+)"),
    ],
    ".jsx": [
        ("component", r"(?:function|const)\s+(\w+)\s*\(([^)]*)\)"),
        ("hook", r"(?:const|let)\s+\[(\w+),\s*set\w+\]\s*=\s*use(\w+)"),
    ],
    ".ts": [
        ("function", r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)"),
        ("interface", r"(?:export\s+)?interface\s+(\w+)"),
        ("type", r"(?:export\s+)?type\s+(\w+)"),
        ("class", r"(?:export\s+)?class\s+(\w+)"),
    ],
    ".tsx": [
        ("component", r"(?:export\s+)?(?:function|const)\s+(\w+)\s*\(([^)]*)\)"),
    ],
    ".py": [
        ("function", r"^def\s+(\w+)\s*\(([^)]*)\)"),
        ("class", r"^class\s+(\w+)"),
        ("method", r"^\s+def\s+(\w+)\s*\(self[^)]*\)"),
    ],
}

# Object literal patterns — detect data shapes
_OBJECT_PATTERNS: dict[str, str] = {
    ".js": r"(?:const|let|var)\s+(\w+)\s*=\s*\{",
    ".jsx": r"(?:const|let|var)\s+(\w+)\s*=\s*\{",
    ".py": r"(\w+)\s*=\s*\{",
}


def scan_file_signatures(file_path: Path) -> Optional[dict]:
    """Extract function signatures and key patterns from a source file.

    Returns a dict with:
      - functions: [{name, params, line}]
      - classes: [name]
      - data_shapes: [{name, keys}]  (top-level object literals with their keys)
      - line_count: int
    """
    suffix = file_path.suffix.lower()
    patterns = _PATTERNS.get(suffix)
    if not patterns:
        # Also match .ts/.tsx via .js patterns
        if suffix in (".mjs", ".cjs"):
            patterns = _PATTERNS[".js"]
        else:
            return None

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
    except OSError:
        return None

    lines = content.splitlines()
    functions: list[dict] = []
    classes: list[str] = []

    for line_num, line in enumerate(lines, 1):
        for kind, pattern in patterns:
            m = re.search(pattern, line)
            if not m:
                continue
            if kind in ("class", "interface", "type"):
                classes.append(m.group(1))
            elif kind == "hook":
                functions.append({"name": f"use{m.group(2)}", "params": m.group(1), "line": line_num})
            else:
                name = m.group(1)
                params = m.group(2).strip() if m.lastindex >= 2 else ""
                # Skip private/internal
                if name.startswith("_") and kind != "method":
                    continue
                functions.append({"name": name, "params": params[:80], "line": line_num})

    # Extract top-level data shapes (object literals)
    data_shapes: list[dict] = []
    obj_pattern = _OBJECT_PATTERNS.get(suffix)
    if obj_pattern:
        for m in re.finditer(obj_pattern, content):
            obj_name = m.group(1)
            # Find the keys inside the object (first 10 keys)
            start = m.end()
            depth = 1
            pos = start
            keys: list[str] = []
            while pos < len(content) and depth > 0 and len(keys) < 10:
                ch = content[pos]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                elif depth == 1:
                    key_match = re.match(r"\s*(\w+)\s*:", content[pos:])
                    if key_match:
                        keys.append(key_match.group(1))
                        pos += key_match.end()
                        continue
                pos += 1
            if keys:
                data_shapes.append({"name": obj_name, "keys": keys})

    return {
        "functions": functions[:30],  # cap to prevent huge payloads
        "classes": classes[:10],
        "data_shapes": data_shapes[:10],
        "line_count": len(lines),
    }


def scan_project_signatures(
    repo_root: Path,
    max_files: int = 50,
) -> dict[str, dict]:
    """Scan all source files in the project and return signatures per file.

    Returns: {relative_path: {functions, classes, data_shapes, line_count}}
    """
    result: dict[str, dict] = {}
    count = 0

    extensions = set(_PATTERNS.keys()) | {".mjs", ".cjs"}

    for file_path in sorted(repo_root.rglob("*")):
        if count >= max_files:
            break
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        # Skip common ignored directories
        rel = str(file_path.relative_to(repo_root))
        if any(part in rel for part in ("node_modules", "__pycache__", ".venv", "dist", "build", ".git")):
            continue

        sigs = scan_file_signatures(file_path)
        if sigs and (sigs["functions"] or sigs["classes"] or sigs["data_shapes"]):
            result[rel.replace("\\", "/")] = sigs
            count += 1

    return result


def signatures_to_context(signatures: dict[str, dict]) -> str:
    """Convert scanned signatures to a compact text block for prompt injection."""
    lines: list[str] = ["CODE STRUCTURE (function signatures and data shapes):"]

    for file_path, info in sorted(signatures.items()):
        file_lines: list[str] = []

        for fn in info.get("functions", [])[:8]:
            params = fn.get("params", "")
            if params:
                file_lines.append(f"    {fn['name']}({params}) @{fn['line']}")
            else:
                file_lines.append(f"    {fn['name']}() @{fn['line']}")

        for cls in info.get("classes", [])[:3]:
            file_lines.append(f"    class {cls}")

        for ds in info.get("data_shapes", [])[:3]:
            keys_str = ", ".join(ds["keys"][:6])
            if len(ds["keys"]) > 6:
                keys_str += ", ..."
            file_lines.append(f"    {ds['name']} = {{{keys_str}}}")

        if file_lines:
            lines.append(f"  {file_path} ({info.get('line_count', 0)} lines):")
            lines.extend(file_lines)

    return "\n".join(lines)
