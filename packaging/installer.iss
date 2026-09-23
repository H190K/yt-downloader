; Inno Setup 6 script for H190K Downloader.
;
; Normally compiled by scripts\build.ps1, which passes /DAppVersion and /DSourceDir.
; Manual compile (from repo root, after PyInstaller):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Odist packaging\installer.iss
;
; Installs to C:\H190K Downloader by default. Standard users can create folders in
; the root of C:\ and get Modify rights on them, so the app's data\bin tools folder
; (yt-dlp, ffmpeg, deno) stays writable beside the exe.
;
; Required tools (task "downloadtools", checked by default):
;   After the files are copied, a native "Downloading required tools" wizard page downloads
;   yt-dlp.exe, the FFmpeg zip and the Deno zip (+ their checksum files) into {tmp}, then runs
;   "H190K Downloader.exe --import-tools {tmp}" hidden (no console; log: data\import-tools.log),
;   which verifies, unzips and installs them into data\bin. Tools already present are skipped.
;   If anything fails, the user gets Retry / Skip; Skip never fails the installation - the app's
;   own first-run setup screen downloads the tools instead.
;
; Command line:
;   /SILENT                      downloads the tools (task is on by default; progress is shown;
;                                a failure asks Retry/Skip unless /SUPPRESSMSGBOXES -> Skip).
;   /VERYSILENT                  does NOT download (fast, offline-safe) unless /DOWNLOADTOOLS is
;                                given or the task is requested with /TASKS= or /MERGETASKS=.
;   /VERYSILENT /DOWNLOADTOOLS   downloads the tools; a failure is logged and skipped silently.
;   /MERGETASKS="!downloadtools" never downloads.
;
; Testing: the download locations can be overridden at compile time, e.g.
;   ISCC /DFfmpegBase=https://offline.invalid/x packaging\installer.iss   (simulates a failure)

; AppName / AppIdValue are overridable only so test builds (e.g. /DAppName="H190K Downloader Test"
; /DAppIdValue="{{...}") can be installed side by side without touching a real installation.
#ifndef AppName
  #define AppName "H190K Downloader"
#endif
#ifndef AppIdValue
  #define AppIdValue "{{9E6D2AA7-4F43-41B7-9BA6-648A741A4DAE}"
#endif
#define AppExeName "H190K Downloader.exe"
#define AppPublisher "H190K"
#define AppURL "https://h190k.com"
#define GitHubURL "https://github.com/H190K"
#define RepoURL "https://github.com/H190K/yt-downloader"
#ifndef AppVersion
  #define AppVersion "2.0.1"
#endif
; Same sources as core/deps.py (_REPOS / _ASSETS / _CHECKSUM_ASSETS).
#ifndef YtdlpBase
  #define YtdlpBase "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
#endif
#ifndef FfmpegBase
  #define FfmpegBase "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download"
#endif
#ifndef DenoBase
  #define DenoBase "https://github.com/denoland/deno/releases/latest/download"
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\dist\H190K Downloader"
#endif

[Setup]
AppId={#AppIdValue}
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
WelcomeLabel2=This will install [name/ver] on your computer.%n%nDownload video (MP4 with audio) and audio (MP3 / M4A) from YouTube, Instagram, TikTok, X, SoundCloud and 1000+ other sites.%n%nSetup also downloads the tools the app needs (yt-dlp, FFmpeg, Deno) into the app's own folder; the app keeps them up to date.%n%nCreated by H190K - free and open source (MIT).
FinishedLabel=Setup has finished installing [name] on your computer.%n%nThanks for using it! Found a bug or have an idea? Open an issue on GitHub.

[CustomMessages]
ToolsGroup=Required tools:
ToolsTask=Download required tools now (yt-dlp, FFmpeg, Deno — about 250 MB)
ToolsPageCaption=Downloading required tools
ToolsPageDescription=Setup is downloading yt-dlp, FFmpeg and Deno. This may take a few minutes.
ToolsDownloading=Downloading %1 (%2 of %3)...
ToolsChecksums=Downloading checksums...
ToolsInstallCaption=Installing required tools
ToolsInstallDescription=Setup is installing yt-dlp, FFmpeg and Deno into the app's folder.
ToolsInstalling=Installing tools...
ToolsInstallingDetail=Verifying and extracting yt-dlp, FFmpeg and Deno. This takes a few seconds.
ToolsFailedTitle=Couldn't download the tools now.
ToolsFailedText=H190K Downloader will download them automatically the first time you open it.
ToolsRetry=&Retry
ToolsSkip=&Skip
ToolsFinishedOk=yt-dlp, FFmpeg and Deno are installed and ready to use.
ToolsFinishedSkipped=The required tools were not downloaded. H190K Downloader will download them automatically the first time you open it.

[Tasks]
Name: "downloadtools"; Description: "{cm:ToolsTask}"; GroupDescription: "{cm:ToolsGroup}"
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
; The required tools are downloaded by the wizard itself (task "downloadtools", see [Code]).
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

{ ---------------------------------------------------------------------------------------------
  Required tools: downloaded on a native wizard page after the files are copied, then installed
  by "H190K Downloader.exe --import-tools <tmp dir>" (hidden, no console). Never fails the setup.
  --------------------------------------------------------------------------------------------- }
const
  ToolsNotRun = 0;
  ToolsInstalled = 1;
  ToolsSkipped = 2;

var
  DownloadPage: TDownloadWizardPage;
  ToolsResult: Integer;
  DownloadIndex, DownloadCount: Integer;
  LastDownloadName: String;
  ChecksumPass: Boolean;

function CmdLineHas(const Switch: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Switch) = 0 then begin
      Result := True;
      Exit;
    end;
end;

{ True when /TASKS= or /MERGETASKS= explicitly asks for the downloadtools task. }
function ToolsTaskRequested(): Boolean;
var
  I: Integer;
  P: String;
begin
  Result := False;
  for I := 1 to ParamCount do begin
    P := Lowercase(ParamStr(I));
    if ((Pos('/tasks=', P) = 1) or (Pos('/mergetasks=', P) = 1)) and
       (Pos('downloadtools', P) > 0) and (Pos('!downloadtools', P) = 0) then
      Result := True;
  end;
end;

function ShouldDownloadTools(): Boolean;
begin
  if not WizardIsTaskSelected('downloadtools') then
    Result := False
  else if CmdLineHas('/VERYSILENT') then
    { Keep very silent installs fast and offline-safe unless asked for explicitly. }
    Result := CmdLineHas('/DOWNLOADTOOLS') or ToolsTaskRequested()
  else
    Result := True;
end;

function ToolsDir(): String;
begin
  Result := ExpandConstant('{app}\data\bin\');
end;

function NeedYtdlp(): Boolean;
begin
  Result := not FileExists(ToolsDir() + 'yt-dlp.exe');
end;

function NeedFfmpeg(): Boolean;
begin
  Result := not (FileExists(ToolsDir() + 'ffmpeg.exe') and FileExists(ToolsDir() + 'ffprobe.exe'));
end;

function NeedDeno(): Boolean;
begin
  Result := not FileExists(ToolsDir() + 'deno.exe');
end;

function FriendlyToolName(const FileName: String): String;
begin
  if Pos('ffmpeg', Lowercase(FileName)) > 0 then
    Result := 'FFmpeg'
  else if Pos('deno', Lowercase(FileName)) > 0 then
    Result := 'Deno'
  else
    Result := 'yt-dlp';
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if FileName <> LastDownloadName then begin
    LastDownloadName := FileName;
    if ChecksumPass then
      DownloadPage.Msg1Label.Caption := CustomMessage('ToolsChecksums')
    else begin
      DownloadIndex := DownloadIndex + 1;
      { (a [Code] line must not start with '[': Inno would read it as a section tag) }
      DownloadPage.Msg1Label.Caption := FmtMessage(CustomMessage('ToolsDownloading'), [
        FriendlyToolName(FileName), IntToStr(DownloadIndex), IntToStr(DownloadCount)]);
    end;
  end;
  if (ProgressMax > 0) and (Progress = ProgressMax) then
    Log(Format('Downloaded %s', [FileName]));
  Result := True;
end;

procedure DeleteDownloads();
begin
  DeleteFile(ExpandConstant('{tmp}\yt-dlp.exe'));
  DeleteFile(ExpandConstant('{tmp}\ffmpeg-master-latest-win64-gpl.zip'));
  DeleteFile(ExpandConstant('{tmp}\deno-x86_64-pc-windows-msvc.zip'));
end;

{ One attempt: download the missing tools, then import them. Returns True on success. }
function DownloadAndInstallTools(var ErrorMsg: String; var Aborted: Boolean): Boolean;
var
  GetYtdlp, GetFfmpeg, GetDeno, Downloaded: Boolean;
  ResultCode: Integer;
begin
  Result := False;
  Aborted := False;
  ErrorMsg := '';
  GetYtdlp := NeedYtdlp();
  GetFfmpeg := NeedFfmpeg();
  GetDeno := NeedDeno();

  DownloadPage.Clear;
  DownloadCount := 0;
  if GetYtdlp then begin
    DownloadPage.Add('{#YtdlpBase}/yt-dlp.exe', 'yt-dlp.exe', '');
    DownloadCount := DownloadCount + 1;
  end;
  if GetFfmpeg then begin
    DownloadPage.Add('{#FfmpegBase}/ffmpeg-master-latest-win64-gpl.zip', 'ffmpeg-master-latest-win64-gpl.zip', '');
    DownloadCount := DownloadCount + 1;
  end;
  if GetDeno then begin
    DownloadPage.Add('{#DenoBase}/deno-x86_64-pc-windows-msvc.zip', 'deno-x86_64-pc-windows-msvc.zip', '');
    DownloadCount := DownloadCount + 1;
  end;
  if DownloadCount = 0 then begin
    Log('All required tools are already installed; nothing to download.');
    Result := True;
    Exit;
  end;

  DownloadIndex := 0;
  LastDownloadName := '';
  ChecksumPass := False;
  DownloadPage.ProgressBar.Style := npbstNormal;
  DownloadPage.Show;
  try
    Downloaded := False;
    try
      DownloadPage.Download;  // into the {tmp} folder
      Downloaded := True;
    except
      Aborted := DownloadPage.AbortedByUser;
      ErrorMsg := GetExceptionMessage;
    end;

    if Downloaded then begin
      { Published checksums, best effort: --import-tools fetches them itself if these fail. }
      DownloadPage.Clear;
      ChecksumPass := True;
      LastDownloadName := '';
      if GetYtdlp then DownloadPage.Add('{#YtdlpBase}/SHA2-256SUMS', 'SHA2-256SUMS', '');
      if GetFfmpeg then DownloadPage.Add('{#FfmpegBase}/checksums.sha256', 'checksums.sha256', '');
      if GetDeno then DownloadPage.Add('{#DenoBase}/deno-x86_64-pc-windows-msvc.zip.sha256sum', 'deno-x86_64-pc-windows-msvc.zip.sha256sum', '');
      try
        DownloadPage.Download;
      except
        Log('Checksum download failed (the import step will fetch them itself): ' + GetExceptionMessage);
      end;
      ChecksumPass := False;

      { Inno Setup cannot unzip: the app verifies, extracts and installs the tools. The exe is
        windowed and handles --import-tools before any console code, so nothing flashes up. }
      DownloadPage.Caption := CustomMessage('ToolsInstallCaption');
      DownloadPage.Description := CustomMessage('ToolsInstallDescription');
      DownloadPage.SetText(CustomMessage('ToolsInstalling'), CustomMessage('ToolsInstallingDetail'));
      DownloadPage.AbortButton.Hide;  { nothing to stop here; it only takes a few seconds }
      DownloadPage.ProgressBar.Style := npbstMarquee;
      if not Exec(ExpandConstant('{app}\{#AppExeName}'), '--import-tools "' + ExpandConstant('{tmp}') + '"',
                  ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        ErrorMsg := 'Could not start {#AppExeName}: ' + SysErrorMessage(ResultCode) + '.'
      else if ResultCode <> 0 then
        ErrorMsg := Format('Installing the tools failed (code %d). Details: %s', [ResultCode, ExpandConstant('{app}\data\import-tools.log')])
      else
        Result := True;
      Log(Format('--import-tools exit code: %d', [ResultCode]));
      DeleteDownloads();
    end;
  finally
    DownloadPage.ProgressBar.Style := npbstNormal;
    DownloadPage.Hide;
    DownloadPage.Caption := CustomMessage('ToolsPageCaption');
    DownloadPage.Description := CustomMessage('ToolsPageDescription');
  end;
end;

procedure InstallTools();
var
  ErrorMsg: String;
  Aborted: Boolean;
  Choice: Integer;
begin
  repeat
    if DownloadAndInstallTools(ErrorMsg, Aborted) then begin
      ToolsResult := ToolsInstalled;
      Exit;
    end;
    ToolsResult := ToolsSkipped;
    Log('Required tools step failed: ' + ErrorMsg);
    if Aborted then begin
      Log('Download aborted by the user; skipping. The app will download the tools on first run.');
      Exit;
    end;
    if CmdLineHas('/VERYSILENT') then
      Exit;
    Choice := SuppressibleTaskDialogMsgBox(CustomMessage('ToolsFailedTitle'),
      CustomMessage('ToolsFailedText') + #13#10#13#10 + 'Details: ' + AddPeriod(ErrorMsg),
      mbError, MB_YESNO, [CustomMessage('ToolsRetry'), CustomMessage('ToolsSkip')], 0, IDNO);
    { Yes = Retry, No = Skip. (Inno only allows custom labels on the non-Cancel buttons.) }
  until Choice <> IDYES;
  Log('Required tools skipped; the app will download them on first run.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and ShouldDownloadTools() then begin
    try
      InstallTools();
    except
      { Never let the optional tools step fail or roll back the installation. }
      Log('Unexpected error in the required tools step: ' + GetExceptionMessage);
      ToolsResult := ToolsSkipped;
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Note: String;
begin
  if (CurPageID = wpFinished) and (ToolsResult <> ToolsNotRun) then begin
    if ToolsResult = ToolsInstalled then
      Note := CustomMessage('ToolsFinishedOk')
    else
      Note := CustomMessage('ToolsFinishedSkipped');
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + Note + #13#10;
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
    WizardForm.RunList.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height;
  end;
end;

procedure InitializeWizard();
var
  X: Integer;
begin
  X := AddLink('h190k.com', ScaleX(16), @WebsiteClick);
  X := AddLink('GitHub', X, @GitHubClick);
  AddLink('Source code', X, @RepoClick);

  ToolsResult := ToolsNotRun;
  DownloadPage := CreateDownloadPage(CustomMessage('ToolsPageCaption'), CustomMessage('ToolsPageDescription'), @OnDownloadProgress);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;
