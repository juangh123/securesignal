#!/bin/sh
set -eu

# Nitro Enclaves have no network stack. Forward the parent's TCP proxy to the
# FastAPI process over the enclave's vsock device.
if [ -e /dev/nsm ]; then
    export ENCLAVE_PORT="${ENCLAVE_PORT:-8000}"
    export PORT="${PORT:-8000}"
    python /app/aws_vsock_proxy.py &
    python /app/aws_vsock_rpc_bridge.py &
    exec python /app/aws_nitro_bootstrap.py
fi

exec python /app/gcp_secrets_bootstrap.py
