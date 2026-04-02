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
