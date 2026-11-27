#!/usr/bin/env bash
# Prepare a FRESH Vultr Ubuntu 24.04 VM before deploy/setup_vm.sh. Two phases, so you can't lock yourself out:
#
#   1. As root (fresh VM, SSH key added in the Vultr console):
#        ssh root@<VM-IP> 'bash -s' < deploy/bootstrap_vm.sh
#      -> system update, a non-root sudo user (default: receipts) with root's SSH key, firewall for SSH/HTTP/HTTPS.
#
#   2. Once `ssh receipts@<VM-IP>` works:
#        ssh receipts@<VM-IP> 'sudo bash -s -- --harden' < deploy/bootstrap_vm.sh
#      -> disables root login and password login over SSH.
#
# Then: clone the repo as that user, put the inference key in /etc/agent/inference.env, run deploy/setup_vm.sh.
# Safe to re-run.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP_USER="${APP_USER:-receipts}"
log() { printf '\n==> %s\n' "$*"; }

if [ "${1:-}" = "--harden" ]; then
  log "SSH hardening: no root login, no passwords"
  # sshd keeps the FIRST value it reads; cloud-init ships 50-cloud-init.conf, so this file must sort before it.
  cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
  sshd -t
  systemctl reload ssh
  sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication) '
  exit 0
fi

log "Waiting for cloud-init to finish"
cloud-init status --wait >/dev/null 2>&1 || true

log "System update"
APT="apt-get -o DPkg::Lock::Timeout=900"
$APT update -y
$APT -o Dpkg::Options::="--force-confold" full-upgrade -y
$APT install -y ca-certificates curl gnupg git jq

log "Non-root user '$APP_USER' (sudo, same SSH key as root)"
id "$APP_USER" &>/dev/null || adduser --disabled-password --gecos "" "$APP_USER"
usermod -aG sudo "$APP_USER"
echo "$APP_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$APP_USER"
chmod 440 "/etc/sudoers.d/90-$APP_USER"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
install -m 600 -o "$APP_USER" -g "$APP_USER" /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"

log "Firewall: SSH, HTTP, HTTPS"
if command -v ufw >/dev/null; then ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; ufw --force enable; fi

log "Folder for the inference key (root-owned; readable by $APP_USER)"
install -d -m 750 -o root -g "$APP_USER" /etc/agent

cat <<EOF

Done. Next:
  1. Check that 'ssh $APP_USER@<VM-IP>' works, then harden SSH (phase 2, see the top of this file).
  2. Store the Vultr Serverless Inference key without it ever touching a file in the repo or your shell history:
       ssh -t $APP_USER@<VM-IP> 'read -rsp "Key: " K && printf "VULTR_INFERENCE_API_KEY=%s\n" "\$K" | sudo tee /etc/agent/inference.env >/dev/null && sudo chown root:$APP_USER /etc/agent/inference.env && sudo chmod 640 /etc/agent/inference.env'
  3. As $APP_USER: git clone the repo, cp .env.example .env, then: sudo bash deploy/setup_vm.sh <VM-IP-with-dashes>.sslip.io
EOF
