; DTK Windows installer (Inno Setup 6)
; Build: pyinstaller packaging/dtk.spec --noconfirm --clean --distpath build-dist --workpath build-work
;        then: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\dtk.iss
; Data is NOT bundled: the app downloads it from GitHub Releases on first run.

#define MyAppName "Danbooru Tag Knowledge"
#define MyAppShortName "DTK"
#define MyAppVersion "0.4.0"
#define MyAppPublisher "chenxluo"
#define MyAppURL "https://github.com/chenxluo/dtk"
#define MyAppExeName "DTK.exe"

[Setup]
AppId={{7B3D9C4A-52E8-4B6D-9A1F-DTKTAG000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppShortName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\installer
OutputBaseFilename=DTK-setup-{#MyAppVersion}
SetupIconFile=dtk.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Files]
Source: "..\build-dist\DTK\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\DTK 命令行"; Filename: "{app}\dtk-cli.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
