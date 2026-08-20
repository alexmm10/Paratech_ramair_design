@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: py.exe was not found. Install 64-bit Python 3.10 or newer first.
  pause
  exit /b 2
)

if not exist ".venv-catia\Scripts\python.exe" (
  echo Creating .venv-catia...
  py -3 -m venv .venv-catia
  if errorlevel 1 goto :failed
)

echo Installing the preprocessor dependencies. Pip is not upgraded automatically.
".venv-catia\Scripts\python.exe" -m pip install --disable-pip-version-check -r "CATIA\Utilities\requirements-catia-preprocessor.txt"
if errorlevel 1 goto :failed

".venv-catia\Scripts\python.exe" "CATIA\Utilities\VERIFY_CATIA_PACKAGE.py"
if errorlevel 1 goto :failed
echo.
echo Environment ready. Run RUN_CATIA_PREPROCESSOR_WINDOWS.bat to regenerate CATIA\Inputs.
exit /b 0

:failed
echo.
echo ERROR: CATIA preprocessor setup failed.
pause
exit /b 1
