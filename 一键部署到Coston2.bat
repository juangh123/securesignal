@echo off
echo =======================================================
echo Deploying SecureSignal to Flare Coston2 Testnet
echo =======================================================
cd contracts
echo [1/4] Installing dependencies...
call npm install
echo.
echo [2/4] Clearing Hardhat Cache...
rmdir /S /Q "%APPDATA%\hardhat-nodejs" 2>nul
rmdir /S /Q "%LOCALAPPDATA%\hardhat-nodejs" 2>nul
echo.
echo [3/4] Deploying Contracts...
call npx hardhat run scripts/deploy.ts --network coston2
echo.
echo [4/4] Registering TEE Key...
call npx hardhat run scripts/setup-tee.ts --network coston2
echo.
echo =======================================================
echo Deployment Complete! Contract addresses have been updated automatically.
echo =======================================================
pause
