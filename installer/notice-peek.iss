#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef ProgramDirectory
  #error ProgramDirectory is required
#endif
#ifndef OutputDirectory
  #error OutputDirectory is required
#endif

[Setup]
#ifdef TestRoot
AppId=notice-peek-installer-smoke-test
DefaultDirName={#TestRoot}\program
Uninstallable=yes
CreateUninstallRegKey=no
OutputBaseFilename=notice-peek-setup-test
UsePreviousAppDir=no
#else
AppId={{045DB250-85DB-480B-96ED-725741027D54}
DefaultDirName={localappdata}\Programs\notice-peek
OutputBaseFilename=notice-peek-setup
#endif
AppName=notice-peek
AppVersion={#AppVersion}
AppPublisher=hy2oni
AppPublisherURL=https://github.com/hy2oni/notice-peek
AppSupportURL=https://github.com/hy2oni/notice-peek/issues
AppUpdatesURL=https://github.com/hy2oni/notice-peek/releases/latest
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern light
DisableWelcomePage=yes
DisableProgramGroupPage=yes
DefaultGroupName=notice-peek
OutputDir={#OutputDirectory}
Compression=lzma2
SolidCompression=yes
SetupIconFile=..\notice_peek\assets\notice-peek.ico
UninstallDisplayIcon={app}\notice-peek.exe
CloseApplications=yes
RestartApplications=no
AllowNetworkDrive=no
AllowUNCPath=no
SetupLogging=yes

[Languages]
Name: korean; MessagesFile: compiler:Languages\Korean.isl

[Files]
Source: "{#ProgramDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PythonLicenseFile}"; DestDir: "{app}\licenses"; DestName: "Python-LICENSE.txt"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
#ifdef TestRoot
Name: "{#TestRoot}\shortcuts\desktop\notice-peek"; Filename: "{app}\notice-peek.exe"; WorkingDir: "{app}"; IconFilename: "{app}\notice-peek.exe"
Name: "{#TestRoot}\shortcuts\start-menu\notice-peek"; Filename: "{app}\notice-peek.exe"; WorkingDir: "{app}"; IconFilename: "{app}\notice-peek.exe"
#else
Name: "{userdesktop}\notice-peek"; Filename: "{app}\notice-peek.exe"; WorkingDir: "{app}"; IconFilename: "{app}\notice-peek.exe"
Name: "{userprograms}\notice-peek"; Filename: "{app}\notice-peek.exe"; WorkingDir: "{app}"; IconFilename: "{app}\notice-peek.exe"
#endif

[Run]
Filename: "{app}\notice-peek.exe"; Description: "notice-peek 실행"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=notice-peek 설치가 완료되었습니다.%n%n바탕화면의 notice-peek 아이콘을 더블클릭하면 실행됩니다.%n처음 실행할 때 받을 공지 게시판을 선택해 주세요.
