@echo off
setlocal
cd /d "%~dp0"
call START_RAMAIR_CFD2D_APP.bat --install --install-system
exit /b %ERRORLEVEL%
