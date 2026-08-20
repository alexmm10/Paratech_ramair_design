@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-catia\Scripts\python.exe" (
  echo ERROR: .venv-catia is missing. Run SETUP_CATIA_PREPROCESSOR_WINDOWS.bat first.
  pause
  exit /b 2
)

set "RAMAIR_CONFIG=Application Support\Configurations\default_case_config.json"
if not "%~1"=="" set "RAMAIR_CONFIG=%~1"
if not exist "%RAMAIR_CONFIG%" (
  echo ERROR: configuration file not found: %RAMAIR_CONFIG%
  pause
  exit /b 2
)

echo Generating fresh CATIA inputs from:
echo   %RAMAIR_CONFIG%
".venv-catia\Scripts\python.exe" preprocess_ramair_main.py --config "%RAMAIR_CONFIG%"
if errorlevel 1 (
  echo.
  echo ERROR: preprocessor execution failed. CATIA was not started.
  pause
  exit /b 1
)

".venv-catia\Scripts\python.exe" "CATIA\Utilities\VERIFY_CATIA_PACKAGE.py"
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo CATIA inputs are ready in: %CD%\CATIA\Inputs
start "" "%CD%\CATIA\Inputs"
exit /b 0
