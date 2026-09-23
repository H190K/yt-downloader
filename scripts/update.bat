@echo off
rem Downloads missing tools and updates yt-dlp, ffmpeg and deno.
rem - Copied next to an installed/built "H190K Downloader.exe": runs the exe with --update.
rem - In the source tree (scripts\): runs "python main.py --update" from the repo root
rem   (using run.bat's .venv if present).
setlocal
if exist "%~dp0H190K Downloader.exe" (
    "%~dp0H190K Downloader.exe" --update
    goto :done
)

cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --update
) else (
    where py >nul 2>&1 && (py -3 main.py --update) || (python main.py --update)
)

:done
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
