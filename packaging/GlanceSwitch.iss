; Inno Setup script - per-user install, no administrator rights needed.
; Build: iscc /DAppVersion=0.1.0 packaging\GlanceSwitch.iss  (after PyInstaller)

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1C2B7E-3D8A-4E51-9B0C-7A2D4E9F1C33}
AppName=GlanceSwitch
AppVersion={#AppVersion}
AppPublisher=GlanceSwitch for Windows
DefaultDirName={localappdata}\Programs\GlanceSwitch
DefaultGroupName=GlanceSwitch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=GlanceSwitch-Setup-{#AppVersion}
SetupIconFile=glance_switch.ico
UninstallDisplayIcon={app}\GlanceSwitch.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Lancer GlanceSwitch au démarrage de Windows"; Flags: unchecked

[Files]
Source: "..\dist\GlanceSwitch\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\GlanceSwitch"; Filename: "{app}\GlanceSwitch.exe"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GlanceSwitch"; ValueData: """{app}\GlanceSwitch.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\GlanceSwitch.exe"; Description: "Lancer GlanceSwitch"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM GlanceSwitch.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\GlanceSwitch"
