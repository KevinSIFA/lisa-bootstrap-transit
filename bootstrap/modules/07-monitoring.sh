#!/usr/bin/env bash
# ============================================================================
# 07-monitoring.sh — Netdata bind loopback + alertes Telegram
# ============================================================================
# - Installe Netdata via le script officiel (stable channel, sans telemetrie)
# - Configure le bind sur 127.0.0.1 uniquement (pas d'exposition publique)
# - Active les alertes Telegram natives (utilise notre bot LISA)
# - Ouvre l'acces au dashboard Netdata uniquement via Tailscale
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 07 : monitoring (Netdata)"

require_var TELEGRAM_BOT_TOKEN
require_var TELEGRAM_ALLOWED_USER_IDS
export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock


# --- 1. Installation Netdata via le kickstart officiel ----------------------
if command -v netdata &>/dev/null; then
    log_ok "Netdata deja installe : $(netdata -V 2>&1 | head -1 | awk '{print $2}')"
else
    log_info "Installation de Netdata (stable, sans telemetrie, sans auto-update)..."
    curl -fsSL https://get.netdata.cloud/kickstart.sh > /tmp/netdata-kickstart.sh
    bash /tmp/netdata-kickstart.sh \
        --stable-channel \
        --disable-telemetry \
        --no-updates \
        --dont-wait \
        --non-interactive
    rm -f /tmp/netdata-kickstart.sh
    log_ok "Netdata installe"
fi


# --- 2. Configuration bind loopback strict ---------------------------------
log_info "Configuration de Netdata : bind 127.0.0.1 uniquement..."
mkdir -p /etc/netdata

# Override de la config par defaut
cat > /etc/netdata/netdata.conf <<'EOF'
# LISA - Configuration Netdata (gere par bootstrap)
[global]
    run as user = netdata
    hostname = lisa-vps

[web]
    bind to = 127.0.0.1=dashboard:registry
    allow connections from = localhost 100.* fd7a:* 10.* fc00:*
    allow dashboard from = localhost 100.* fd7a:* 10.* fc00:*
EOF

log_ok "Netdata bind strictement sur 127.0.0.1 (accessible via Tailscale)"


# --- 3. Configuration des alertes Telegram natives -------------------------
log_info "Configuration des alertes Telegram..."

# Le premier user de TELEGRAM_ALLOWED_USER_IDS est utilise comme destinataire
admin_chat_id=$(echo "${TELEGRAM_ALLOWED_USER_IDS}" | cut -d',' -f1)

# Le fichier de config peut etre dans /etc/netdata ou /opt/netdata/etc/netdata
alert_conf_src="/usr/lib/netdata/conf.d/health_alarm_notify.conf"
alert_conf_dst="/etc/netdata/health_alarm_notify.conf"

# Cherche le fichier source dans plusieurs emplacements possibles
for candidate in /usr/lib/netdata/conf.d/health_alarm_notify.conf \
                 /opt/netdata/usr/lib/netdata/conf.d/health_alarm_notify.conf \
                 /etc/netdata/health_alarm_notify.conf.example; do
    if [[ -f "${candidate}" ]]; then
        alert_conf_src="${candidate}"
        break
    fi
done

# Si pas de source, on cree le fichier minimal
if [[ ! -f "${alert_conf_dst}" ]]; then
    if [[ -f "${alert_conf_src}" ]]; then
        cp "${alert_conf_src}" "${alert_conf_dst}"
    else
        touch "${alert_conf_dst}"
    fi
fi

# Active Telegram dans la config (cree les lignes si absentes)
{
    grep -q "^SEND_TELEGRAM=" "${alert_conf_dst}" \
        && sed -i "s|^SEND_TELEGRAM=.*|SEND_TELEGRAM=\"YES\"|" "${alert_conf_dst}" \
        || echo 'SEND_TELEGRAM="YES"' >> "${alert_conf_dst}"

    grep -q "^TELEGRAM_BOT_TOKEN=" "${alert_conf_dst}" \
        && sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=\"${TELEGRAM_BOT_TOKEN}\"|" "${alert_conf_dst}" \
        || echo "TELEGRAM_BOT_TOKEN=\"${TELEGRAM_BOT_TOKEN}\"" >> "${alert_conf_dst}"

    grep -q "^DEFAULT_RECIPIENT_TELEGRAM=" "${alert_conf_dst}" \
        && sed -i "s|^DEFAULT_RECIPIENT_TELEGRAM=.*|DEFAULT_RECIPIENT_TELEGRAM=\"${admin_chat_id}\"|" "${alert_conf_dst}" \
        || echo "DEFAULT_RECIPIENT_TELEGRAM=\"${admin_chat_id}\"" >> "${alert_conf_dst}"
}

# Permissions correctes
chown root:netdata "${alert_conf_dst}" 2>/dev/null || true
chmod 640 "${alert_conf_dst}"

log_ok "Alertes Telegram configurees (chat: ${admin_chat_id})"


# --- 4. Desactivation explicite de Netdata Cloud ---------------------------
mkdir -p /var/lib/netdata/cloud.d
cat > /var/lib/netdata/cloud.d/cloud.conf <<'EOF'
[global]
    enabled = no
EOF
chown -R netdata:netdata /var/lib/netdata/cloud.d 2>/dev/null || true
log_ok "Netdata Cloud desactive (donnees restent on-premise)"


# --- 5. Ouverture du port 19999 via Tailscale uniquement -------------------
log_info "Autorisation du dashboard Netdata via Tailscale..."
ufw allow in on tailscale0 to any port 19999 proto tcp comment 'Netdata dashboard via Tailscale'
ufw reload > /dev/null
log_ok "Dashboard Netdata accessible via http://<tailscale-ip>:19999"


# --- 6. Restart Netdata et verification ------------------------------------
systemctl restart netdata
sleep 2

if systemctl is-active --quiet netdata; then
    log_ok "Netdata actif"
else
    log_warn "Netdata pas demarre, verifie avec : systemctl status netdata"
fi


log_ok "Module 07 termine"
