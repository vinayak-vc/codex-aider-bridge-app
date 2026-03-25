# bridge.spec — PyInstaller build specification
#
# Build with:
#   pip install pyinstaller
#   pyinstaller bridge.spec --clean
#
# Output: dist/bridge-app/bridge-app.exe  (one-folder distribution)
#
# NOTE: The bundled exe still requires aider, ollama, and a supervisor CLI
# (codex / claude) to be installed on the target machine — those are external
# programs that the bridge calls out to, not Python libraries.

block_cipher = None

a = Analysis(
    ["launch_ui.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Flask templates — must be a real directory, not bytecode
        ("ui/templates", "ui/templates"),
        # Initial data directory (holds .gitkeep; runtime JSON written here)
        ("ui/data", "ui/data"),
        # Log directory placeholder
        ("logs", "logs"),
    ],
    hiddenimports=[
        # Flask and its dependencies are not always auto-discovered
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
        # UI package
        "ui",
        "ui.app",
        "ui.bridge_runner",
        "ui.setup_checker",
        "ui.state_store",
        # Bridge packages (imported transitively via --_bridge-run path)
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
        "bridge_logging",
        "bridge_logging.logger",
        "planner",
        "planner.codex_client",
        "planner.fallback_planner",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused heavy packages
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "scipy",
        "tkinter.test",
        "test",
        "unittest",
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
    [],
    exclude_binaries=True,
    name="bridge-app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress binaries (requires upx.exe on PATH, optional)
    upx_exclude=[],
    console=True,       # keep console window — useful for error messages
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # set to "icon.ico" if you add an icon file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="bridge-app",
)
