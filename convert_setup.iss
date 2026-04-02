#define MyAppName "简谱转换工具"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "志雄"
#define MyAppExeName "ConvertTool.exe"
#define MyAppCopyright "Copyright (c) 2026 志雄"
#define MyAppURL "https://github.com"

[Setup]
AppId={{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ConvertTool
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-dist
OutputBaseFilename=ConvertTool-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
ShowLanguageDialog=no
AppCopyright={#MyAppCopyright}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Dirs]
Name: "{app}\Input"
Name: "{app}\Output"

[Files]
Source: "dist\ConvertTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "package-assets\lilypond-runtime\*"; DestDir: "{app}\lilypond-runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "package-assets\audiveris-runtime\*"; DestDir: "{app}\audiveris-runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "package-assets\tessdata\*"; DestDir: "{app}\tessdata"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "jdk\*"; DestDir: "{app}\jdk"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "jianpu-ly.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "Input\Do_You_Hear_the_People_Sing.pdf"; DestDir: "{app}\Input"; Flags: ignoreversion
Source: "Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf"; DestDir: "{app}\Input"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  IsUpgradeMode: Boolean;
  IsRepairMode: Boolean;
  IsDowngradeMode: Boolean;

// 比较版本字符串，返回 -1/0/1
function CompareVersions(V1, V2: String): Integer;
var
  P1, P2, N1, N2: Integer;
  S1, S2: String;
begin
  Result := 0;
  while (V1 <> '') or (V2 <> '') do
  begin
    P1 := Pos('.', V1);
    if P1 = 0 then begin S1 := V1; V1 := ''; end
    else begin S1 := Copy(V1, 1, P1-1); V1 := Copy(V1, P1+1, Length(V1)); end;
    P2 := Pos('.', V2);
    if P2 = 0 then begin S2 := V2; V2 := ''; end
    else begin S2 := Copy(V2, 1, P2-1); V2 := Copy(V2, P2+1, Length(V2)); end;
    N1 := StrToIntDef(S1, 0);
    N2 := StrToIntDef(S2, 0);
    if N1 < N2 then begin Result := -1; Exit; end;
    if N1 > N2 then begin Result :=  1; Exit; end;
  end;
end;

function GetInstalledVersion: String;
var
  RegKey: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  if not RegQueryStringValue(HKLM64, RegKey, 'DisplayVersion', Result) then
    if not RegQueryStringValue(HKLM, RegKey, 'DisplayVersion', Result) then
      Result := '';
end;

function GetUninstallerPath: String;
var
  RegKey: String;
  S: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  S := '';
  if not RegQueryStringValue(HKLM64, RegKey, 'UninstallString', S) then
    RegQueryStringValue(HKLM, RegKey, 'UninstallString', S);
  // 去除路径两端的引号
  if (Length(S) >= 2) and (S[1] = '"') then
  begin
    Delete(S, 1, 1);
    if Pos('"', S) > 0 then
      S := Copy(S, 1, Pos('"', S) - 1);
  end;
  Result := S;
end;

function InitializeSetup: Boolean;
var
  InstalledVer: String;
  MsgResult: Integer;
  UninstallerPath: String;
  ResultCode: Integer;
begin
  Result := True;
  InstalledVer := GetInstalledVersion;
  if InstalledVer = '' then Exit; // 全新安装，直接继续

  case CompareVersions(InstalledVer, '{#MyAppVersion}') of
    -1: // 已装旧版 → 升级
    begin
      IsUpgradeMode := True;
      if MsgBox(
           '检测到已安装旧版本 ' + InstalledVer + '，即将升级到新版本 {#MyAppVersion}。' + #13#10#13#10 +
           '是否继续升级？',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
        Result := False;
    end;
    0: // 同版本 → 修复/卸载
    begin
      IsRepairMode := True;
      MsgResult := MsgBox(
        '检测到已安装相同版本 {#MyAppVersion}。' + #13#10#13#10 +
        '请选择操作：' + #13#10 +
        '  是(Y) — 修复安装' + #13#10 +
        '  否(N) — 卸载软件' + #13#10 +
        '  取消  — 退出',
        mbConfirmation, MB_YESNOCANCEL);
      if MsgResult = IDCANCEL then
        Result := False
      else if MsgResult = IDNO then
      begin
        UninstallerPath := GetUninstallerPath;
        if UninstallerPath <> '' then
          ShellExec('', UninstallerPath, '', '', SW_SHOW, ewNoWait, ResultCode);
        Result := False;
      end;
    end;
    1: // 已装新版 → 降级警告
    begin
      IsDowngradeMode := True;
      if MsgBox(
           '警告：当前已安装更高版本 ' + InstalledVer + '，即将安装旧版本 {#MyAppVersion}。' + #13#10#13#10 +
           '降级可能导致功能异常或数据损坏。' + #13#10 +
           '是否仍要继续？',
           mbError, MB_YESNO or MB_DEFBUTTON2) = IDNO then
        Result := False;
    end;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (IsUpgradeMode or IsRepairMode or IsDowngradeMode) and (PageID = wpSelectTasks);
end;
