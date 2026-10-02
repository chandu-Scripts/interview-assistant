; Inno Setup script. Build with:  python scripts/build_installer.py
; (which passes /DMyAppVersion=<version>). Installs per-user by default: no admin rights needed.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Interview Assistant"
#define MyAppExe "InterviewAssistant.exe"

[Setup]
AppId={{6F1D5B7E-3C0A-4D9B-9B57-1A6E0C5E2A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Interview Assistant
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=InterviewAssistant-Setup-{#MyAppVersion}
SetupIconFile=..\packaging\app.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\InterviewAssistant\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function CmdLineParamExists(const Param: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Param) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

// Uninstall never touches your data silently. Interactive uninstalls ask; a silent uninstall keeps
// data unless the command line contains /PURGE.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  Purge: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    if UninstallSilent then
      Purge := CmdLineParamExists('/PURGE')
    else
      Purge := MsgBox('Also delete your saved settings, question history and stored API keys from this PC?',
                      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if Purge then
      Exec(ExpandConstant('{app}\{#MyAppExe}'), '--purge-data', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
