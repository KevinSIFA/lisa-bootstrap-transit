#!/usr/bin/env bash
# ============================================================================
# 06-network.sh — Reseau : Tailscale + restriction SSH a Tailscale uniquement
# ============================================================================
# - Installe Tailscale via le script officiel
# - Joint le tailnet via TAILSCALE_AUTH_KEY (pre-approuve, non ephemere)
# - Restreint le pare-feu : SSH (port 22) uniquement via interface tailscale0
# - Verifie que le VPS est joignable via Tailscale
#
# ATTENTION : ce module ferme l'acces SSH public (port 22 internet) une fois
# que Tailscale est UP. Apres ce module, tu te connectes au VPS via son IP
# Tailscale (100.x.x.x), plus jamais via l'IP publique 187.127.107.127.
# Le terminal web Hostinger reste disponible en backup.
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 06 : reseau (Tailscale + UFW restriction SSH)"

require_var TAILSCALE_AUTH_KEY
export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock


# --- 1. Installation de Tailscale ------------------------------------------
if command -v tailscale &>/dev/null; then
    log_ok "Tailscale deja installe : $(tailscale version | head -1)"
else
    log_info "Installation de Tailscale via le script officiel..."
    curl -fsSL https://tailscale.com/install.sh | sh
    log_ok "Tailscale installe : $(tailscale version | head -1)"
fi


# --- 2. Demarrage et auth via auth key -------------------------------------
ts_hostname="${VPS_HOSTNAME:-$(hostname)}"
# Tailscale a horreur des points dans les hostnames, on simplifie
ts_machine_name=$(echo "${ts_hostname}" | cut -d. -f1)

log_info "Connexion au tailnet (machine : ${ts_machine_name})..."
tailscale up \
    --auth-key="${TAILSCALE_AUTH_KEY}" \
    --hostname="${ts_machine_name}" \
    --accept-routes \
    --ssh=false

# Recupere l'IP Tailscale
ts_ip=$(tailscale ip -4 | head -1)
if [[ -z "${ts_ip}" ]]; then
    log_error "Impossible d'obtenir une IP Tailscale. Connexion echouee."
    exit 1
fi
log_ok "VPS joint au tailnet, IP Tailscale : ${ts_ip}"


# --- 3. Restriction du pare-feu UFW : SSH via Tailscale uniquement ---------
log_info "Restriction du pare-feu : SSH uniquement via interface tailscale0..."

# On supprime la regle SSH ouverte au monde
ufw --force delete allow 22/tcp 2>/dev/null || true
ufw --force delete allow ssh 2>/dev/null || true

# Et on autorise SSH uniquement depuis l'interface Tailscale
ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH via Tailscale uniquement'

# On bloque explicitement le port 22 sur les autres interfaces (defense en profondeur)
ufw deny 22/tcp comment 'Bloque SSH public (defense en profondeur)'

ufw reload > /dev/null
log_ok "UFW : SSH desormais accessible uniquement via Tailscale"


# --- 4. Activation du service Tailscale au boot ----------------------------
systemctl enable tailscaled > /dev/null 2>&1 || true
log_ok "Service tailscaled active au boot"


# --- 5. Permissions des secrets Google service account + GitHub deploy key -
# Le Module 04 a deja chmod 600 les secrets. On revalide ici par defense
# en profondeur, et on s'assure que openclaw peut les lire.
if [[ -f /opt/lisa/secrets/lisa-service-account.json ]]; then
    chown openclaw:openclaw /opt/lisa/secrets/lisa-service-account.json
    chmod 600 /opt/lisa/secrets/lisa-service-account.json
    log_ok "Permissions service account Google securisees"
fi
if [[ -f /opt/lisa/secrets/lisa_deploy_key ]]; then
    chown openclaw:openclaw /opt/lisa/secrets/lisa_deploy_key
    chmod 600 /opt/lisa/secrets/lisa_deploy_key
    log_ok "Permissions deploy key GitHub securisees"
fi
chmod 700 /opt/lisa/secrets 2>/dev/null || true
chown openclaw:openclaw /opt/lisa/secrets 2>/dev/null || true


# --- 6. Resume --------------------------------------------------------------
log_info "Resume reseau :"
log_info "  IP publique         : ${VPS_IP:-187.127.107.127} (SSH FERME desormais)"
log_info "  IP Tailscale        : ${ts_ip}                  (SSH ACCESSIBLE)"
log_info "  Hostname Tailscale  : ${ts_machine_name}"
log_info ""
log_info "Pour te connecter depuis ton PC (Tailscale doit etre UP sur le PC) :"
log_info "    ssh root@${ts_ip}"
log_info "    ssh root@${ts_machine_name}"
log_info ""
log_info "Si tu perds l'acces SSH : utilise le terminal web Hostinger en backup."


log_ok "Module 06 termine"
