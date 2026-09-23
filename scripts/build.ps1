<#
.SYNOPSIS
    Builds H190K Downloader: PyInstaller onedir app + (optional) Inno Setup installer.

.DESCRIPTION
    1. Creates / reuses the isolated build venv  .venv-build
    2. Installs requirements.txt + requirements-dev.txt (PyInstaller)
    3. Runs PyInstaller with packaging\H190K-Downloader.spec  ->  dist\H190K Downloader\
    4. If Inno Setup 6 (ISCC.exe) is found, compiles packaging\installer.iss  ->  dist\H190K-Downloader-Setup-<version>.exe

    The built app does not need Python on the target PC. yt-dlp, ffmpeg and deno are
    NOT bundled; the app downloads them on first run into its data\bin folder.

.PARAMETER Version
    Version string for the installer. Defaults to core.__version__ if defined, else 2.0.0.

.PARAMETER NoInstaller
    Skip the Inno Setup step even if ISCC.exe is available.

.PARAMETER Recreate
    Delete and recreate .venv-build before building.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Version 2.1.0 -NoInstaller
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$NoInstaller,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot  # repo root (this script lives in scripts\)
Set-Location $Root

$AppName  = 'H190K Downloader'
$VenvDir  = Join-Path $Root '.venv-build'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'
$DistDir  = Join-Path $Root 'dist'
$WorkDir  = Join-Path $Root 'build'
$SpecFile = Join-Path $Root 'packaging\H190K-Downloader.spec'
$IssFile  = Join-Path $Root 'packaging\installer.iss'

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Invoke-Native {
    # Runs a native command and throws on a non-zero exit code.
    # -Attempts > 1 retries: antivirus real-time scanning (e.g. Defender) sometimes locks a
    # freshly written exe while PyInstaller / Inno Setup embed the icon and version resources
    # ("EndUpdateResource failed (110)", "set_exe_build_timestamp ... no more attempts").
    param([string]$Exe, [string[]]$Arguments, [int]$Attempts = 1)
    # Native tools write progress/warnings to stderr; don't let PowerShell treat that as fatal.
    $ErrorActionPreference = 'Continue'
    for ($i = 1; $i -le $Attempts; $i++) {
        & $Exe @Arguments
        if ($LASTEXITCODE -eq 0) { return }
        if ($i -lt $Attempts) {
            Write-Warning "Exit code $LASTEXITCODE (often a transient antivirus file lock) - retrying in $(5 * $i) s ($i/$Attempts)..."
            Start-Sleep -Seconds (5 * $i)
        }
    }
    throw "Command failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')"
}

function Find-BasePython {
    # Prefer the Windows launcher with a 3.12 / 3.11 / any-3 interpreter, then python on PATH.
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in @('-3.12', '-3.11', '-3')) {
            & $py.Source $v -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @($py.Source, $v) }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*WindowsApps*') { return @($python.Source) }
    throw 'Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and re-run.'
}

function Find-ISCC {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    foreach ($key in @(
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1')) {
        $loc = (Get-ItemProperty $key -ErrorAction SilentlyContinue).InstallLocation
        if ($loc -and (Test-Path (Join-Path $loc 'ISCC.exe'))) { return (Join-Path $loc 'ISCC.exe') }
    }
    return $null
}

function Get-AppVersion {
    if ($Version) { return $Version }
    $init = Join-Path $Root 'core\__init__.py'
    if (Test-Path $init) {
        $m = Select-String -Path $init -Pattern '^__version__\s*=\s*[''"]([^''"]+)[''"]' | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    return '2.0.0'
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()

# --- 1. Build venv -------------------------------------------------------------
if ($Recreate -and (Test-Path $VenvDir)) {
    Write-Step 'Removing existing .venv-build'
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPy)) {
    Write-Step 'Creating build virtual environment (.venv-build)'
    $base = Find-BasePython
    $baseArgs = @()
    if ($base.Count -gt 1) { $baseArgs = $base[1..($base.Count - 1)] }
    Invoke-Native $base[0] ($baseArgs + @('-m', 'venv', $VenvDir))
} else {
    Write-Step 'Reusing .venv-build'
}

# --- 2. Dependencies -----------------------------------------------------------
Write-Step 'Installing build dependencies'
Invoke-Native $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip', '--quiet')
Invoke-Native $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet',
                        '-r', (Join-Path $Root 'requirements-dev.txt'))

# --- 3. PyInstaller ------------------------------------------------------------
foreach ($required in @('main.py', 'assets\icon.ico', 'core\__init__.py', 'ui\__init__.py')) {
    if (-not (Test-Path (Join-Path $Root $required))) { throw "Missing required file: $required" }
}

Write-Step 'Running PyInstaller'
$appOut = Join-Path $DistDir $AppName
if (Test-Path $appOut) { Remove-Item -Recurse -Force $appOut }
Invoke-Native $VenvPy @('-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--distpath', $DistDir, '--workpath', $WorkDir, $SpecFile) -Attempts 3

$exePath = Join-Path $appOut "$AppName.exe"
if (-not (Test-Path $exePath)) { throw "PyInstaller finished but $exePath was not produced." }
$sizeMB = [math]::Round(((Get-ChildItem $appOut -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "Built: $exePath  ($sizeMB MB folder)" -ForegroundColor Green

# --- 4. Installer --------------------------------------------------------------
if ($NoInstaller) {
    Write-Step 'Skipping installer (-NoInstaller)'
} else {
    $iscc = Find-ISCC
    if (-not $iscc) {
        Write-Step 'Inno Setup 6 not found - skipping installer'
        Write-Host '  Install it from https://jrsoftware.org/isdl.php (or: winget install JRSoftware.InnoSetup) and re-run.'
    } else {
        $appVersion = Get-AppVersion
        Write-Step "Building installer v$appVersion with $iscc"
        Invoke-Native $iscc @('/Q', "/DAppVersion=$appVersion", "/DSourceDir=$appOut",
                              "/O$DistDir", $IssFile) -Attempts 5
        Get-ChildItem $DistDir -Filter 'H190K-Downloader-Setup-*.exe' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1 |
            ForEach-Object { Write-Host "Installer: $($_.FullName)  ($([math]::Round($_.Length / 1MB, 1)) MB)" -ForegroundColor Green }
    }
}

$sw.Stop()
Write-Host ("`nDone in {0:N0}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
