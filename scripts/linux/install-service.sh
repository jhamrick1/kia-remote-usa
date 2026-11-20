#!/usr/bin/env bash
# Raspberry Pi / Linux: run the car service at boot as a systemd service, restarted if it stops.
# Run from anywhere, as the user that owns the repo (it asks for sudo to write the unit):
#   bash scripts/linux/install-service.sh
# Remove: sudo systemctl disable --now kia-remote && sudo rm /etc/systemd/system/kia-remote.service
# Logs:   journalctl -u kia-remote -f   (the service also keeps server.log in its data folder)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No .venv yet. First run, in $ROOT:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
USER_NAME="$(id -un)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/kia-remote"
mkdir -p "$DATA"
chmod 700 "$DATA"

sudo tee /etc/systemd/system/kia-remote.service >/dev/null <<EOF
[Unit]
Description=Car Remote (Kia Connect)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$ROOT
ExecStart=$PY -m server
Restart=always
RestartSec=10
# Hardening: read-only system and repo, writes only to the data folder.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$DATA
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now kia-remote
sleep 3
systemctl --no-pager --lines=5 status kia-remote || true
echo
echo "Car service installed. Open http://localhost:8765 on this machine (or through your tunnel)."
