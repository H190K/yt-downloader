; Inno Setup 6 script for H190K Downloader.
;
; Normally compiled by scripts\build.ps1, which passes /DAppVersion and /DSourceDir.
; Manual compile (from repo root, after PyInstaller):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Odist packaging\installer.iss
;
; Installs to C:\H190K Downloader by default. Standard users can create folders in
; the root of C:\ and get Modify rights on them, so the app's data\bin tools folder
; (yt-dlp, ffmpeg, deno - downloaded on first run) stays writable beside the exe.

#define AppName "H190K Downloader"
#define AppExeName "H190K Downloader.exe"
#define AppPublisher "H190K"
#define AppURL "https://h190k.com"
#define GitHubURL "https://github.com/H190K"
#define RepoURL "https://github.com/H190K/yt-downloader"
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\dist\H190K Downloader"
#endif

[Setup]
AppId={{9E6D2AA7-4F43-41B7-9BA6-648A741A4DAE}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#RepoURL}/issues
AppUpdatesURL={#RepoURL}/releases
AppCopyright=Copyright (c) 2025-2026 H190K. MIT License.
AppComments=Source code: {#RepoURL}
LicenseFile={#SourcePath}..\LICENSE
DefaultDirName={sd}\{#AppName}
DisableDirPage=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#SourcePath}..\dist
OutputBaseFilename=H190K-Downloader-Setup-{#AppVersion}
SetupIconFile={#SourcePath}..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardImageFile={#SourcePath}assets\wizard-large.bmp
WizardSmallImageFile={#SourcePath}assets\wizard-small.bmp
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=Copyright (c) 2025-2026 H190K

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nDownload video (MP4 with audio) and audio (MP3 / M4A) from YouTube, Instagram, TikTok, X, SoundCloud and 1000+ other sites.%n%nOn first run the app downloads its tools (yt-dlp, FFmpeg, Deno) into its own folder and keeps them up to date.%n%nCreated by H190K - free and open source (MIT).
FinishedLabel=Setup has finished installing [name] on your computer.%n%nThanks for using it! Found a bug or have an idea? Open an issue on GitHub.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; Tools + config live here; make sure it is writable even if installed elevated.
Name: "{app}\data"; Permissions: users-modify

[Files]
; Excludes: never ship tools/config created by test-running the exe from dist\.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\data,\data\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[INI]
; Internet shortcuts (.url) for the Start-menu links below.
Filename: "{app}\links\H190K website.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#AppURL}"
Filename: "{app}\links\H190K on GitHub.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#GitHubURL}"
Filename: "{app}\links\Source code.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#RepoURL}"
Filename: "{app}\links\Report an issue.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#RepoURL}/issues"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Update H190K Downloader tools"; Filename: "{app}\{#AppExeName}"; Parameters: "--update"; WorkingDir: "{app}"; Comment: "Download / update yt-dlp, ffmpeg and deno"
Name: "{group}\H190K website"; Filename: "{app}\links\H190K website.url"
Name: "{group}\H190K on GitHub"; Filename: "{app}\links\H190K on GitHub.url"
Name: "{group}\Source code (GitHub)"; Filename: "{app}\links\Source code.url"
Name: "{group}\License"; Filename: "{app}\LICENSE.txt"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Runs first and waits, so the GUI below starts with the tools already present.
Filename: "{app}\{#AppExeName}"; Parameters: "--setup"; WorkingDir: "{app}"; Description: "Download required tools now (yt-dlp, ffmpeg, deno)"; StatusMsg: "Downloading required tools..."; Flags: postinstall skipifsilent waituntilterminated
Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: postinstall skipifsilent nowait

[UninstallDelete]
; Downloaded tools (data\bin), versions.json and config.json.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\links"
Type: dirifempty; Name: "{app}"

[Code]
{ Clickable credit links at the bottom-left of every wizard page. }
procedure OpenURL(const URL: String);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', URL, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure WebsiteClick(Sender: TObject); begin OpenURL('{#AppURL}'); end;
procedure GitHubClick(Sender: TObject); begin OpenURL('{#GitHubURL}'); end;
procedure RepoClick(Sender: TObject); begin OpenURL('{#RepoURL}'); end;

function AddLink(const Caption: String; Left: Integer; OnClick: TNotifyEvent): Integer;
var
  L: TNewStaticText;
begin
  L := TNewStaticText.Create(WizardForm);
  L.Parent := WizardForm;
  L.Caption := Caption;
  L.Cursor := crHand;
  L.Font.Color := clHotLight;
  L.Font.Style := [fsUnderline];
  L.OnClick := OnClick;
  L.Left := Left;
  L.Top := WizardForm.CancelButton.Top + (WizardForm.CancelButton.Height - L.Height) div 2;
  L.Anchors := [akLeft, akBottom];
  Result := L.Left + L.Width + ScaleX(12);
end;

procedure InitializeWizard();
var
  X: Integer;
begin
  X := AddLink('h190k.com', ScaleX(16), @WebsiteClick);
  X := AddLink('GitHub', X, @GitHubClick);
  AddLink('Source code', X, @RepoClick);
end;
