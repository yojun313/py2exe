; py2exe 용 Inno Setup 템플릿.
; 이 파일을 빌드 대상 프로젝트 루트로 복사한 뒤 MyAppPublisher 등 고정 정보만 수정하면 된다.
;
; #ifndef 로 감싼 값은 빌드 시 py2exe 가 /D 옵션으로 자동 주입한다:
;   MyAppName, MyAppVersion, MyAppExeName, BuildDir, OutputDir, SourceIconPath, ProjectBaseDir
; 값을 고정하고 싶으면 #ifndef 블록을 지우고 #define 만 남기면 된다.

#ifndef MyAppName
  #define MyAppName "YourAppName"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppExeName
  #define MyAppExeName MyAppName + ".exe"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher MyAppName
#endif

#ifndef ProjectBaseDir
  #define ProjectBaseDir "."
#endif
#ifndef BuildDir
  #define BuildDir ProjectBaseDir + "\exe\" + MyAppName + "_" + MyAppVersion
#endif
#ifndef OutputDir
  #define OutputDir ProjectBaseDir + "\output"
#endif
#ifndef SourceIconPath
  #define SourceIconPath ""
#endif

#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".myp"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
DisableProgramGroupPage=yes
OutputBaseFilename={#MyAppName}_{#MyAppVersion}
OutputDir={#OutputDir}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
DisableStartupPrompt=true
DisableWelcomePage=true
DisableDirPage=true
DisableReadyPage=true
DisableFinishedPage=true
#if SourceIconPath != "" && FileExists(SourceIconPath)
SetupIconFile={#SourceIconPath}
#endif

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Registry]
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: "{#MyAppAssocExt}"; ValueData: ""

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
