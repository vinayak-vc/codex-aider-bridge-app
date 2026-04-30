"""
Thin launcher for scripts/ensure_services.py.
Uses __file__ to resolve the path so this works regardless of the shell's
working directory — no hardcoded machine paths needed in settings.json.
"""
import os
import sys
import subprocess

here   = os.path.dirname(os.path.abspath(__file__))          # .claude/
target = os.path.normpath(os.path.join(here, '..', 'scripts', 'ensure_services.py'))

sys.exit(subprocess.run([sys.executable, target]).returncode)
