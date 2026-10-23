; 4K YT Downloader for Windows: the installer, built by build.py with Inno Setup 6.
;
; Installs for the current user only (no administrator password) into
; %LOCALAPPDATA%\Programs\4K YT Downloader, with a Start menu entry and a desktop shortcut.
; Running a newer installer updates the app in place (it closes the app first).
; The tools the app downloads on first launch (yt-dlp, ffmpeg, deno) live in
; %LOCALAPPDATA%\4K YT Downloader\tools and are removed by the uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif
#define AppName "4K YT Downloader"
#define AppExe "4K YT Downloader.exe"

[Setup]
; Never change AppId: Windows uses it to recognise updates of the same app.
AppId={{8C5E2D47-3F1B-4C7A-9E64-2B7D41A9C3F5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=the.real.sidd
AppPublisherURL=https://github.com/the-real-sidd/4k-yt-downloader
AppSupportURL=https://github.com/the-real-sidd/4k-yt-downloader
AppUpdatesURL=https://github.com/the-real-sidd/4k-yt-downloader/releases/latest
VersionInfoVersion={#FileVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=4K-YT-Downloader-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no
SetupMutex=4KYTDownloaderSetup

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; The previous version's program files (settings and downloaded tools live elsewhere and stay).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Close the app (and its downloads) before removing it.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM ""{#AppExe}"" /T /F"; Flags: runhidden; RunOnceId: "CloseApp"

[UninstallDelete]
; The downloaded tools, the log and the window's data; and the saved settings.
Type: filesandordirs; Name: "{localappdata}\{#AppName}"
Type: filesandordirs; Name: "{userappdata}\{#AppName}"
