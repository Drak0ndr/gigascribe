#define MyAppName "GigaScribe"
#define MyAppVersion "1.3.7"
#define MyAppExeName "GigaScribe.exe"
[Setup]
AppId={{8F6DAD34-1D8E-4D24-A4E2-2B1384F13B6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=GigaScribe Community Build
DefaultDirName={localappdata}\Programs\GigaScribe
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\releases
OutputBaseFilename=GigaScribe-Setup-Windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\GigaScribe.exe
ChangesAssociations=no
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"; Flags: unchecked
[Files]
Source: "..\dist\GigaScribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\GigaScribe"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\GigaScribe"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить GigaScribe"; Flags: nowait postinstall skipifsilent
