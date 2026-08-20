@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "RAMAIR_PYTHON="
where py >nul 2>nul && set "RAMAIR_PYTHON=py -3"
if not defined RAMAIR_PYTHON where python >nul 2>nul && set "RAMAIR_PYTHON=python"
if not defined RAMAIR_PYTHON (
  echo MISSING: Python 3.10 or newer was not found on Windows.
  where winget >nul 2>nul
  if not errorlevel 1 (
    set /p "RAMAIR_INSTALL_PYTHON=Install Python 3.12 automatically with winget? [Y/n]: "
    if /i not "!RAMAIR_INSTALL_PYTHON!"=="n" (
      winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
      echo Python installation finished. Close this window and run this launcher again.
      pause
      exit /b 0
    )
  )
  echo Install Python from https://www.python.org/downloads/windows/
  echo Enable either python.exe or the Python launcher py.exe, then rerun this file.
  pause
  exit /b 2
)

echo Starting RamAir: Design and CFD from the Windows source checkout...
%RAMAIR_PYTHON% run_ramair_cfd2d_app.py %*
set "RAMAIR_EXIT=%ERRORLEVEL%"
if not "%RAMAIR_EXIT%"=="0" (
  echo.
  echo RamAir launcher stopped with exit code %RAMAIR_EXIT%.
  pause
)
exit /b %RAMAIR_EXIT%
