#!/usr/bin/env bash
# ==============================================================================
# Syncthing + Proton VPN + 60-Day Retention Hub Installer
# Author: yb007-sketch
# Repository: https://github.com/yb007-sketch/syncthing-proton-vault
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}  Syncthing + Proton VPN + 60-Day Retention Hub Setup  ${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Directories
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"
STATE_DIR="$HOME/.local/state/syncthing"
SYNC_DIR="${SYNC_DIR:-$HOME/AndroidSync/Storage}"
RETENTION_DIR="${RETENTION_DIR:-$HOME/AndroidSync/Retention_60Days}"

echo -e "\n${YELLOW}[1/5] Creating application and storage directories...${NC}"
mkdir -p "$BIN_DIR" "$SYSTEMD_DIR" "$STATE_DIR" "$SYNC_DIR" "$RETENTION_DIR"

# 2. Copy Executables
echo -e "${YELLOW}[2/5] Installing daemon executables...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/bin/proton-natpmp.py" "$BIN_DIR/proton-natpmp.py"
cp "$SCRIPT_DIR/bin/syncthing-dashboard.py" "$BIN_DIR/syncthing-dashboard.py"
chmod +x "$BIN_DIR/proton-natpmp.py" "$BIN_DIR/syncthing-dashboard.py"

# 3. Copy .stignore if not already present
if [ ! -f "$SYNC_DIR/.stignore" ]; then
    echo -e "${YELLOW}[3/5] Installing optimized .stignore template...${NC}"
    cp "$SCRIPT_DIR/config/stignore.template" "$SYNC_DIR/.stignore"
fi

# 4. Systemd Units
echo -e "${YELLOW}[4/5] Installing and enabling user systemd services...${NC}"
cp "$SCRIPT_DIR/systemd/proton-natpmp.service" "$SYSTEMD_DIR/proton-natpmp.service"
cp "$SCRIPT_DIR/systemd/syncthing-dashboard.service" "$SYSTEMD_DIR/syncthing-dashboard.service"

systemctl --user daemon-reload
systemctl --user enable --now proton-natpmp.service
systemctl --user enable --now syncthing-dashboard.service

# 5. Output Summary
LAN_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || echo "127.0.0.1")

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}  Installation Succeeded!                             ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "Dashboard is live and accessible at:"
echo -e "  - Local:   ${BLUE}http://localhost:8386${NC}"
echo -e "  - LAN:     ${BLUE}http://${LAN_IP}:8386${NC}"
echo -e "\nServices running:"
echo -e "  - proton-natpmp.service:       $(systemctl --user is-active proton-natpmp.service)"
echo -e "  - syncthing-dashboard.service: $(systemctl --user is-active syncthing-dashboard.service)"
echo -e "\nActive Storage Directory:   $SYNC_DIR"
echo -e "60-Day Retention Holding:   $RETENTION_DIR"
echo -e "\nNext step: Open http://localhost:8386 in your browser to inspect your files and pair your phone."
