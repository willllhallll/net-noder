#!/usr/bin/env bash
# Idempotent provisioning for the net-noder dev container.
# Installs tshark, a Python virtualenv with both packages, and the web deps.
set -euo pipefail

ROOT="/workspaces/net-noder"
VENV="$ROOT/.venv"

echo "==> apt: tshark + python venv/pip tooling"
sudo apt-get update -qq
# tshark is used read-only here (we parse files, never capture), so decline the
# setuid dumpcap helper to keep the install non-interactive.
echo "wireshark-common wireshark-common/install-setuid boolean false" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  tshark python3-venv python3-pip

echo "==> Python virtualenv at $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip wheel

echo "==> pip install: ingest + server (editable)"
"$VENV/bin/pip" install --quiet -e "$ROOT/ingest" -e "$ROOT/server"

echo "==> npm install: web"
export NVM_DIR="/usr/local/share/nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd "$ROOT/web"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi

echo "==> net-noder dev container ready."
echo "    ingest:  netnoder-ingest <pcap-dir-or-file>"
echo "    api:     netnoder-api        (http://localhost:8000)"
echo "    web dev: (cd web && npm run dev)   (http://localhost:5173)"
