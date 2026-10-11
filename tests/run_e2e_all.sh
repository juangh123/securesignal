#!/bin/bash

# tests/run_e2e_all.sh
# End-to-end integration test runner for SecureSignal
#
# Runs with `set -e`, so any failing step aborts the run, and an EXIT trap
# always kills the background Hardhat node and TEE service.

set -e

HARDHAT_PID=""
TEE_PID=""

cleanup() {
  echo "-> Cleaning up background processes..."
  if [ -n "$TEE_PID" ]; then kill "$TEE_PID" 2>/dev/null || true; fi
  if [ -n "$HARDHAT_PID" ]; then kill "$HARDHAT_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT

wait_for_rpc() {
  local url="$1"
  local label="$2"
  for attempt in $(seq 1 180); do
    if node -e "fetch(process.argv[1],{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'eth_chainId',params:[]})}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" "$url" 2>/dev/null; then
      return 0
    fi
    if [ $((attempt % 15)) -eq 0 ]; then
      echo "   ...still waiting for $label (${attempt}s)"
    fi
    sleep 1
  done
  echo "[ERROR] $label did not become ready: $url" >&2
  return 1
}

wait_for_http() {
  local url="$1"
  local label="$2"
  for attempt in $(seq 1 120); do
    if node -e "fetch(process.argv[1]).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" "$url" 2>/dev/null; then
      return 0
    fi
    if [ $((attempt % 15)) -eq 0 ]; then
      echo "   ...still waiting for $label (${attempt}s)"
    fi
    sleep 1
  done
  echo "[ERROR] $label did not become ready: $url" >&2
  return 1
}

echo "======================================"
echo "Starting SecureSignal E2E Test Suite"
echo "======================================"

# 1. Start Hardhat Node
echo "-> Starting local Hardhat node..."
cd contracts
npx --no-install hardhat node &
HARDHAT_PID=$!
cd ..

# Wait for Hardhat to be ready
wait_for_rpc "http://127.0.0.1:8545" "Hardhat node"
echo "-> Hardhat node started (PID: $HARDHAT_PID)"

# 2. Run Contracts Tests
echo "-> Running Contract local tests..."
cd contracts
npx --no-install hardhat test
cd ..

# 3. Deploy contracts for e2e
echo "-> Deploying contracts to local network..."
cd contracts
npx --no-install hardhat run scripts/deploy.ts --network localhost
npx --no-install hardhat run scripts/setup-tee.ts --network localhost
cd ..

# 4. Start TEE Service
echo "-> Starting local TEE Python service..."
cd tee-service
# Call the interpreter directly: the venv's pytest/uvicorn console wrappers
# have Windows shebangs that Git Bash cannot execute reliably (paths with
# spaces), while `python -m` works in every layout. CI has no venv directory
# and falls back to its global interpreter.
PYTHON=""
if [ -x venv/bin/python ]; then
  PYTHON="$PWD/venv/bin/python"
elif [ -x venv/Scripts/python.exe ]; then
  PYTHON="$PWD/venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
fi
if [ -z "$PYTHON" ]; then
  echo "[ERROR] no Python interpreter found" >&2
  exit 1
fi
if ! "$PYTHON" -c "import pytest, uvicorn" >/dev/null 2>&1; then
  echo "[ERROR] pytest/uvicorn missing for $PYTHON; install tee-service/requirements-dev.txt" >&2
  exit 1
fi

# Match setup-tee.ts localhost defaults: account #1 signs as the TEE,
# account #0 pays for relayer transactions.
export TEE_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export RPC_URL=http://127.0.0.1:8545
export ANALYSIS_OFFLINE=1

"$PYTHON" -m pytest -q
"$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 3000 &
TEE_PID=$!
cd ..

# Wait for TEE service to be ready
wait_for_http "http://127.0.0.1:3000/public-key" "TEE service"
echo "-> TEE Service started (PID: $TEE_PID)"

# 5. Run Frontend E2E tests
echo "-> Running Frontend local E2E tests..."
cd frontend/e2e
E2E_EXIT_CODE=0
if ! node e2e-local.mjs; then
  E2E_EXIT_CODE=1
fi
cd ../..

echo "======================================"
if [ "$E2E_EXIT_CODE" -eq 0 ]; then
  echo "SUMMARY: ALL PASSED"
else
  echo "SUMMARY: FAILED"
fi
echo "======================================"

exit "$E2E_EXIT_CODE"
