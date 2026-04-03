# bridge.spec — PyInstaller onefile build
#
# Produces: dist/bridge-app.exe  (single file, no _internal folder)
#
# Usage:
#   pip install pyinstaller flask pywebview
#   pyinstaller bridge.spec --clean
#
# Or just run: build.bat
#
# The exe embeds a Flask server + pywebview window (Edge WebView2).
# aider, ollama, and a supervisor CLI (codex/claude) must still be installed
# on the target machine — they are external tools the bridge shells out to.

block_cipher = None

# Collect all pywebview runtime files (Edge WebView2 glue DLLs, etc.)
try:
    from PyInstaller.utils.hooks import collect_all, collect_submodules
    _wv_datas, _wv_bins, _wv_hidden = collect_all("webview")
    _wv_mods = collect_submodules("webview")
except Exception:
    _wv_datas, _wv_bins, _wv_hidden, _wv_mods = [], [], [], []

a = Analysis(
    ["launch_ui.py"],
    pathex=["."],
    binaries=[*_wv_bins],
    datas=[
        # Include main.py so bridge subprocess can import it
        ("main.py", "."),

        # Include all Python packages
        ("ui", "ui"),
        ("supervisor", "supervisor"),
        ("executor", "executor"),
        ("parser", "parser"),
        ("validator", "validator"),
        ("context", "context"),
        ("models", "models"),
        ("utils", "utils"),
        ("bridge_logging", "bridge_logging"),
        ("planner", "planner"),

        ("logs", "logs"),
        *_wv_datas,
    ],
    hiddenimports=[
        # ── Flask stack ──────────────────────────────────────────────────────
        "flask",
        "flask.json.provider",
        "jinja2",
        "jinja2.ext",
        "werkzeug",
        "werkzeug.serving",
        "werkzeug.routing",
        "click",
        "itsdangerous",
        "markupsafe",
        # ── Bridge CLI (imported by launch_ui.py --_bridge-run) ──────────
        "main",
        # ── UI package ───────────────────────────────────────────────────────
        "ui",
        "ui.app",
        "ui.bridge_runner",
        "ui.setup_checker",
        "ui.state_store",
        # ── Bridge packages (loaded via --_bridge-run path) ─────────────────
        "supervisor",
        "supervisor.agent",
        "executor",
        "executor.aider_runner",
        "executor.diff_collector",
        "parser",
        "parser.task_parser",
        "validator",
        "validator.validator",
        "context",
        "context.file_selector",
        "context.idea_loader",
        "context.repo_scanner",
        "models",
        "models.task",
        "utils",
        "utils.command_resolution",
        "utils.token_tracker",
        "utils.report_generator",
        "utils.run_diagnostics",
        "utils.onboarding_scanner",
        "utils.project_knowledge",
        "utils.relay_formatter",
        "utils.manual_supervisor",
        "utils.project_type_prompt",
        "utils.checkpoint",
        "utils.telemetry",
        "utils.model_advisor",
        "context.project_understanding",
        "bridge_logging",
        "bridge_logging.logger",
        "planner",
        "planner.codex_client",
        "planner.fallback_planner",
        # ── pywebview Windows backends ───────────────────────────────────────
        *_wv_hidden,
        *_wv_mods,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "scipy",
        "test",
        "unittest",
        "xmlrpc",
        "pydoc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,   # ← onefile: embed all binaries directly in the exe
    a.zipfiles,
    a.datas,      # ← onefile: embed all datas directly in the exe
    [],
    name="bridge-app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress — requires upx.exe on PATH (optional, skip if absent)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # ← no CMD window when double-clicked
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # replace with "assets/icon.ico" if you have one
)
# NOTE: No COLLECT step — onefile mode embeds everything in the single exe.
