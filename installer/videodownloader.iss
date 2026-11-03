; VideoDownloader Windows Setup Script (Inno Setup 6.3+)
; Invoked automatically by `python scripts/build_win_exe.py`:
;   ISCC.exe /DMyAppVersion=3.0.0 installer\videodownloader.iss

#define MyAppName "VideoDownloader"
#ifndef MyAppVersion
  #define MyAppVersion "3.0.0"
#endif
#define MyAppExeName "VideoDownloader.exe"

[Setup]
AppId={{8F3A2C1E-7B4D-4E9A-9C12-5D8E3F1A6B40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppName} Contributors
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=VideoDownloader-Setup-{#MyAppVersion}-x64
SetupIconFile=..\videodownloader\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.CreateDesktopShortcut=创建桌面快捷方式(&D)
english.CreateDesktopShortcut=Create a &desktop shortcut
chinesesimplified.AutoStartMinimized=开机自动启动并静默驻留系统托盘 (启用 Ctrl+Shift+D 全局秒捕)
english.AutoStartMinimized=Launch minimized in system tray on Windows startup
chinesesimplified.LaunchNow=立即启动 {#MyAppName}
english.LaunchNow=Launch {#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopShortcut}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart"; Description: "{cm:AutoStartMinimized}"; Flags: unchecked

[InstallDelete]
; Clean previous PyInstaller onedir `_internal` folder on upgrade so removed modules never linger
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Never ship runtime/user data created when the onedir bundle was launched locally (DB, logs, downloads, hot-updated bins)
Source: "..\dist\VideoDownloader\*"; DestDir: "{app}"; Excludes: "\data\*,\logs\*,\downloads\*,\bin\*,\config\*,\portable.flag,*.db,*.db-wal,*.db-shm,cookies*.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    UserDataDir := ExpandConstant('{localappdata}\{#MyAppName}');
    if DirExists(UserDataDir) and (not UninstallSilent) then
    begin
      if SuppressibleMsgBox(
           '是否同时清理软件配置、日志与历史缓存 (' + UserDataDir + ')？' + #13#10 + #13#10 +
           '注：您已下载到本地文件夹中的音视频文件将完好保留，绝不会被删除。',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(UserDataDir, True, True, True);
      end;
    end;
  end;
end;
