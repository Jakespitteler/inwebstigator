; The Inno Setup script for the Windows installer testers are sent.
; Build it with scripts\build_installer.ps1, which packages the app with PyInstaller first (see "Building the Windows
; app" in README.md). Paths are relative to this file, so it builds from any copy of the repository.
; Non-commercial use only

#define MyAppName "Inwebstigator"
#define MyAppVersion "0.3"
#define MyAppPublisher "AdJud1cator"
#define MyAppURL "https://webloom-two.vercel.app/test-site"
#define MyAppExeName "inwebstigator.exe"
; The app's "already running" check (see ensure_single_instance in inwebstigator.py)
#define MyAppMutex "Global\Inwebstigator_SingleInstance"

[Setup]
; NOTE: The value of AppId uniquely identifies this application. Do not use the same AppId value in installers for other applications.
; Keep it the same between versions, so a new installer updates the app rather than installing a second copy.
AppId={{2F9CE132-F399-4A60-BAA0-B495E47C2BA4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Installed for the person running the installer only (in AppData\Local\Programs), so it needs no administrator
; access, and starts at their own sign-in, launches as them and keeps its data in their own AppData
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; "ArchitecturesAllowed=x64compatible" specifies that Setup cannot run
; on anything but x64 and Windows 11 on Arm.
ArchitecturesAllowed=x64compatible
; "ArchitecturesInstallIn64BitMode=x64compatible" requests that the
; install be done in "64-bit mode" on x64 or Windows 11 on Arm,
; meaning it should use the native 64-bit Program Files directory and
; the 64-bit view of the registry.
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
; The app keeps running in the system tray, so installing or uninstalling asks for it to be quit first
AppMutex={#MyAppMutex}
OutputDir=dist
OutputBaseFilename=Inwebstigator Installer
SetupIconFile=app\frontend\static\favicon.ico
SolidCompression=yes
WizardStyle=modern dynamic

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Ticked by default, so scans carry on after the computer restarts
Name: "startatsignin"; Description: "Start {#MyAppName} when I sign in to Windows"; GroupDescription: "Starting {#MyAppName}:"

[Files]
; The app, as packaged by PyInstaller (uv run pyinstaller inwebstigator.spec)
Source: "dist\inwebstigator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The email settings, which the app reads from next to inwebstigator.exe. It holds the email account's password,
; so only send the installer to people who should have it.
Source: ".env"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Starts the app when the person signs in to Windows, so scans carry on after a restart
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startatsignin
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; Updating the app with the box unticked removes the shortcut an earlier install added
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"; Tasks: not startatsignin

[UninstallDelete]
; The saved websites, scan history and logs (the app's user_data_dir in app/core/config.py)
Type: filesandordirs; Name: "{localappdata}\Inwebstigator"
