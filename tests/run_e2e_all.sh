#!/bin/bash

# tests/run_e2e_all.sh
# End-to-end integration test runner for SecureSignal

set -e

echo "======================================"
echo "Starting SecureSignal E2E Test Suite"
echo "======================================"

# 1. Start Hardhat Node
echo "-> Starting local Hardhat node..."
cd contracts
npx hardhat node &
HARDHAT_PID=$!
cd ..

# Wait for Hardhat to be ready
sleep 3
echo "-> Hardhat node started (PID: $HARDHAT_PID)"

# 2. Run Contracts Tests
echo "-> Running Contract local tests..."
cd contracts
npx hardhat test
if [ $? -ne 0 ]; then
  echo "[ERROR] Contract tests failed."
  kill $HARDHAT_PID
  exit 1
fi

# 3. Deploy contracts for e2e
echo "-> Deploying contracts to local network..."
npx hardhat run scripts/deploy.ts --network localhost
if [ $? -ne 0 ]; then
  echo "[ERROR] Contract deployment failed."
  kill $HARDHAT_PID
  exit 1
fi
cd ..

# 4. Start TEE Service
echo "-> Starting local TEE Python service..."
cd tee-service
source venv/Scripts/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || echo "Virtualenv not found, using global python"
pytest
if [ $? -ne 0 ]; then
  echo "[ERROR] TEE service tests failed."
  kill $HARDHAT_PID
  exit 1
fi
uvicorn main:app --host 0.0.0.0 --port 3000 &
TEE_PID=$!
cd ..

# Wait for TEE service to be ready
sleep 3
echo "-> TEE Service started (PID: $TEE_PID)"

# 5. Run Frontend E2E tests
echo "-> Running Frontend local E2E tests..."
cd frontend/e2e
node e2e-local.mjs
E2E_EXIT_CODE=$?
cd ../..

echo "======================================"
if [ $E2E_EXIT_CODE -eq 0 ]; then
  echo "SUMMARY: ALL PASSED"
else
  echo "SUMMARY: FAILED"
fi
echo "======================================"

# Cleanup
echo "-> Cleaning up background processes..."
kill $HARDHAT_PID
kill $TEE_PID

exit $E2E_EXIT_CODE
