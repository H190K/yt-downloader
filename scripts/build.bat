@echo off
rem Thin wrapper around build.ps1. Extra arguments are passed through,
rem e.g.  build.bat -Version 2.1.0 -NoInstaller
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
exit /b %ERRORLEVEL%
