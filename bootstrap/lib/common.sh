#!/usr/bin/env bash
# ============================================================================
# common.sh — Fonctions partagees du bootstrap LISA
# ============================================================================

readonly C_RESET='\033[0m'
readonly C_RED='\033[0;31m'
readonly C_GREEN='\033[0;32m'
readonly C_YELLOW='\033[0;33m'
readonly C_BLUE='\033[0;34m'
readonly C_BOLD='\033[1m'

readonly LOG_DIR="/var/log/lisa"
readonly STATE_DIR="/var/lib/lisa/state"
readonly LOG_FILE="${LOG_DIR}/bootstrap.log"

DRY_RUN=${DRY_RUN:-false}
FORCE=${FORCE:-false}

# --- JOURNALISATION ---------------------------------------------------------
_log() {
    local level="$1"
    local color="$2"
    local msg="$3"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${color}[${level}]${C_RESET} ${msg}" >&2
    if [[ -w "${LOG_DIR}" ]] 2>/dev/null || [[ -w "$(dirname "${LOG_FILE}")" ]] 2>/dev/null; then
        echo "${timestamp} [${level}] ${msg}" >> "${LOG_FILE}" 2>/dev/null || true
    fi
}
log_info()  { _log "INFO"  "${C_BLUE}"   "$*"; }
log_ok()    { _log "OK"    "${C_GREEN}"  "$*"; }
log_warn()  { _log "WARN"  "${C_YELLOW}" "$*"; }
log_error() { _log "ERROR" "${C_RED}"    "$*"; }
log_step()  { _log "STEP"  "${C_BOLD}"   "$*"; }

# --- VERIFICATIONS BLOQUANTES -----------------------------------------------
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Ce script doit etre execute en root (utilise sudo)."
        exit 1
    fi
}

require_ubuntu_2404() {
    if [[ ! -f /etc/os-release ]]; then
        log_error "Impossible de lire /etc/os-release."
        exit 1
    fi
    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "${ID:-}" != "ubuntu" ]] || [[ "${VERSION_ID:-}" != "24.04" ]]; then
        log_error "OS non supporte: ${PRETTY_NAME:-inconnu}. Requis: Ubuntu 24.04 LTS."
        exit 1
    fi
}

require_var() {
    local varname="$1"
    if [[ -z "${!varname:-}" ]]; then
        log_error "Variable d'environnement requise non definie: ${varname}"
        exit 1
    fi
}

# --- MARQUEURS D'IDEMPOTENCE -----------------------------------------------
marker_path()   { echo "${STATE_DIR}/.installed-$1"; }
marker_exists() { [[ -f "$(marker_path "$1")" ]]; }
marker_create() {
    local module="$1"
    {
        echo "module=${module}"
        echo "completed_at=$(date -Iseconds)"
        echo "user=$(whoami)"
    } > "$(marker_path "${module}")"
}
marker_delete() { rm -f "$(marker_path "$1")"; }
marker_summary() {
    local module="$1"
    if marker_exists "${module}"; then
        local ts
        ts="$(grep '^completed_at=' "$(marker_path "${module}")" | cut -d= -f2)"
        echo -e "${C_GREEN}[OK]${C_RESET} ${module} (le ${ts})"
    else
        echo -e "${C_YELLOW}[TODO]${C_RESET} ${module}"
    fi
}

# --- GESTION DU VERROU APT/DPKG ---------------------------------------------
# On verifie uniquement les fichiers de verrou (source de verite).
# On NE matche PAS le processus "unattended-upgr" car le service
# unattended-upgrades-shutdown reste en attente de signal en permanence.
wait_for_apt_lock() {
    local max_wait=600
    local waited=0
    local interval=3
    while fuser /var/lib/dpkg/lock-frontend &>/dev/null \
       || fuser /var/lib/dpkg/lock &>/dev/null \
       || fuser /var/lib/apt/lists/lock &>/dev/null; do
        if (( waited >= max_wait )); then
            log_error "Verrou apt/dpkg toujours detenu apres ${max_wait}s. Abandon."
            return 1
        fi
        if (( waited % 30 == 0 )); then
            log_info "Attente du verrou apt/dpkg (${waited}s/${max_wait}s)..."
        fi
        sleep ${interval}
        waited=$((waited + interval))
    done
}

# --- EXECUTION DE MODULES ---------------------------------------------------
run_module() {
    local module="$1"
    local module_path="${MODULES_DIR}/${module}.sh"
    if [[ ! -f "${module_path}" ]]; then
        log_warn "Module non trouve, ignore: ${module} (sera livre plus tard)"
        return 0
    fi
    if marker_exists "${module}" && ! $FORCE; then
        log_info "Module ${module} deja execute (skip). Utilise --force pour rejouer."
        return 0
    fi
    log_step "=== Demarrage module: ${module} ==="
    if $DRY_RUN; then
        log_info "[DRY-RUN] bash ${module_path}"
        return 0
    fi
    if bash "${module_path}"; then
        marker_create "${module}"
        log_ok "Module ${module} termine avec succes."
    else
        log_error "Module ${module} a echoue. Corrige et rejoue le bootstrap."
        exit 1
    fi
}

# --- INITIALISATION ---------------------------------------------------------
init_directories() {
    mkdir -p "${LOG_DIR}" "${STATE_DIR}"
    chmod 750 "${LOG_DIR}" "${STATE_DIR}"
}

load_env() {
    local env_file="$1"
    if [[ ! -f "${env_file}" ]]; then
        log_error "Fichier d'environnement non trouve: ${env_file}"
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
}

trap_error() {
    local rc=$?
    local line=$1
    log_error "Erreur ligne ${line} (code ${rc}). Voir ${LOG_FILE}"
    exit ${rc}
}
