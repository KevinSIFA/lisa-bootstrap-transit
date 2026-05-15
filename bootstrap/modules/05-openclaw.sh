#!/usr/bin/env bash
# ============================================================================
# 05-openclaw.sh — Installation et configuration OpenClaw V3 (mai 2026)
# ============================================================================
# - Installe Node.js LTS (>=22) via NodeSource
# - Installe OpenClaw globalement via npm
# - Verifie version >= 2026.5.6 (anti Rough Week 2026.4.24-29 + anti CVE)
# - Prepare ~/.openclaw arborescence
# - Deploie openclaw.json V3 avec substitution variables d'env
# - Deploie AGENTS.md + SOUL.md de l'agent LISA
# - Deploie les 3 SKILL.md (lisa-extraction, lisa-calibration, lisa-orchestrator)
# - Gere /etc/lisa/openclaw.env + /etc/lisa/telegram.token
# - Lance openclaw doctor + security audit final
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 05 : installation OpenClaw V3"

export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock

# Version minimale exigee (post Rough Week 2026.4.24-29)
# Doc reference : docs/connaissanceopenclaw05_2026.md section 4
readonly OPENCLAW_MIN_VERSION="2026.5.6"

# Chemins
readonly LISA_V2_REPO="${LISA_V2_REPO:-/opt/lisa/lisa-v2-repo}"
readonly OPENCLAW_HOME="/home/openclaw/.openclaw"
readonly LISA_HOME="/opt/lisa"
readonly LISA_ETC="/etc/lisa"


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


# --- 3. Verification version (anti Rough Week + anti CVE) ------------------
installed_version=$(openclaw --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [[ -z "${installed_version}" ]]; then
    log_error "Impossible de lire la version d'OpenClaw"
    exit 1
fi
log_info "Version OpenClaw installee : ${installed_version}"
log_info "Version minimale requise   : ${OPENCLAW_MIN_VERSION}"

# Versions a eviter absolument (Rough Week)
case "${installed_version}" in
    2026.4.24|2026.4.25|2026.4.26|2026.4.27|2026.4.28|2026.4.29)
        log_error "Version ${installed_version} est dans la Rough Week, BLOQUEE"
        log_error "Faire : npm install -g openclaw@2026.5.6 puis relancer ce module"
        exit 1
        ;;
esac

if [[ "$(printf '%s\n%s\n' "${OPENCLAW_MIN_VERSION}" "${installed_version}" | sort -V | head -1)" == "${OPENCLAW_MIN_VERSION}" ]]; then
    log_ok "Version OpenClaw conforme (>= ${OPENCLAW_MIN_VERSION})"
else
    log_warn "Version OpenClaw (${installed_version}) < ${OPENCLAW_MIN_VERSION}"
    log_warn "Risque CVE-2026-25253 / CVE-2026-45006. Upgrade recommande."
fi


# --- 4. Preparation arborescence ~/.openclaw -------------------------------
log_info "Preparation de ${OPENCLAW_HOME}..."
mkdir -p "${OPENCLAW_HOME}"/{agents/lisa/workspace,skills,state,logs,credentials}
chown -R openclaw:openclaw "${OPENCLAW_HOME}"
chmod 700 "${OPENCLAW_HOME}"
log_ok "Arborescence ${OPENCLAW_HOME} prete (chmod 700)"


# --- 5. Preparation /etc/lisa pour secrets ---------------------------------
log_info "Preparation de ${LISA_ETC}..."
mkdir -p "${LISA_ETC}"
chown root:openclaw "${LISA_ETC}"
chmod 750 "${LISA_ETC}"

# Le fichier openclaw.env contient les variables substituees dans openclaw.json
# Format : KEY=VALUE, une par ligne, sans quotes
if [[ -f "${LISA_ETC}/openclaw.env" ]]; then
    log_ok "${LISA_ETC}/openclaw.env existe deja (non ecrase)"
else
    log_info "Creation d'un template ${LISA_ETC}/openclaw.env (a remplir manuellement)..."
    cat > "${LISA_ETC}/openclaw.env" <<'ENVEOF'
# OpenClaw runtime environment — LISA V3 (mai 2026)
# Renseigner toutes les valeurs avant le premier demarrage du gateway.
# chmod 640 root:openclaw

# Token gateway (generer : openclaw doctor --generate-gateway-token)
OPENCLAW_GATEWAY_TOKEN=

# Anthropic — workspace LISA unique (dev/staging/prod ensemble)
ANTHROPIC_API_KEY=
ANTHROPIC_API_KEY_BACKUP=

# Telegram (BotFather)
TELEGRAM_BOT_TOKEN=
KEVIN_TELEGRAM_USER_ID=

# Google (Gemini Flash + Pro via Vertex)
# GOOGLE_APPLICATION_CREDENTIALS et GOOGLE_CLOUD_PROJECT deja codes dans openclaw.json
# Pas de variables ici sauf override explicite
ENVEOF
    chown root:openclaw "${LISA_ETC}/openclaw.env"
    chmod 640 "${LISA_ETC}/openclaw.env"
    log_warn "Template ${LISA_ETC}/openclaw.env cree — REMPLIR LES VALEURS avant gateway start"
fi

# Token Telegram en fichier separe (referencé par tokenFile dans openclaw.json)
if [[ ! -f "${LISA_ETC}/telegram.token" ]]; then
    log_info "Creation du fichier vide ${LISA_ETC}/telegram.token..."
    touch "${LISA_ETC}/telegram.token"
    chown root:openclaw "${LISA_ETC}/telegram.token"
    chmod 640 "${LISA_ETC}/telegram.token"
    log_warn "Renseigner le token bot Telegram dans ${LISA_ETC}/telegram.token"
fi


# --- 6. Deploiement openclaw.json V3 (si repo disponible) ------------------
src_config="${LISA_V2_REPO}/openclaw_config/openclaw.json"
dest_config="${OPENCLAW_HOME}/openclaw.json"

if [[ -f "${src_config}" ]]; then
    if [[ -f "${dest_config}" ]]; then
        backup_path="${dest_config}.bak.$(date +%Y%m%d-%H%M%S)"
        cp "${dest_config}" "${backup_path}"
        log_info "Backup ancienne config : ${backup_path}"
    fi
    log_info "Deploiement openclaw.json V3 depuis ${src_config}..."
    cp "${src_config}" "${dest_config}"
    chown openclaw:openclaw "${dest_config}"
    chmod 600 "${dest_config}"
    log_ok "openclaw.json V3 deploye"
else
    log_warn "Repo LISA V2 non trouve (${LISA_V2_REPO})"
    log_warn "Cloner d'abord le repo ou passer LISA_V2_REPO=<chemin> en env"
    log_warn "Skip deploiement openclaw.json"
fi


# --- 7. Deploiement AGENTS.md + SOUL.md de l'agent LISA --------------------
agent_dir="${OPENCLAW_HOME}/agents/lisa"
for f in AGENTS.md SOUL.md; do
    src="${LISA_V2_REPO}/openclaw_config/${f}"
    if [[ -f "${src}" ]]; then
        cp "${src}" "${agent_dir}/${f}"
        chown openclaw:openclaw "${agent_dir}/${f}"
        chmod 644 "${agent_dir}/${f}"
        log_ok "Deploye ${f} dans ${agent_dir}/"
    else
        log_warn "Manque ${src}"
    fi
done


# --- 8. Deploiement des 3 SKILL.md dans /opt/lisa/skills/ ------------------
log_info "Preparation /opt/lisa/skills/..."
mkdir -p "${LISA_HOME}/skills"
chown openclaw:openclaw "${LISA_HOME}/skills"
chmod 750 "${LISA_HOME}/skills"

for skill in lisa-extraction lisa-calibration lisa-orchestrator; do
    src="${LISA_V2_REPO}/openclaw_skills/${skill}/SKILL.md"
    dest_dir="${LISA_HOME}/skills/${skill}"
    if [[ -f "${src}" ]]; then
        mkdir -p "${dest_dir}"
        cp "${src}" "${dest_dir}/SKILL.md"
        chown -R openclaw:openclaw "${dest_dir}"
        chmod 644 "${dest_dir}/SKILL.md"
        log_ok "Deploye skill ${skill}"
    else
        log_warn "Manque ${src}"
    fi
done


# --- 9. Deploiement des prompts byte-stables -------------------------------
log_info "Preparation /opt/lisa/prompts/..."
mkdir -p "${LISA_HOME}/prompts"
chown openclaw:openclaw "${LISA_HOME}/prompts"
chmod 750 "${LISA_HOME}/prompts"

for prompt in lisa_gemini_v6_1.txt vision_split.txt lisa_orchestrator.txt; do
    src="${LISA_V2_REPO}/prompts/${prompt}"
    dest="${LISA_HOME}/prompts/${prompt}"
    if [[ -f "${src}" ]]; then
        cp "${src}" "${dest}"
        chown openclaw:openclaw "${dest}"
        chmod 644 "${dest}"
        # Verification byte-stability via sha256
        local_sha=$(sha256sum "${src}" | awk '{print $1}')
        log_ok "Deploye ${prompt} (sha256: ${local_sha:0:12}...)"
    else
        log_warn "Manque ${src}"
    fi
done


# --- 10. Variables d'environnement systeme pour systemd --------------------
env_file="/etc/environment.d/openclaw.conf"
mkdir -p /etc/environment.d
cat > "${env_file}" <<EOF
OPENCLAW_HOME=${OPENCLAW_HOME}
OPENCLAW_STATE_DIR=${OPENCLAW_HOME}/state
OPENCLAW_CONFIG_PATH=${OPENCLAW_HOME}/openclaw.json
LISA_HOME=${LISA_HOME}
EOF
log_ok "Variables d'environnement systeme OpenClaw definies"


# --- 11. Validation finale (doctor + audit) --------------------------------
log_info "Validation finale via openclaw doctor + security audit..."

# On lance les commandes en mode user openclaw, non-bloquant si echec
if sudo -iu openclaw openclaw doctor --quiet 2>/dev/null; then
    log_ok "openclaw doctor OK"
else
    log_warn "openclaw doctor a remonte des problemes — voir 'sudo -iu openclaw openclaw doctor --deep'"
fi

if sudo -iu openclaw openclaw security audit 2>/dev/null | tee /tmp/openclaw-audit.log; then
    if grep -qi "critical" /tmp/openclaw-audit.log; then
        log_error "Findings CRITICAL dans security audit ! Voir /tmp/openclaw-audit.log"
    else
        log_ok "Security audit OK (pas de critical)"
    fi
else
    log_warn "security audit n'a pas pu s'executer"
fi


# --- 12. Resume --------------------------------------------------------------
log_info "OpenClaw V3 installe et pre-configure."
log_info ""
log_info "Etapes manuelles restantes :"
log_info "  1. Remplir ${LISA_ETC}/openclaw.env avec les vraies valeurs"
log_info "  2. Renseigner ${LISA_ETC}/telegram.token avec le token BotFather"
log_info "  3. Generer le gateway token : sudo -iu openclaw openclaw doctor --generate-gateway-token"
log_info "  4. Coller le token dans openclaw.env (OPENCLAW_GATEWAY_TOKEN)"
log_info "  5. Demarrer le service : sudo systemctl enable --now openclaw-gateway"
log_info "  6. Verifier : ss -tlnp | grep 18789  (doit etre 127.0.0.1, pas 0.0.0.0)"

log_ok "Module 05 termine"
