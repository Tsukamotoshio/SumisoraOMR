#define MyAppName "简谱转换工具"
#define MyAppVersion "0.2.0-preview"

#define MyAppVersionNumeric "0.2.0.0"
#define MyAppPublisher "Tsukamotoshio"
#define MyAppExeName "ConvertTool.exe"
#define MyAppCopyright "Copyright (c) 2026 Tsukamotoshio"
#define MyAppURL "https://github.com"

[Setup]
AppId={{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ConvertTool-{#MyAppVersion}
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
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}

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
Source: "package-assets\waifu2x-runtime\*"; DestDir: "{app}\waifu2x-runtime"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "jdk\*"; DestDir: "{app}\jdk"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "jianpu-ly.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "Input\Do_You_Hear_the_People_Sing.pdf"; DestDir: "{app}\Input"; Flags: ignoreversion
Source: "Input\Sunset_Waltz_By_Yoko_Shimomura-Violin.pdf"; DestDir: "{app}\Input"; Flags: ignoreversion
Source: "README_EN.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "读我.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[UninstallDelete]
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  MODE_FRESH     = 0;
  MODE_UPGRADE   = 1;
  MODE_REPAIR    = 2;
  MODE_DOWNGRADE = 3;

var
  InstallMode: Integer;
  InstalledVer: String;
  OldInstallDir: String;       // 旧版安装目录（升级时用于数据迁移）
  MaintenancePage: TWizardPage;
  RadioRepair: TNewRadioButton;
  RadioRemove: TNewRadioButton;
  DesktopIconCheckbox: TNewCheckBox;
  InstallCompleted: Boolean;

// ──────────────────────────────────────────────────────────────────────────────
// 版本比较：返回 -1 / 0 / 1
// ──────────────────────────────────────────────────────────────────────────────
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

// ──────────────────────────────────────────────────────────────────────────────
// 通过注册表（同 AppId）读取已安装版本号
// ──────────────────────────────────────────────────────────────────────────────
function GetInstalledVersion: String;
var
  RegKey: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  if not RegQueryStringValue(HKLM64, RegKey, 'DisplayVersion', Result) then
    if not RegQueryStringValue(HKLM, RegKey, 'DisplayVersion', Result) then
      if not RegQueryStringValue(HKCU, RegKey, 'DisplayVersion', Result) then
        Result := '';
end;

// 通过注册表读取旧版安装目录
function GetInstalledDir: String;
var
  RegKey: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  if not RegQueryStringValue(HKLM64, RegKey, 'InstallLocation', Result) then
    if not RegQueryStringValue(HKLM, RegKey, 'InstallLocation', Result) then
      if not RegQueryStringValue(HKCU, RegKey, 'InstallLocation', Result) then
        Result := '';
  // 去掉末尾反斜杠
  if (Length(Result) > 0) and (Result[Length(Result)] = '\') then
    Delete(Result, Length(Result), 1);
end;

function GetUninstallerPath: String;
var
  RegKey, S: String;
begin
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D5D0D1C4-0E83-4A2E-BE8E-3D5A0A93F101}_is1';
  S := '';
  if not RegQueryStringValue(HKLM64, RegKey, 'UninstallString', S) then
    if not RegQueryStringValue(HKLM, RegKey, 'UninstallString', S) then
      RegQueryStringValue(HKCU, RegKey, 'UninstallString', S);
  if (Length(S) >= 2) and (S[1] = '"') then
  begin
    Delete(S, 1, 1);
    if Pos('"', S) > 0 then
      S := Copy(S, 1, Pos('"', S) - 1);
  end;
  Result := S;
end;

// ──────────────────────────────────────────────────────────────────────────────
// 检测旧版（0.1.x）未通过注册表留下的安装目录（ConvertTool 或 ConvertTool-0.1.x）
// 用于修复"0.1版无法被识别"的问题
// ──────────────────────────────────────────────────────────────────────────────
function GetOldUnregisteredDir: String;
var
  Candidates: array[0..9] of String;
  i: Integer;
begin
  Result := '';
  Candidates[0] := ExpandConstant('{autopf}\ConvertTool');
  Candidates[1] := ExpandConstant('{autopf}\ConvertTool-0.1');
  Candidates[2] := ExpandConstant('{autopf}\ConvertTool-0.1.0');
  Candidates[3] := ExpandConstant('{autopf}\ConvertTool-0.1.1');
  Candidates[4] := ExpandConstant('{autopf}\ConvertTool-0.1.2');
  Candidates[5] := ExpandConstant('{autopf}\ConvertTool-0.1.3');
  Candidates[6] := ExpandConstant('{autopf}\ConvertTool-0.1.4');
  Candidates[7] := ExpandConstant('{pf64}\ConvertTool');
  Candidates[8] := ExpandConstant('{pf32}\ConvertTool');
  Candidates[9] := ExpandConstant('{pf}\ConvertTool');
  for i := 0 to 9 do
  begin
    if FileExists(Candidates[i] + '\ConvertTool.exe') then
    begin
      Result := Candidates[i];
      Exit;
    end;
  end;
end;

// ──────────────────────────────────────────────────────────────────────────────
// 数据迁移：将旧目录的 Input / Output / conversion_history.json 拷贝到新目录
// 使用 robocopy（Windows 7+ 内建），仅复制旧文件中比新目录更新的内容
// ──────────────────────────────────────────────────────────────────────────────
procedure MigrateUserData(FromDir: String; ToDir: String);
var
  ResultCode: Integer;
begin
  if (FromDir = '') or not DirExists(FromDir) then Exit;
  if FromDir = ToDir then Exit;

  // 迁移 Input 文件夹（仅复制尚未存在于目标的文件）
  if DirExists(FromDir + '\Input') then
    Exec(ExpandConstant('{sys}\robocopy.exe'),
         '"' + FromDir + '\Input" "' + ToDir + '\Input" /E /XC /XN /XO /NJH /NJS /NP /R:1 /W:1',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 迁移 Output 文件夹
  if DirExists(FromDir + '\Output') then
    Exec(ExpandConstant('{sys}\robocopy.exe'),
         '"' + FromDir + '\Output" "' + ToDir + '\Output" /E /XC /XN /XO /NJH /NJS /NP /R:1 /W:1',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // 迁移转换历史记录（如目标不存在则复制）
  if FileExists(FromDir + '\conversion_history.json') then
    if not FileExists(ToDir + '\conversion_history.json') then
      FileCopy(FromDir + '\conversion_history.json',
               ToDir + '\conversion_history.json', False);
end;

// ──────────────────────────────────────────────────────────────────────────────
// 初始化：检测已安装版本，处理旧版（包括 0.1.x 无注册表记录的情况）
// ──────────────────────────────────────────────────────────────────────────────
function InitializeSetup: Boolean;
begin
  Result := True;
  OldInstallDir := '';

  InstalledVer := GetInstalledVersion;
  if InstalledVer <> '' then
  begin
    // 通过同 AppId 找到已安装版本
    OldInstallDir := GetInstalledDir;
    case CompareVersions(InstalledVer, '{#MyAppVersion}') of
      -1: InstallMode := MODE_UPGRADE;
       0: InstallMode := MODE_REPAIR;
       1: InstallMode := MODE_DOWNGRADE;
    end;
    Exit;
  end;

  // 注册表中未找到 → 尝试检测旧版（0.1.x）遗留目录
  OldInstallDir := GetOldUnregisteredDir;
  if OldInstallDir <> '' then
  begin
    InstalledVer := '0.1';
    InstallMode  := MODE_UPGRADE;
    Exit;
  end;

  InstallMode := MODE_FRESH;
end;

// ──────────────────────────────────────────────────────────────────────────────
// 向导初始化：升级时强制新版本目录；根据模式显示维护页
// ──────────────────────────────────────────────────────────────────────────────
procedure InitializeWizard;
var
  LabelDesc: TNewStaticText;
begin
  WizardForm.FormStyle := fsStayOnTop;

  // 升级时指向新版本目录：复用旧版安装的父目录（保持用户自定义位置）
  if InstallMode = MODE_UPGRADE then
  begin
    if OldInstallDir <> '' then
      WizardForm.DirEdit.Text := ExtractFileDir(OldInstallDir) + '\ConvertTool-{#MyAppVersion}'
    else
      WizardForm.DirEdit.Text := ExpandConstant('{autopf}\ConvertTool-{#MyAppVersion}');
  end;

  if InstallMode = MODE_FRESH then Exit;

  // 创建维护选项页
  case InstallMode of
    MODE_UPGRADE:
      MaintenancePage := CreateCustomPage(wpWelcome, '升级确认',
        '检测到旧版本 ' + InstalledVer + ' 已安装');
    MODE_REPAIR:
      MaintenancePage := CreateCustomPage(wpWelcome, '维护选项',
        '版本 {#MyAppVersion} 已安装在您的系统中');
    MODE_DOWNGRADE:
      MaintenancePage := CreateCustomPage(wpWelcome, '版本警告',
        '检测到已安装更高版本 ' + InstalledVer);
  end;

  LabelDesc := TNewStaticText.Create(MaintenancePage);
  LabelDesc.Parent := MaintenancePage.Surface;
  LabelDesc.Left := 0;
  LabelDesc.Top := 0;
  LabelDesc.Width := MaintenancePage.SurfaceWidth;
  LabelDesc.WordWrap := True;
  LabelDesc.AutoSize := True;

  case InstallMode of
    MODE_UPGRADE:
    begin
      LabelDesc.Caption :=
        '点击"下一步"将自动卸载旧版本并安装 {#MyAppVersion}。' + #13#10 +
        '您的 Input / Output 文件夹及转换历史将自动迁移到新目录。' + #13#10 +
        '迁移完成后旧版目录将自动删除。';
    end;
    MODE_REPAIR:
    begin
      LabelDesc.Caption := '请选择要执行的操作：';

      RadioRepair := TNewRadioButton.Create(MaintenancePage);
      RadioRepair.Parent := MaintenancePage.Surface;
      RadioRepair.Left := ScaleX(8);
      RadioRepair.Top := LabelDesc.Top + ScaleY(28);
      RadioRepair.Width := MaintenancePage.SurfaceWidth - ScaleX(8);
      RadioRepair.Height := ScaleY(17);
      RadioRepair.Caption := '修复安装（重新安装所有程序文件）';
      RadioRepair.Checked := True;

      RadioRemove := TNewRadioButton.Create(MaintenancePage);
      RadioRemove.Parent := MaintenancePage.Surface;
      RadioRemove.Left := ScaleX(8);
      RadioRemove.Top := RadioRepair.Top + RadioRepair.Height + ScaleY(10);
      RadioRemove.Width := MaintenancePage.SurfaceWidth - ScaleX(8);
      RadioRemove.Height := ScaleY(17);
      RadioRemove.Caption := '卸载 {#MyAppName}';
      RadioRemove.Checked := False;
    end;
    MODE_DOWNGRADE:
    begin
      LabelDesc.Caption :=
        '警告：降级安装 {#MyAppVersion} 可能导致功能异常。' + #13#10#13#10 +
        '建议先完整卸载当前版本再安装目标版本。' + #13#10 +
        '如需继续降级，请点击"下一步"；否则请点击"取消"。';
    end;
  end;
end;

// ──────────────────────────────────────────────────────────────────────────────
// 升级/降级：复制文件前静默卸载旧版
// 若旧版为 0.1.x（无注册表记录），跳过卸载步骤，直接进行数据迁移
// ──────────────────────────────────────────────────────────────────────────────
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UninstallerPath: String;
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  if (InstallMode = MODE_UPGRADE) or (InstallMode = MODE_DOWNGRADE) then
  begin
    UninstallerPath := GetUninstallerPath;
    if (UninstallerPath <> '') and FileExists(UninstallerPath) then
    begin
      if not Exec(UninstallerPath, '/VERYSILENT /NORESTART', '',
                  SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        Result := '旧版本自动卸载失败（错误码 ' + IntToStr(ResultCode) + '），请手动卸载后重试。';
    end;
    // 若为 0.1.x 目录检测（无正式卸载程序），跳过卸载，后续仅做数据迁移
  end;
end;

// 始终跳过附加任务页；非全新安装时跳过目录选择页
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = wpSelectTasks then
  begin
    Result := True;
    Exit;
  end;
  if InstallMode <> MODE_FRESH then
    if (PageID = wpSelectDir) or (PageID = wpSelectProgramGroup) then
      Result := True;
end;

// 维护页"下一步"：选择卸载时启动卸载程序并关闭向导
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Assigned(MaintenancePage) and (CurPageID = MaintenancePage.ID) then
  begin
    if Assigned(RadioRemove) and RadioRemove.Checked then
    begin
      ShellExec('', GetUninstallerPath, '/SILENT', '', SW_SHOW, ewNoWait, ResultCode);
      WizardForm.Close;
      Result := False;
    end;
  end;
end;

// 在完成页添加"创建桌面快捷方式"复选框
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    DesktopIconCheckbox := TNewCheckBox.Create(WizardForm);
    DesktopIconCheckbox.Parent := WizardForm.FinishedPage;
    DesktopIconCheckbox.SetBounds(
      WizardForm.RunList.Left,
      WizardForm.RunList.Top + WizardForm.RunList.Height + ScaleY(4) - ScaleY(17),
      WizardForm.RunList.Width,
      ScaleY(17));
    DesktopIconCheckbox.Caption := '创建桌面快捷方式（{#MyAppName}）';
    DesktopIconCheckbox.Checked := True;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssDone then
  begin
    InstallCompleted := True;
    // 安装完成后迁移旧版用户数据（Input / Output / conversion_history.json）
    if (InstallMode = MODE_UPGRADE) and (OldInstallDir <> '') then
    begin
      MigrateUserData(OldInstallDir, ExpandConstant('{app}'));
      // 迁移完成后删除旧版目录（只删与新目录不同的目录）
      if (OldInstallDir <> ExpandConstant('{app}')) and DirExists(OldInstallDir) then
        Exec(ExpandConstant('{sys}\cmd.exe'),
             '/c rmdir /s /q "' + OldInstallDir + '"',
             '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

// ──────────────────────────────────────────────────────────────────────────────
// 安装完成后：
//   1. 根据复选框创建或跳过桌面快捷方式
//   2. 按系统语言自动打开对应说明文档（非静默模式）
// ──────────────────────────────────────────────────────────────────────────────
procedure DeinitializeSetup;
var
  ShortcutPath, ReadmeFile: String;
  ResultCode: Integer;
begin
  if not InstallCompleted then Exit;

  // 桌面快捷方式
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

  // 按系统语言自动打开说明文档（非静默模式）
  if not WizardSilent then
  begin
    if ExpandConstant('{language}') = 'chinesesimplified' then
      ReadmeFile := ExpandConstant('{app}\读我.txt')
    else
      ReadmeFile := ExpandConstant('{app}\README.txt');
    if FileExists(ReadmeFile) then
      ShellExec('open', ReadmeFile, '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
  end;
end;

// 卸载进度窗口同样置顶
procedure InitializeUninstallProgressForm;
begin
  UninstallProgressForm.FormStyle := fsStayOnTop;
end;
