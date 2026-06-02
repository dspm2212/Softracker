#!/usr/bin/env bash
# instalar.sh — Deploy monitoreo-servidor on a Linux server.
#
# Run as root from the monitoreo-servidor/ directory:
#   sudo bash deploy/instalar.sh
#
# Author: Daniel Perez

set -euo pipefail

INSTALL_DIR=/opt/monitoreo-salas
DATA_DIR=/var/lib/monitoreo-salas
LOG_DIR=/var/log/monitoreo-salas
CONFIG_DIR=/etc/monitoreo-salas
SERVICE_USER=monitoreo
SERVICE_FILE=deploy/monitoreo-api.service

# ── Helpers ────────────────────────────────────────────────────────────────────
log() { echo "[instalar] $*"; }
die() { echo "[instalar] ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo)."
[[ -f requirements.txt ]] || die "Run this script from the monitoreo-servidor/ directory."

# ── Service user ──────────────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    log "Created user: $SERVICE_USER"
fi

# ── Directories ───────────────────────────────────────────────────────────────
for dir in "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR" "$CONFIG_DIR"; do
    mkdir -p "$dir"
done
mkdir -p "$DATA_DIR/parquet" "$DATA_DIR/rejected" "$DATA_DIR/log"
log "Directories ready."

# ── Application files ─────────────────────────────────────────────────────────
rsync -a --delete app/ "$INSTALL_DIR/app/"
rsync -a scripts/    "$INSTALL_DIR/scripts/"
cp requirements.txt  "$INSTALL_DIR/"
log "Application files copied to $INSTALL_DIR."

# ── Python venv ───────────────────────────────────────────────────────────────
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
log "Python venv created and dependencies installed."

# ── Config files (create only if absent — never overwrite) ────────────────────
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    cp deploy/config.example.yaml "$CONFIG_DIR/config.yaml"
    log "Created $CONFIG_DIR/config.yaml from example."
    log "  *** Edit $CONFIG_DIR/config.yaml before starting the service. ***"
fi

if [[ ! -f "$CONFIG_DIR/tokens.yaml" ]]; then
    echo "rooms: {}" > "$CONFIG_DIR/tokens.yaml"
    log "Created empty $CONFIG_DIR/tokens.yaml."
    log "  *** Generate tokens before starting: ***"
    log "  *** $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/scripts/generar_tokens.py --salas SALA-01 SALA-02 ***"
fi

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$LOG_DIR" "$INSTALL_DIR"
chown root:"$SERVICE_USER" "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR"/*.yaml
log "Permissions set."

# ── systemd service ───────────────────────────────────────────────────────────
cp "$SERVICE_FILE" /etc/systemd/system/monitoreo-api.service
systemctl daemon-reload
systemctl enable monitoreo-api.service
log "systemd service installed and enabled."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo " Installation complete!"
echo "============================================="
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.yaml"
echo "  2. Generate room tokens:"
echo "       $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/scripts/generar_tokens.py \\"
echo "           --salas SALA-01 SALA-02  --tokens-file $CONFIG_DIR/tokens.yaml"
echo "  3. Set admin_token_hash in config.yaml:"
echo "       $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/scripts/generar_tokens.py \\"
echo "           --admin --config-file $CONFIG_DIR/config.yaml"
echo "  4. Start the service:"
echo "       systemctl start monitoreo-api"
echo "  5. Check status:"
echo "       systemctl status monitoreo-api"
echo "       journalctl -u monitoreo-api -f"
