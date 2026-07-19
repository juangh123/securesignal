#!/usr/bin/env bash
# Start the SecureSignal TEE service.
# Must run from the tee-service directory so that `main:app` and the
# crypto/ analysis/ attestation/ flare/ packages resolve correctly.
cd "$(dirname "$0")"
exec uvicorn main:app --host 0.0.0.0 --port 8000
