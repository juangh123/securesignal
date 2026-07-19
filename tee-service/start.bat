@echo off
REM Start the SecureSignal TEE service.
REM Must run from the tee-service directory so that `main:app` and the
REM crypto/ analysis/ attestation/ flare/ packages resolve correctly.
cd /d "%~dp0"
uvicorn main:app --host 0.0.0.0 --port 8000
