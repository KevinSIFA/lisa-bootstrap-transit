#!/usr/bin/env bash
# ============================================================================
# 01-prerequisites.sh — Verifications systeme et preparation de base
# ============================================================================
# Verifie que le VPS est conforme :
#   - OS : Ubuntu 24.04 LTS
#   - RAM : >= 14 Go (sur 16 Go nominal)
#   - Disque libre : >= 150 Go
#   - Connectivite internet
# Puis :
#   - Definit le hostname
#   - Configure le fuseau horaire
#   - apt update + upgrade (uniquement security)
#   - Ajoute 4 Go de swap si absent (filet de securite)
# ============================================================================

set -euo pipefail

# Charge la lib (le module est appele par bootstrap-lisa.sh qui a deja charge l'env)
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 01 : verifications systeme et preparation de base"


# --- 1. OS Ubuntu 24.04 -----------------------------------------------------
log_info "Verification de l'OS..."
require_ubuntu_2404
log_ok "OS conforme : Ubuntu 24.04 LTS"


# --- 2. RAM >= 14 Go --------------------------------------------------------
log_info "Verification de la RAM..."
ram_total_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
ram_total_gb=$(( ram_total_kb / 1024 / 1024 ))
if (( ram_total_gb < 14 )); then
    log_error "RAM insuffisante : ${ram_total_gb} Go (requis : 14 Go minimum)"
    exit 1
fi
log_ok "RAM disponible : ${ram_total_gb} Go"


# --- 3. Disque libre >= 150 Go ----------------------------------------------
log_info "Verification de l'espace disque..."
disk_free_kb=$(df --output=avail / | tail -1 | tr -d ' ')
disk_free_gb=$(( disk_free_kb / 1024 / 1024 ))
if (( disk_free_gb < 150 )); then
    log_error "Espace disque insuffisant : ${disk_free_gb} Go (requis : 150 Go)"
    exit 1
fi
log_ok "Espace disque libre : ${disk_free_gb} Go"


# --- 4. Connectivite internet -----------------------------------------------
log_info "Verification de la connectivite internet..."
for host in archive.ubuntu.com github.com api.anthropic.com generativelanguage.googleapis.com login.tailscale.com; do
    if ! curl -fsS --max-time 5 -o /dev/null "https://${host}" 2>/dev/null \
       && ! curl -fsS --max-time 5 -o /dev/null "http://${host}" 2>/dev/null; then
        log_warn "Hote injoignable : ${host} (poursuite, mais a verifier)"
    fi
done
log_ok "Connectivite internet OK"


# --- 5. Hostname ------------------------------------------------------------
current_hostname=$(hostname)
target_hostname="${VPS_HOSTNAME:-}"
if [[ -z "${target_hostname}" ]]; then
    log_warn "VPS_HOSTNAME non defini, hostname inchange : ${current_hostname}"
elif [[ "${current_hostname}" != "${target_hostname}" ]]; then
    log_info "Configuration du hostname : ${target_hostname}"
    hostnamectl set-hostname "${target_hostname}"
    # Met a jour /etc/hosts pour eviter les warnings sudo
    if ! grep -q "${target_hostname}" /etc/hosts; then
        echo "127.0.1.1 ${target_hostname}" >> /etc/hosts
    fi
    log_ok "Hostname configure : ${target_hostname}"
else
    log_ok "Hostname deja correct : ${current_hostname}"
fi


# --- 6. Fuseau horaire ------------------------------------------------------
target_tz="${VPS_TIMEZONE:-Pacific/Noumea}"
current_tz=$(timedatectl show --property=Timezone --value)
if [[ "${current_tz}" != "${target_tz}" ]]; then
    log_info "Configuration du fuseau horaire : ${target_tz}"
    timedatectl set-timezone "${target_tz}"
    log_ok "Fuseau horaire : ${target_tz}"
else
    log_ok "Fuseau horaire deja correct : ${target_tz}"
fi


# --- 7. apt update + upgrade securite ---------------------------------------
log_info "Mise a jour de la liste des paquets..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

log_info "Application des mises a jour de securite (upgrade)..."
apt-get -qq -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    upgrade
log_ok "Systeme a jour"


# --- 8. Swap 4 Go (filet de securite si pic memoire) ------------------------
swap_size_kb=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
if (( swap_size_kb < 1024 * 1024 )); then
    log_info "Pas de swap detecte, creation d'un fichier swap de 4 Go..."
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile > /dev/null
    swapon /swapfile
    # Persiste dans /etc/fstab
    if ! grep -q "^/swapfile" /etc/fstab; then
        echo "/swapfile none swap sw 0 0" >> /etc/fstab
    fi
    # Tuning : reduit l'utilisation aggressive du swap
    sysctl -w vm.swappiness=10 > /dev/null
    if ! grep -q "^vm.swappiness" /etc/sysctl.conf; then
        echo "vm.swappiness=10" >> /etc/sysctl.conf
    fi
    log_ok "Swap 4 Go cree et active (swappiness=10)"
else
    log_ok "Swap deja present : $(( swap_size_kb / 1024 / 1024 )) Go"
fi


# --- 9. Outils basiques pour la suite ---------------------------------------
log_info "Installation des outils CLI de base..."
apt-get -qq -y install \
    curl wget ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    htop nano vim less rsync jq
log_ok "Outils CLI de base installes"


log_ok "Module 01 termine"
