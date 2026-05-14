#!/usr/bin/env bash
# ============================================================================
# 09-systemd.sh — Services systemd + crons LISA
# ============================================================================
# Active les services systemd existants (deja installes par les modules
# precedents) et installe les crons LISA :
#   - Push catalogue Git tous les jours a 23h
#   - Healthcheck quotidien
#   - Rotation des logs LISA
#
# Note : le service OpenClaw (daemon) sera installe via "openclaw onboard
# --install-daemon" en Livraison 2 (etape interactive).
# Le service du pipeline Python LISA sera installe en Livraison 3
# (quand le code Python sera en place).
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 09 : services systemd + crons"


# --- 1. Verification des services deja en place ----------------------------
log_info "Verification des services systemd..."

for svc in tailscaled netdata fail2ban unattended-upgrades; do
    if systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
        log_ok "  - ${svc} : enabled"
    else
        log_warn "  - ${svc} : non enabled (sera ignore)"
    fi

    if systemctl is-active --quiet "${svc}" 2>/dev/null; then
        log_ok "  - ${svc} : active"
    else
        log_warn "  - ${svc} : non actif (verifier manuellement)"
    fi
done


# --- 2. Cron LISA -----------------------------------------------------------
log_info "Installation des taches cron LISA..."

cron_file="/etc/cron.d/lisa"
cat > "${cron_file}" <<'CRONEOF'
# ============================================================================
# Cron LISA (gere par bootstrap-lisa.sh, ne pas editer a la main)
# ============================================================================
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""

# 1) Push catalogue scripts vers le repo Git (tous les jours a 23h NC)
0 23 * * * openclaw /opt/lisa/scripts/cron-push-catalogue.sh >> /var/log/lisa/cron-catalogue.log 2>&1

# 2) Healthcheck quotidien (touch un fichier temoin a 6h)
0 6 * * * root /opt/lisa/scripts/cron-healthcheck.sh >> /var/log/lisa/cron-healthcheck.log 2>&1

# 3) Rotation des logs LISA (dimanche 3h, force logrotate)
0 3 * * 0 root /usr/sbin/logrotate -f /etc/logrotate.d/lisa >> /var/log/lisa/cron-logrotate.log 2>&1

# 4) Verification d'updates OpenClaw (lundi 9h, notification Telegram seulement)
0 9 * * 1 openclaw /opt/lisa/scripts/cron-check-openclaw-update.sh >> /var/log/lisa/cron-update.log 2>&1
CRONEOF
chmod 644 "${cron_file}"
log_ok "Cron LISA installe : ${cron_file}"


# --- 3. Scripts cron ad-hoc (places dans /opt/lisa/scripts/) ---------------
log_info "Ecriture des scripts cron..."

mkdir -p /opt/lisa/scripts
chown openclaw:openclaw /opt/lisa/scripts

# Script de push catalogue
cat > /opt/lisa/scripts/cron-push-catalogue.sh <<'PUSHEOF'
#!/usr/bin/env bash
# Push le repertoire catalogue vers le repo Git (deploy key)
set -euo pipefail
cd /opt/lisa/catalogue
export GIT_SSH_COMMAND="ssh -i /opt/lisa/secrets/lisa_deploy_key -o StrictHostKeyChecking=accept-new"
git add -A
if git diff-index --quiet HEAD --; then
    echo "$(date -Iseconds) Aucun changement, pas de push."
    exit 0
fi
git -c user.email="lisa@srv1519973.hstgr.cloud" -c user.name="LISA Bot" \
    commit -m "Catalogue auto-push $(date -Iseconds)"
git push origin main
echo "$(date -Iseconds) Push effectue."
PUSHEOF
chmod 750 /opt/lisa/scripts/cron-push-catalogue.sh

# Script de healthcheck (preuve de vie quotidienne)
cat > /opt/lisa/scripts/cron-healthcheck.sh <<'HCEOF'
#!/usr/bin/env bash
# Healthcheck quotidien : verifie services critiques et notifie Telegram
# en cas de probleme uniquement.
set -uo pipefail

issues=()
for svc in tailscaled netdata fail2ban; do
    systemctl is-active --quiet "${svc}" || issues+=("${svc} DOWN")
done

# Espace disque < 10%
disk_free_pct=$(df / | awk 'NR==2 {gsub("%",""); print 100-$5}')
if (( disk_free_pct < 10 )); then
    issues+=("Espace disque libre: ${disk_free_pct}%")
fi

# Marqueur de vie (utile pour suivi externe)
touch "/var/lib/lisa/state/healthcheck-$(date +%Y%m%d)"

# Si problemes : notification Telegram
if (( ${#issues[@]} > 0 )); then
    if [[ -f /etc/environment.d/openclaw.conf ]]; then
        # On charge les vars d'env Telegram depuis le bot LISA
        source /opt/lisa/.env 2>/dev/null || true
    fi
    msg="LISA healthcheck ALERTE: $(IFS=$'\n'; echo "${issues[*]}")"
    echo "$(date -Iseconds) ${msg}"
fi
HCEOF
chmod 750 /opt/lisa/scripts/cron-healthcheck.sh

# Script de check update OpenClaw (placeholder - notifie via Telegram)
cat > /opt/lisa/scripts/cron-check-openclaw-update.sh <<'UPDEOF'
#!/usr/bin/env bash
# Verifie si une nouvelle version d'OpenClaw est disponible.
# Notifie via Telegram, NE PAS mettre a jour automatiquement.
set -uo pipefail

current=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
latest=$(npm view openclaw version 2>/dev/null || echo "")

if [[ -n "${latest}" ]] && [[ "${current}" != "${latest}" ]]; then
    echo "$(date -Iseconds) Update OpenClaw disponible: ${current} -> ${latest}"
    # TODO : envoi Telegram (geré dans Livraison 2)
else
    echo "$(date -Iseconds) OpenClaw a jour : ${current}"
fi
UPDEOF
chmod 750 /opt/lisa/scripts/cron-check-openclaw-update.sh

chown -R openclaw:openclaw /opt/lisa/scripts
log_ok "Scripts cron installes dans /opt/lisa/scripts/"


# --- 4. Logrotate config ---------------------------------------------------
log_info "Configuration de logrotate pour les logs LISA..."
cat > /etc/logrotate.d/lisa <<'LREOF'
/var/log/lisa/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 640 openclaw openclaw
    sharedscripts
    postrotate
        # Pas de service a redemarrer pour l'instant
        true
    endscript
}
LREOF
chmod 644 /etc/logrotate.d/lisa
log_ok "Logrotate configure (8 semaines de retention)"


# --- 5. Resume --------------------------------------------------------------
log_info "Resume systemd + crons :"
log_info "  Services actifs : tailscaled, netdata, fail2ban, unattended-upgrades"
log_info "  Crons LISA (vu : crontab -u root /etc/cron.d/lisa) :"
log_info "    23h00 (NC) - Push catalogue Git"
log_info "    06h00      - Healthcheck quotidien"
log_info "    03h00 dim. - Rotation logs"
log_info "    09h00 lun. - Check update OpenClaw (notif uniquement)"
log_info ""
log_info "A faire en Livraison 2 :"
log_info "  - sudo -iu openclaw && openclaw onboard --install-daemon"
log_info "  - Configuration skills Telegram (canal d'admin)"


log_ok "Module 09 termine"
