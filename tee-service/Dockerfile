# ── NSM helper: official AWS Nitro Enclaves API ─────────────────────────────
# Pinned by digest like the python base below: nsm-attest is copied into the
# enclave image, so a floating rust tag would drift PCR0 on upstream rebuilds.
# Refresh deliberately (docker buildx imagetools inspect rust:1.98-slim-bookworm).
FROM rust:1.99-slim-bookworm@sha256:24707e70a4f1e23d9a7b25202ec43126d28a52294b96aad5c670df1fd1f1d400 AS nsm-builder

WORKDIR /build
COPY aws-nsm-helper/Cargo.toml ./
COPY aws-nsm-helper/Cargo.lock ./
COPY aws-nsm-helper/src ./src
RUN cargo build --release --locked

# ── Base image: pinned by digest for reproducible builds ─────────────────────
# Digest fetched 2026-07-19 from the Docker Registry HTTP API (manifest list,
# multi-arch). To verify or refresh it:
#   docker buildx imagetools inspect python:3.11-slim
# or without Docker:
#   TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
#   curl -sI -H "Authorization: Bearer $TOKEN" \
#     -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
#     https://registry-1.docker.io/v2/library/python/manifests/3.11-slim \
#     | grep -i docker-content-digest
# NOTE: the 3.11-slim tag is rebuilt upstream over time; refresh this digest
# deliberately, never silently.
FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

WORKDIR /app

# build-essential is only a safety net in case a locked dependency ever falls
# back to building from an sdist.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    socat \
    && rm -rf /var/lib/apt/lists/*

# Hash-locked dependency install: requirements-lock.txt pins every package
# (including transitive deps) with sha256 hashes, targeting linux/amd64
# CPython 3.11. requirements.txt remains the human-maintained source of truth;
# regenerate the lock after changing it (procedure in the lock file header,
# helper: tools/gen_requirements_lock.py).
COPY requirements-lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt

# Copy source code
COPY . .
COPY aws_enclave_entrypoint.sh /usr/local/bin/aws_enclave_entrypoint.sh
COPY --from=nsm-builder /build/target/release/nsm-attest /usr/local/bin/nsm-attest
RUN chmod +x /usr/local/bin/nsm-attest /usr/local/bin/aws_enclave_entrypoint.sh

# Expose port
EXPOSE 8000

# Start through the enclave-aware entrypoint. Outside Nitro/GCP the entrypoint
# falls back to the normal uvicorn bootstrap used by Render and local Docker.
CMD ["sh", "/usr/local/bin/aws_enclave_entrypoint.sh"]
