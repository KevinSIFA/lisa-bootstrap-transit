#!/usr/bin/env bash
# ============================================================================
# 02-system-security.sh — Securisation systeme + utilisateur applicatif
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 02 : securite systeme + utilisateur applicatif"

require_var ADMIN_EMAIL
export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock


# --- 1. Utilisateur openclaw ------------------------------------------------
if id openclaw &>/dev/null; then
    log_ok "Utilisateur 'openclaw' deja present"
else
    log_info "Creation de l'utilisateur 'openclaw'..."
    useradd -m -s /bin/bash -c "LISA application user" openclaw
    passwd -l openclaw > /dev/null
    log_ok "Utilisateur 'openclaw' cree"
fi

mkdir -p /home/openclaw/.ssh
chmod 700 /home/openclaw/.ssh
touch /home/openclaw/.ssh/authorized_keys
chmod 600 /home/openclaw/.ssh/authorized_keys
chown -R openclaw:openclaw /home/openclaw/.ssh


# --- 2. sudo NOPASSWD limite pour openclaw ----------------------------------
sudoers_file="/etc/sudoers.d/openclaw-lisa"
if [[ ! -f "${sudoers_file}" ]]; then
    log_info "Configuration de sudo limite pour openclaw..."
    cat > "${sudoers_file}" <<'EOF'
# LISA — sudo limite pour l'utilisateur applicatif
openclaw ALL=(root) NOPASSWD: /bin/systemctl start lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl stop lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl restart lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl status lisa-*
openclaw ALL=(root) NOPASSWD: /bin/journalctl -u lisa-*
EOF
    chmod 440 "${sudoers_file}"
    visudo -c -f "${sudoers_file}" > /dev/null
    log_ok "sudo configure pour openclaw"
else
    log_ok "sudoers openclaw deja configure"
fi


# --- 3. SSH hardening -------------------------------------------------------
sshd_drop="/etc/ssh/sshd_config.d/99-lisa.conf"
log_info "Application du hardening SSH..."
cat > "${sshd_drop}" <<EOF
# LISA — hardening SSH
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
LoginGraceTime 30
MaxAuthTries 3
MaxSessions 5
ClientAliveInterval 300
ClientAliveCountMax 2
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
GatewayPorts no
LogLevel VERBOSE
EOF
chmod 644 "${sshd_drop}"

if sshd -t 2>&1; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    log_ok "SSH hardening applique"
else
    log_error "Config SSH invalide, modification annulee"
    rm -f "${sshd_drop}"
    exit 1
fi


# --- 4. UFW (firewall) ------------------------------------------------------
log_info "Configuration du pare-feu UFW..."
apt-get -qq -y install ufw

ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH (provisoire, sera restreint a Tailscale)'
ufw --force enable > /dev/null
log_ok "UFW actif (deny in / allow out, SSH ouvert temporairement)"


# --- 5. unattended-upgrades -------------------------------------------------
log_info "Installation et configuration de unattended-upgrades..."
wait_for_apt_lock
apt-get -qq -y install unattended-upgrades apt-listchanges

cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

sed -i "s|^//Unattended-Upgrade::Mail .*|Unattended-Upgrade::Mail \"${ADMIN_EMAIL}\";|" \
    /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null || true
sed -i 's|^//Unattended-Upgrade::MailReport.*|Unattended-Upgrade::MailReport "only-on-error";|' \
    /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null || true

systemctl enable --now unattended-upgrades > /dev/null
log_ok "unattended-upgrades active"


# --- 6. fail2ban ------------------------------------------------------------
log_info "Installation et configuration de fail2ban..."
wait_for_apt_lock
apt-get -qq -y install fail2ban

cat > /etc/fail2ban/jail.d/lisa-ssh.local <<EOF
[sshd]
enabled  = true
port     = ssh
filter   = sshd
backend  = systemd
maxretry = 3
findtime = 600
bantime  = 3600
destemail = ${ADMIN_EMAIL}
sender   = root@${VPS_HOSTNAME:-localhost}
action   = %(action_)s
EOF

systemctl enable --now fail2ban > /dev/null
systemctl restart fail2ban
log_ok "fail2ban actif (SSH : 3 tentatives en 10 min = ban 1h)"


log_ok "Module 02 termine"
