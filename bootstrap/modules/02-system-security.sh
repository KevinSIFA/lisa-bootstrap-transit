#!/usr/bin/env bash
# ============================================================================
# 02-system-security.sh — Securisation du systeme et utilisateur applicatif
# ============================================================================
# - Cree l'utilisateur 'openclaw' (compte applicatif, pas humain)
# - Configure sudo NOPASSWD limite (uniquement pour les commandes utiles)
# - Configure SSH : desactive password, force cle, garde root accessible
#   (sera restreint via Tailscale dans le module 06)
# - Active UFW : tout ferme sauf SSH 22 (sera retreint plus tard)
# - Installe et configure unattended-upgrades (security only)
# - Installe et configure fail2ban (protection SSH brute-force)
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 02 : securite systeme + utilisateur applicatif"

require_var ADMIN_EMAIL


# --- 1. Utilisateur 'openclaw' ----------------------------------------------
if id openclaw &>/dev/null; then
    log_ok "Utilisateur 'openclaw' deja present"
else
    log_info "Creation de l'utilisateur 'openclaw'..."
    useradd -m -s /bin/bash -c "LISA application user" openclaw
    # Mot de passe verrouille (login uniquement par cle)
    passwd -l openclaw > /dev/null
    log_ok "Utilisateur 'openclaw' cree (login par cle uniquement)"
fi

# Dossier SSH pour openclaw
mkdir -p /home/openclaw/.ssh
chmod 700 /home/openclaw/.ssh
touch /home/openclaw/.ssh/authorized_keys
chmod 600 /home/openclaw/.ssh/authorized_keys
chown -R openclaw:openclaw /home/openclaw/.ssh


# --- 2. sudo NOPASSWD limite pour openclaw ----------------------------------
# Openclaw peut redemarrer ses propres services et lire ses logs, sans
# privilege global. Strict allowlist.
sudoers_file="/etc/sudoers.d/openclaw-lisa"
if [[ ! -f "${sudoers_file}" ]]; then
    log_info "Configuration de sudo limite pour openclaw..."
    cat > "${sudoers_file}" <<'EOF'
# LISA — sudo limite pour l'utilisateur applicatif
# Commandes autorisees sans mot de passe (allowlist stricte)

openclaw ALL=(root) NOPASSWD: /bin/systemctl start lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl stop lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl restart lisa-*
openclaw ALL=(root) NOPASSWD: /bin/systemctl status lisa-*
openclaw ALL=(root) NOPASSWD: /bin/journalctl -u lisa-*
openclaw ALL=(root) NOPASSWD: /bin/journalctl --since *
EOF
    chmod 440 "${sudoers_file}"
    visudo -c -f "${sudoers_file}" > /dev/null
    log_ok "sudo configure pour openclaw (allowlist stricte)"
else
    log_ok "sudoers openclaw deja configure"
fi


# --- 3. SSH hardening -------------------------------------------------------
sshd_drop="/etc/ssh/sshd_config.d/99-lisa.conf"
log_info "Application du hardening SSH..."
cat > "${sshd_drop}" <<EOF
# LISA — hardening SSH (overrides /etc/ssh/sshd_config)
# Ce fichier est gere par bootstrap-lisa.sh, ne pas editer a la main.

# Authentication
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
KbdInteractiveAuthentication no
UsePAM yes

# Connection
LoginGraceTime 30
MaxAuthTries 3
MaxSessions 5
ClientAliveInterval 300
ClientAliveCountMax 2

# Disable unused features
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
GatewayPorts no

# Logging
LogLevel VERBOSE
EOF
chmod 644 "${sshd_drop}"

# Verifie la config et restart si OK
if sshd -t 2>&1 | tee -a "${LOG_FILE}"; then
    systemctl reload ssh || systemctl reload sshd
    log_ok "SSH hardening applique (root: cle uniquement, password: off)"
else
    log_error "Config SSH invalide, modification annulee"
    rm -f "${sshd_drop}"
    exit 1
fi


# --- 4. UFW (firewall) ------------------------------------------------------
log_info "Configuration du pare-feu UFW..."
apt-get -qq -y install ufw

# Politique par defaut : deny in, allow out
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing

# SSH (sera restreint a Tailscale dans le module 06)
ufw allow 22/tcp comment 'SSH (provisoire, sera restreint a Tailscale)'

# Active UFW
ufw --force enable > /dev/null
log_ok "UFW actif (deny in / allow out, SSH ouvert temporairement)"


# --- 5. unattended-upgrades -------------------------------------------------
log_info "Installation et configuration de unattended-upgrades..."
apt-get -qq -y install unattended-upgrades apt-listchanges

# Active les mises a jour automatiques (security only)
cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# Email admin sur erreur uniquement
sed -i "s|^//Unattended-Upgrade::Mail .*|Unattended-Upgrade::Mail \"${ADMIN_EMAIL}\";|" \
    /etc/apt/apt.conf.d/50unattended-upgrades
sed -i 's|^//Unattended-Upgrade::MailReport.*|Unattended-Upgrade::MailReport "only-on-error";|' \
    /etc/apt/apt.conf.d/50unattended-upgrades
sed -i 's|^//Unattended-Upgrade::Automatic-Reboot "false";|Unattended-Upgrade::Automatic-Reboot "false";|' \
    /etc/apt/apt.conf.d/50unattended-upgrades

systemctl enable --now unattended-upgrades > /dev/null
log_ok "unattended-upgrades active (security only, mail sur erreur)"


# --- 6. fail2ban ------------------------------------------------------------
log_info "Installation et configuration de fail2ban..."
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
log_ok "fail2ban actif (SSH : 3 tentatives en 10 min = bannissement 1h)"


log_ok "Module 02 termine"
