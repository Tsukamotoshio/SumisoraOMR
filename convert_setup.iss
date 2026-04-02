#define MyAppName "简谱转换工具"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "Tsukamotoshio"
#define MyAppExeName "ConvertTool.exe"
#define MyAppCopyright "Copyright (c) 2026 Tsukamotoshio"
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

[UninstallDelete]
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  IsUpgradeMode: Boolean;
  IsRepairMode: Boolean;
  IsDowngradeMode: Boolean;
  DesktopIconCheckbox: TNewCheckBox;
  InstallCompleted: Boolean;

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
  RegKey, S: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  S := '';
  if not RegQueryStringValue(HKLM64, RegKey, 'UninstallString', S) then
    RegQueryStringValue(HKLM, RegKey, 'UninstallString', S);
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
begin
  Result := True;
  InstalledVer := GetInstalledVersion;
  if InstalledVer = '' then Exit;

  case CompareVersions(InstalledVer, '{#MyAppVersion}') of
    -1: // 旧版已安装 → 升级
    begin
      IsUpgradeMode := True;
      if MsgBox(
           '检测到已安装旧版本 ' + InstalledVer + '。' + #13#10#13#10 +
           '点击"是"将自动卸载旧版并安装 {#MyAppVersion}，' + #13#10 +
           'Input / Output 文件夹中的内容不会被删除。' + #13#10#13#10 +
           '是否继续升级？',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
        Result := False;
    end;
    0: // 同版本 → 修复或卸载
    begin
      IsRepairMode := True;
      MsgResult := MsgBox(
        '已安装相同版本 {#MyAppVersion}。' + #13#10#13#10 +
        '  是(Y) — 重新安装（修复损坏的文件）' + #13#10 +
        '  否(N) — 卸载此软件' + #13#10 +
        '  取消   — 退出',
        mbConfirmation, MB_YESNOCANCEL);
      if MsgResult = IDCANCEL then
        Result := False
      else if MsgResult = IDNO then
      begin
        ShellExec('', GetUninstallerPath, '/SILENT', '', SW_SHOW, ewNoWait, MsgResult);
        Result := False;
      end;
    end;
    1: // 更高版本已安装 → 降级警告
    begin
      IsDowngradeMode := True;
      if MsgBox(
           '警告：当前已安装更高版本 ' + InstalledVer + '。' + #13#10 +
           '降级到 {#MyAppVersion} 可能导致功能异常。' + #13#10#13#10 +
           '是否仍要继续？',
           mbError, MB_YESNO or MB_DEFBUTTON2) = IDNO then
        Result := False;
    end;
  end;
end;

// 升级/降级时在安装文件复制前静默卸载旧版（行业标准做法）
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UninstallerPath: String;
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  if IsUpgradeMode or IsDowngradeMode then
  begin
    UninstallerPath := GetUninstallerPath;
    if (UninstallerPath <> '') and FileExists(UninstallerPath) then
    begin
      if not Exec(UninstallerPath, '/VERYSILENT /NORESTART', '',
                  SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        Result := '旧版本自动卸载失败（错误码 ' + IntToStr(ResultCode) + '），请手动卸载后重试。';
    end;
  end;
end;

// 快捷方式选项移至安装完成页，始终跳过"附加任务"选择页
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectTasks);
end;

// 在完成页添加"创建桌面快捷方式"复选框
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    DesktopIconCheckbox := TNewCheckBox.Create(WizardForm);
    DesktopIconCheckbox.Parent := WizardForm.FinishedPage;
    DesktopIconCheckbox.SetBounds(
      WizardForm.FinishedLabel.Left,
      WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12),
      WizardForm.FinishedPage.ClientWidth - WizardForm.FinishedLabel.Left * 2,
      ScaleY(17));
    DesktopIconCheckbox.Caption := '创建桌面快捷方式（{#MyAppName}）';
    DesktopIconCheckbox.Checked := True;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    InstallCompleted := True;
end;

// 安装完成（用户点击"完成"）后根据复选框创建或跳过桌面快捷方式
procedure DeinitializeSetup;
var
  ShortcutPath: String;
begin
  if not InstallCompleted then Exit;
  ShortcutPath := ExpandConstant('{autodesktop}\{#MyAppName}.lnk');
  if Assigned(DesktopIconCheckbox) and DesktopIconCheckbox.Checked then
    CreateShellLink(
      ShortcutPath,
      '{#MyAppName}',
      ExpandConstant('{app}\{#MyAppExeName}'),
      '',
      ExpandConstant('{app}'),
      '',
      0,
      SW_SHOWNORMAL)
  else
    DeleteFile(ShortcutPath);
end;
