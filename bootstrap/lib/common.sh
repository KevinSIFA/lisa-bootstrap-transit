#!/usr/bin/env bash
# ============================================================================
# common.sh — Fonctions partagees du bootstrap LISA
# ============================================================================
# Charge automatiquement par bootstrap-lisa.sh
# Toutes les fonctions sont prefixees par leur categorie :
#   log_*       : journalisation
#   require_*   : verifications bloquantes
#   marker_*    : gestion des marqueurs d'idempotence
#   run_*       : execution de commandes
# ============================================================================

# --- Couleurs ANSI ----------------------------------------------------------
readonly C_RESET='\033[0m'
readonly C_RED='\033[0;31m'
readonly C_GREEN='\033[0;32m'
readonly C_YELLOW='\033[0;33m'
readonly C_BLUE='\033[0;34m'
readonly C_BOLD='\033[1m'

# --- Constantes globales ----------------------------------------------------
readonly LOG_DIR="/var/log/lisa"
readonly STATE_DIR="/var/lib/lisa/state"
readonly LOG_FILE="${LOG_DIR}/bootstrap.log"

# Variables peuplees a l'execution
DRY_RUN=${DRY_RUN:-false}
FORCE=${FORCE:-false}


# ============================================================================
# JOURNALISATION
# ============================================================================
# Tous les logs vont a la fois sur stdout (colore) et dans le fichier de log
# (sans couleurs, avec timestamps).

_log() {
    local level="$1"
    local color="$2"
    local msg="$3"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    # Affichage console (avec couleur)
    echo -e "${color}[${level}]${C_RESET} ${msg}" >&2

    # Fichier de log (sans couleurs)
    if [[ -w "${LOG_DIR}" ]] 2>/dev/null || [[ -w "$(dirname "${LOG_FILE}")" ]] 2>/dev/null; then
        echo "${timestamp} [${level}] ${msg}" >> "${LOG_FILE}" 2>/dev/null || true
    fi
}

log_info()    { _log "INFO"  "${C_BLUE}"   "$*"; }
log_ok()      { _log "OK"    "${C_GREEN}"  "$*"; }
log_warn()    { _log "WARN"  "${C_YELLOW}" "$*"; }
log_error()   { _log "ERROR" "${C_RED}"    "$*"; }
log_step()    { _log "STEP"  "${C_BOLD}"   "$*"; }


# ============================================================================
# VERIFICATIONS BLOQUANTES
# ============================================================================

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
        log_error "Verifie ton fichier .env.bootstrap"
        exit 1
    fi
}

require_command() {
    local cmd="$1"
    if ! command -v "${cmd}" &>/dev/null; then
        log_error "Commande requise non trouvee: ${cmd}"
        exit 1
    fi
}


# ============================================================================
# MARQUEURS D'IDEMPOTENCE
# ============================================================================
# Chaque module ecrit un marqueur a la fin de son execution. Le bootstrap
# saute les modules qui ont deja un marqueur valide, sauf si --force.

marker_path() {
    local module="$1"
    echo "${STATE_DIR}/.installed-${module}"
}

marker_exists() {
    local module="$1"
    [[ -f "$(marker_path "${module}")" ]]
}

marker_create() {
    local module="$1"
    local timestamp
    timestamp="$(date -Iseconds)"
    {
        echo "module=${module}"
        echo "completed_at=${timestamp}"
        echo "user=$(whoami)"
    } > "$(marker_path "${module}")"
}

marker_delete() {
    local module="$1"
    rm -f "$(marker_path "${module}")"
}

marker_summary() {
    local module="$1"
    if marker_exists "${module}"; then
        local ts
        ts="$(grep "^completed_at=" "$(marker_path "${module}")" | cut -d= -f2)"
        echo -e "${C_GREEN}[OK]${C_RESET} ${module} (le ${ts})"
    else
        echo -e "${C_YELLOW}[TODO]${C_RESET} ${module}"
    fi
}


# ============================================================================
# EXECUTION DE COMMANDES
# ============================================================================

# run_cmd : execute une commande, log le resultat, respecte --dry-run
run_cmd() {
    local desc="$1"
    shift
    if $DRY_RUN; then
        log_info "[DRY-RUN] ${desc}: $*"
        return 0
    fi
    log_info "${desc}..."
    if "$@" >> "${LOG_FILE}" 2>&1; then
        return 0
    else
        local rc=$?
        log_error "Echec (code ${rc}): ${desc}"
        log_error "Voir le detail dans ${LOG_FILE}"
        return ${rc}
    fi
}

# run_module : execute un module de bootstrap, gere le marqueur
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


# ============================================================================
# INITIALISATION
# ============================================================================

init_directories() {
    mkdir -p "${LOG_DIR}" "${STATE_DIR}"
    chmod 750 "${LOG_DIR}" "${STATE_DIR}"
}

load_env() {
    local env_file="$1"
    if [[ ! -f "${env_file}" ]]; then
        log_error "Fichier d'environnement non trouve: ${env_file}"
        log_error "Copie .env.bootstrap.example en .env.bootstrap et remplis-le."
        exit 1
    fi
    # Charge les variables (set -a exporte automatiquement)
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
}

# --- Trap pour message d'erreur clair ---------------------------------------
trap_error() {
    local rc=$?
    local line=$1
    log_error "Erreur ligne ${line} (code ${rc}). Voir ${LOG_FILE}"
    exit ${rc}
}
