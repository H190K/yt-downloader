@echo off
rem H190K Downloader - development launcher.
rem Creates .venv on first use, (re)installs requirements when requirements.txt changes,
rem then runs the app from source. Any arguments are passed to main.py.
setlocal
cd /d "%~dp0.."

set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"

if not exist "%VPY%" (
    echo Creating virtual environment in %VENV% ...
    where py >nul 2>&1 && (py -3 -m venv "%VENV%") || (python -m venv "%VENV%")
    if not exist "%VPY%" (
        echo ERROR: Could not create the virtual environment. Install Python 3.10+ from https://www.python.org/
        pause
        exit /b 1
    )
)

rem Reinstall requirements only when requirements.txt differs from the last installed copy.
fc /b "requirements.txt" "%VENV%\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo Installing requirements ...
    "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
    copy /y "requirements.txt" "%VENV%\requirements.installed" >nul
)

"%VPY%" main.py %*
exit /b %ERRORLEVEL%
