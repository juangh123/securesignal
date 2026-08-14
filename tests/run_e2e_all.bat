@echo off
setlocal
echo ======================================
echo Starting SecureSignal E2E Test Suite
echo ======================================

:: 1. Start Hardhat Node
echo -^> Starting local Hardhat node...
cd contracts
start /B cmd /c "npx hardhat node > hardhat.log 2>&1"
cd ..
timeout /t 3 /nobreak >nul
echo -^> Hardhat node started in background.

:: 2. Run Contracts Tests
echo -^> Running Contract local tests...
cd contracts
call npx hardhat test
if %errorlevel% neq 0 (
    echo [ERROR] Contract tests failed.
    goto cleanup
)

:: 3. Deploy contracts for e2e
echo -^> Deploying contracts to local network...
call npx hardhat run scripts/deploy.ts --network localhost
if %errorlevel% neq 0 (
    echo [ERROR] Contract deployment failed.
    goto cleanup
)
cd ..

:: 4. Start TEE Service
echo -^> Starting local TEE Python service...
cd tee-service

:: Check if virtual environment exists and activate it
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

call python -m pytest
if %errorlevel% neq 0 (
    echo [ERROR] TEE service tests failed.
    goto cleanup
)

:: Ensure a deterministic tee key is used for E2E
set "TEE_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
set "PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
set "RPC_URL=http://127.0.0.1:8545"
set "ANALYSIS_OFFLINE=1"

start /B "Uvicorn" cmd /c "python -m uvicorn main:app --host 0.0.0.0 --port 3000"
cd ..
timeout /t 5 /nobreak >nul
echo -^> TEE Service started in background.

echo -^> Registering TEE key on-chain...
cd contracts
call npx hardhat run scripts/setup-tee.ts --network localhost
timeout /t 5 /nobreak >nul
cd ..

:: 5. Run Frontend E2E tests
echo -^> Running Frontend local E2E tests...
cd frontend\e2e
node e2e-local.mjs
set E2E_EXIT_CODE=%errorlevel%
cd ..\..

echo ======================================
if %E2E_EXIT_CODE% equ 0 (
  echo SUMMARY: ALL PASSED
) else (
  echo SUMMARY: FAILED
)
echo ======================================

:cleanup
echo -^> Cleaning up background processes...
taskkill /F /IM node.exe /T >nul 2>&1
taskkill /F /IM uvicorn.exe /T >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1

exit /b %E2E_EXIT_CODE%
