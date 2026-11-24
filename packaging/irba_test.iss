; irba test — Inno Setup kurulum betiği
#define AppName "irba test"
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppExe "irba test.exe"
#ifndef DistDir
  #define DistDir "..\dist\irba test"
#endif
#ifndef OutDir
  #define OutDir "..\dist_setup"
#endif

#ifndef AppGuid
  #define AppGuid "6B1E2C7A-4D8F-4C1B-9A63-1F0B9E2D7A51"
#endif

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=irba
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=irba_test_Setup_{#AppVersion}
SetupIconFile=..\assets\irba.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek kısayollar:"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} programını kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{#AppName} programını şimdi başlat"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: IsUpdate

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;

[UninstallRun]
; Yalnızca bu programın kendi adb/scrcpy süreçlerini kapat (başka programların ADB sunucusuna dokunma)
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process adb,scrcpy -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' }} | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "KillOwn"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
