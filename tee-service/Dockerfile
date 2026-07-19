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
FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

WORKDIR /app

# build-essential is only a safety net in case a locked dependency ever falls
# back to building from an sdist.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
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

# Expose port
EXPOSE 8000

# Start server — honor the PORT env injected by hosts like Render
# (default 8000 for local/docker-compose use)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
