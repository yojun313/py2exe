#define MyAppName "YourAppName"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "YourAppName"
#define MyAppExeName "YourAppName.exe"

#ifndef ProjectBaseDir
  #define ProjectBaseDir "YourProjectBaseDir"
#endif

#ifndef SourceIconPath
  #define SourceIconPath "YourSourceIconPath"
#endif

#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".myp"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt
#define BuildDir ProjectBaseDir + "\exe\" + MyAppName + "_" + MyAppVersion
#define OutputDir ProjectBaseDir + "\Output"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
PrivilegesRequired=lowest                     
AppId={{E9F30387-A1A2-479B-80C3-9ABABED9C40D}
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
SetupIconFile={#SourceIconPath}

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
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: ".myp"; ValueData: ""

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent