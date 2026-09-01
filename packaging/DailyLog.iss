#define AppVersion "0.1.0"

[Setup]
AppId={{A7FEE8A4-7A5E-4A7A-9E6E-5D5C4D41A109}
AppName=Daily Log
AppVersion={#AppVersion}
AppPublisher=Daily Log contributors
DefaultDirName={localappdata}\Programs\DailyLog
DefaultGroupName=Daily Log
OutputDir=..\dist
OutputBaseFilename=DailyLog-Setup-{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
Uninstallable=yes

[Files]
Source: "..\dist\DailyLog\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Daily Log"; Filename: "{app}\DailyLog.exe"
Name: "{autodesktop}\Daily Log"; Filename: "{app}\DailyLog.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

; No [UninstallDelete] entry is intentional: user data in %LOCALAPPDATA%\DailyLog survives uninstall.
