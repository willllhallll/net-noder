# syntax=docker/dockerfile:1
#
# Encapsulated, production-style image for net-noder.
#
# Single runtime container: FastAPI (uvicorn) serves both the built React UI and the
# /api routes on port 8000 — no Vite dev server, no editor/dev tooling. Runtimes are
# pinned to stable LTS (Node 22 to build the web app, Python 3.12 to run everything),
# both of which satisfy the project's version constraints.

# ---- Stage 1: build the web UI --------------------------------------------------
FROM node:22-slim AS web-build
WORKDIR /build/web

# Install deps from the lockfile first for reproducible, cache-friendly builds.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Build the static bundle (tsc && vite build) -> /build/web/dist
COPY web/ ./
RUN npm run build

# ---- Stage 2: runtime -----------------------------------------------------------
FROM python:3.12-slim AS runtime

# tshark is used read-only (we parse pcap files, never capture), so decline the
# setuid dumpcap helper to keep the install non-interactive — same posture as dev.
RUN echo "wireshark-common wireshark-common/install-setuid boolean false" \
      | debconf-set-selections \
    && DEBIAN_FRONTEND=noninteractive apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       tshark \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Regular (non-editable) installs so the netnoder-api / netnoder-ingest console
# scripts land on PATH without dev-mode source linking.
COPY ingest/ ./ingest/
COPY server/ ./server/
RUN pip install --no-cache-dir ./ingest ./server

# Built UI lives at a stable path that the server points to via NETNODER_WEB_DIST.
COPY --from=web-build /build/web/dist /app/web/dist

ENV NETNODER_HOST=0.0.0.0 \
    NETNODER_PORT=8000 \
    NETNODER_DB=/app/data/netnoder.duckdb \
    NETNODER_WEB_DIST=/app/web/dist

# Data (DuckDB store + captures) is mounted here at run time.
RUN mkdir -p /app/data \
    && useradd --create-home --uid 10001 netnoder \
    && chown -R netnoder:netnoder /app
USER netnoder

EXPOSE 8000
CMD ["netnoder-api"]
