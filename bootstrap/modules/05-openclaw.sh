#!/usr/bin/env bash
# ============================================================================
# 05-openclaw.sh — Installation de OpenClaw (agent orchestrateur)
# ============================================================================
# - Installe Node.js LTS (>=22) via NodeSource
# - Installe OpenClaw globalement via npm
# - Verifie que la version est >= MIN_VERSION (securite)
# - Prepare l'arborescence de config ~/.openclaw pour l'utilisateur openclaw
# - Pre-positionne un fichier config.json minimal avec gateway sur loopback
#
# NOTE : La configuration complete (skills, SOUL.md, comms Telegram...) sera
# realisee dans la Livraison 2. Ici, on installe et on securise le bind.
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 05 : installation OpenClaw"

export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock

# Version minimale exigee par la spec (CVE-2026-45006)
readonly OPENCLAW_MIN_VERSION="2026.4.23"


# --- 1. Installation Node.js LTS (24.x) via NodeSource ---------------------
if command -v node &>/dev/null && [[ "$(node -v)" =~ ^v(2[2-9]|[3-9][0-9])\. ]]; then
    log_ok "Node.js deja present : $(node -v)"
else
    log_info "Installation de Node.js 24 LTS via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
    wait_for_apt_lock
    apt-get -qq -y install nodejs
    log_ok "Node.js installe : $(node -v)"
fi


# --- 2. Installation OpenClaw via npm global -------------------------------
if command -v openclaw &>/dev/null; then
    installed_version=$(openclaw --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [[ -n "${installed_version}" ]]; then
        log_ok "OpenClaw deja installe : v${installed_version}"
    else
        log_warn "OpenClaw present mais version non lisible, reinstallation..."
        npm install -g openclaw
    fi
else
    log_info "Installation de OpenClaw via npm (peut prendre 1-2 min)..."
    npm install -g openclaw
    log_ok "OpenClaw installe"
fi


# --- 3. Verification de la version -----------------------------------------
installed_version=$(openclaw --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [[ -z "${installed_version}" ]]; then
    log_error "Impossible de lire la version d'OpenClaw. Verifie l'install."
    exit 1
fi
log_info "Version OpenClaw installee : ${installed_version}"
log_info "Version minimale requise   : ${OPENCLAW_MIN_VERSION}"

# Comparaison version sous forme YYYY.M.D ou X.Y.Z
# On fait une comparaison naturelle sort -V
if [[ "$(printf '%s\n%s\n' "${OPENCLAW_MIN_VERSION}" "${installed_version}" | sort -V | head -1)" == "${OPENCLAW_MIN_VERSION}" ]]; then
    log_ok "Version OpenClaw conforme (>= ${OPENCLAW_MIN_VERSION})"
else
    log_warn "Version OpenClaw (${installed_version}) potentiellement < ${OPENCLAW_MIN_VERSION}"
    log_warn "Verifie manuellement les CVE applicables. On poursuit."
fi


# --- 4. Preparation de l'arborescence ~/.openclaw --------------------------
OPENCLAW_HOME="/home/openclaw/.openclaw"
log_info "Preparation de ${OPENCLAW_HOME}..."
mkdir -p "${OPENCLAW_HOME}"/{agents,skills,state,logs}
chown -R openclaw:openclaw "${OPENCLAW_HOME}"
chmod 750 "${OPENCLAW_HOME}"
log_ok "Arborescence ${OPENCLAW_HOME} prete"


# --- 5. Config.json minimal avec gateway loopback strict -------------------
# La spec impose 127.0.0.1 strict (CVE-2026-25253 ClawBleed).
config_file="${OPENCLAW_HOME}/config.json"
if [[ -f "${config_file}" ]]; then
    log_ok "config.json deja present (non ecrase pour preserver l'existant)"
else
    log_info "Ecriture d'un config.json minimal (gateway loopback strict)..."
    cat > "${config_file}" <<'CONFEOF'
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 18789,
    "controlUi": {
      "enabled": true
    }
  },
  "comments": {
    "host": "Bind loopback strict obligatoire (CVE-2026-25253)",
    "note": "Configuration enrichie en Livraison 2 (skills, Telegram, etc.)"
  }
}
CONFEOF
    chown openclaw:openclaw "${config_file}"
    chmod 640 "${config_file}"
    log_ok "config.json ecrit avec gateway 127.0.0.1:18789"
fi


# --- 6. Variables d'environnement systeme pour OpenClaw --------------------
env_file="/etc/environment.d/openclaw.conf"
mkdir -p /etc/environment.d
cat > "${env_file}" <<EOF
OPENCLAW_HOME=${OPENCLAW_HOME}
OPENCLAW_STATE_DIR=${OPENCLAW_HOME}/state
OPENCLAW_CONFIG_PATH=${OPENCLAW_HOME}/config.json
EOF
log_ok "Variables d'environnement OpenClaw definies"


# --- 7. Resume --------------------------------------------------------------
log_info "OpenClaw installe et pre-configure."
log_info "Prochaine etape (Livraison 2) : ouvrir un shell openclaw et lancer"
log_info "    sudo -iu openclaw"
log_info "    openclaw onboard --install-daemon"
log_info "pour finaliser auth/Telegram/skills."


log_ok "Module 05 termine"
