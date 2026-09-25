; Inno Setup script: MT5 Trading Workstation (SPEC I2)
; Per-user install (no admin rights) so the app runs at the same privilege
; level as the MT5 terminal.

#define MyAppName "MT5 Trading Workstation"
#define MyAppExeName "MT5TradingWorkstation.exe"

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7A1F2C6B-5C1D-4E6A-9B2F-3E8D4A7C9F10}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=mohmdstag7-cmd
DefaultDirName={localappdata}\Programs\MT5TradingWorkstation
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=MT5TradingWorkstation-Setup-{#AppVersion}
OutputDir=..\dist\installer
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MT5TradingWorkstation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
