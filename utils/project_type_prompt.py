"""Interactive project-type selection prompt shown at first bridge run.

Displayed once when:
  - project_knowledge["project"]["type"] is empty, AND
  - --project-type flag was not passed, AND
  - stdin is a terminal (not piped / CI)

The chosen type is saved to project_knowledge so the prompt never appears
again for the same repo.  Pass --project-type <key> to skip it entirely.
"""
from __future__ import annotations

import sys
from typing import Optional


# ── Catalogue ─────────────────────────────────────────────────────────────────

# key → (display name, description, bridge hints)
PROJECT_TYPES: dict[str, tuple[str, str, str]] = {
    "unity":      ("Unity",              "C# · GameObjects · Scenes · MonoBehaviour",
                   "Enables .meta whitelist, Unity MCP compilation check"),
    "godot":      ("Godot",              "GDScript / C# · Nodes · Scenes",
                   "Godot Engine project"),
    "unreal":     ("Unreal Engine",      "C++ / Blueprints · Actors · UAssets",
                   "Unreal Engine project"),
    "python":     ("Python",             "Scripts / packages · requirements.txt / pyproject.toml",
                   "Enables py_compile syntax check"),
    "typescript": ("TypeScript / React", "TS · React · Next.js · Vite",
                   "Enables tsc --noEmit check"),
    "javascript": ("Node.js / JS",       "Node · Express · vanilla JS",
                   "Enables node --check syntax check"),
    "csharp":     (".NET / C#",          ".sln / .csproj · ASP.NET · MAUI · console",
                   "Enables dotnet build check"),
    "flutter":    ("Flutter / Dart",     "Dart · Flutter widgets · pubspec.yaml",
                   "Flutter project"),
    "rust":       ("Rust",               "Cargo.toml · crates",
                   "Rust project"),
    "go":         ("Go",                 "go.mod · packages",
                   "Go project"),
    "other":      ("Other / Auto-detect","Let the bridge figure it out",
                   "Falls back to auto-detection from repo files"),
}

# Display order (ordered dict iteration in Python 3.7+ is insertion-ordered)
_ORDERED_KEYS: list[str] = list(PROJECT_TYPES.keys())


# ── Public API ────────────────────────────────────────────────────────────────

def prompt_project_type() -> Optional[str]:
    """Display an interactive menu and return the chosen type key.

    Returns None if stdin is not a terminal (CI / piped input).
    """
    if not sys.stdin.isatty():
        return None

    _print_banner()

    while True:
        raw = input("\nEnter number (or type the key directly, e.g. 'unity'): ").strip().lower()

        # Accept numeric input
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(_ORDERED_KEYS):
                chosen = _ORDERED_KEYS[idx]
                _confirm(chosen)
                return chosen
            else:
                print(f"  [x]Please enter a number between 1 and {len(_ORDERED_KEYS)}.")
                continue

        # Accept key input directly
        if raw in PROJECT_TYPES:
            _confirm(raw)
            return raw

        # Fuzzy: prefix match
        matches = [k for k in _ORDERED_KEYS if k.startswith(raw)]
        if len(matches) == 1:
            _confirm(matches[0])
            return matches[0]

        print(f"  [x]'{raw}' not recognised. Try a number or one of: {', '.join(_ORDERED_KEYS)}")


def describe(type_key: str) -> str:
    """Return a human-readable description for a type key."""
    entry = PROJECT_TYPES.get(type_key)
    if not entry:
        return type_key
    return f"{entry[0]} — {entry[1]}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_banner() -> None:
    width = 62
    sep = "-" * width
    print("\n" + sep)
    print("  [?]  What type of project is this?")
    print(sep)
    print("  The bridge uses this to pick the right validator, ignore")
    print("  the right files, and generate better task plans.")
    print(sep)

    for i, key in enumerate(_ORDERED_KEYS, start=1):
        name, desc, hint = PROJECT_TYPES[key]
        print(f"  {i:>2}.  {name:<22} {desc}")

    print(sep)
    print("  This is saved and won't be asked again for this repo.")
    print("  Override any time with:  --project-type <key>")
    print(sep)


def _confirm(key: str) -> None:
    name, desc, hint = PROJECT_TYPES[key]
    print(f"\n  [v]  Selected: {name} - {desc}")
    if hint:
        print(f"       Bridge hint: {hint}")
