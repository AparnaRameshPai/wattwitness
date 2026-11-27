#!/usr/bin/env bash
# Set up (or update) the Vultr VM (Ubuntu 22.04/24.04). Run from the project folder as a normal sudo user:
#   sudo bash deploy/setup_vm.sh your-domain-or-IP.sslip.io
# Tip: if you have no domain, use <VM-IP-with-dashes>.sslip.io (e.g. 45-32-10-20.sslip.io): Caddy then gets real HTTPS.
# Safe to re-run after `git pull`: it rebuilds the sandbox image and restarts the app.
set -euo pipefail
DOMAIN="${1:?usage: sudo bash deploy/setup_vm.sh <domain>}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-root}"   # the app runs as this user, not as root

echo "== 1. Docker"
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
usermod -aG docker "$APP_USER"

echo "== 2. gVisor (runsc): an extra kernel boundary for sandboxes"
if ! command -v runsc >/dev/null; then
  apt-get update && apt-get install -y apt-transport-https ca-certificates curl gnupg
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update && apt-get install -y runsc
fi
grep -q runsc /etc/docker/daemon.json 2>/dev/null || { runsc install; systemctl restart docker; }

echo "== 3. Python environment (owned by $APP_USER)"
apt-get install -y python3-venv python3-pip
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/data"

echo "== 4. Sandbox image"
docker build -q -t receipts-sandbox:latest "$APP_DIR/sandbox-image"

echo "== 5. App service"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" "$APP_DIR/deploy/receipts.service" \
  > /etc/systemd/system/receipts.service
systemctl daemon-reload
systemctl enable receipts
systemctl restart receipts

echo "== 6. Caddy (automatic HTTPS)"
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi
sed "s#__DOMAIN__#$DOMAIN#g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "== 7. Firewall: only SSH and web"
if command -v ufw >/dev/null; then ufw allow OpenSSH; ufw allow 80; ufw allow 443; ufw --force enable; fi

if [ ! -f /etc/agent/inference.env ] && ! grep -qs '^VULTR_INFERENCE_KEY=.\+' "$APP_DIR/.env"; then
  echo "!! No inference key found. Put it in /etc/agent/inference.env as VULTR_INFERENCE_API_KEY=... (root:$APP_USER, 640)"
fi
echo "Done. Open https://$DOMAIN  (models + SANDBOX_RUNTIME=runsc go in $APP_DIR/.env; after editing: sudo systemctl restart receipts)"
