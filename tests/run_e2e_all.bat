@echo off
setlocal

set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%BASH%" (
  echo [ERROR] Git Bash was not found at "%BASH%".
  exit /b 1
)

pushd "%~dp0.."
"%BASH%" "./tests/run_e2e_all.sh"
set "E2E_EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %E2E_EXIT_CODE%
