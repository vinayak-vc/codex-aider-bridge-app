; installer.iss — Inno Setup 6 installer script for Codex-Aider Bridge
;
; Pre-requisite: Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
; Build command:  iscc installer.iss
; Or just run:    build.bat  (calls iscc automatically if found)
;
; Output: dist\CodexAiderBridgeSetup.exe
;
; What the installer does:
;   1. Copies bridge-app.exe to %ProgramFiles%\Codex-Aider Bridge\
;   2. Creates a Start Menu shortcut
;   3. Creates an optional Desktop shortcut (ticked by default)
;   4. Registers an Add/Remove Programs entry with uninstall support
;   5. Optionally launches the app after installation

[Setup]
AppName=Codex-Aider Bridge
AppVersion=1.0.0
AppPublisher=Vinayak
AppPublisherURL=https://github.com/vinayak-vc/codex-aider-bridge-app
AppSupportURL=https://github.com/vinayak-vc/codex-aider-bridge-app/issues
AppUpdatesURL=https://github.com/vinayak-vc/codex-aider-bridge-app/releases

; Default install to per-user Program Files (no admin required by default)
DefaultDirName={autopf}\Codex-Aider Bridge
DefaultGroupName=Codex-Aider Bridge
AllowNoIcons=yes

; Request admin only if user picks a system-wide path
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Output
OutputDir=dist
OutputBaseFilename=CodexAiderBridgeSetup

; Compression
Compression=lzma2/ultra64
SolidCompression=yes

; Modern wizard style (Windows 10/11 look)
WizardStyle=modern
WizardSizePercent=120

; Uninstall icon
UninstallDisplayIcon={app}\bridge-app.exe
UninstallDisplayName=Codex-Aider Bridge

; Minimum Windows version: Windows 10 (Edge WebView2 required)
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
  Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"; \
  Flags: checked

[Files]
; The single bundled exe — everything is inside it
Source: "dist\bridge-app.exe"; \
  DestDir: "{app}"; \
  Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\Codex-Aider Bridge"; \
  Filename: "{app}\bridge-app.exe"; \
  Comment: "Launch the Codex-Aider Bridge UI"

Name: "{group}\Uninstall Codex-Aider Bridge"; \
  Filename: "{uninstallexe}"

; Desktop (optional, controlled by Tasks above)
Name: "{autodesktop}\Codex-Aider Bridge"; \
  Filename: "{app}\bridge-app.exe"; \
  Comment: "Launch the Codex-Aider Bridge UI"; \
  Tasks: desktopicon

[Run]
; Offer to launch the app after installation completes
Filename: "{app}\bridge-app.exe"; \
  Description: "Launch Codex-Aider Bridge now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the data folder created at runtime (settings, history)
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
