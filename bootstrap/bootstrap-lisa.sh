#!/usr/bin/env bash
# ============================================================================
# bootstrap-lisa.sh — Bootstrap orchestrateur du serveur LISA
# ============================================================================
# Prepare un VPS Ubuntu 24.04 vierge pour heberger l'agent LISA :
#   - Securite du systeme (UFW, fail2ban, ssh hardening)
#   - Outils OS (Tesseract, OpenCV, exiftool, qpdf, sqlite-vec)
#   - Stack Python (PyMuPDF, pandas, anthropic, etc.)
#   - Agent OpenClaw (>= 2026.4.23, gateway loopback strict)
#   - Reseau (Tailscale pour admin SSH)
#   - Monitoring (Netdata bind loopback)
#   - Espace de travail (arborescence, grimoire sqlite-vec)
#   - Services systemd et crons
#
# IDEMPOTENT : peut etre rejoue, saute les modules deja executes (marqueurs).
#
# Usage :
#   sudo ./bootstrap-lisa.sh [OPTIONS]
#
# Voir --help pour le detail des options.
# ============================================================================

set -euo pipefail

# Repertoires
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly LIB_DIR="${SCRIPT_DIR}/lib"
readonly MODULES_DIR="${SCRIPT_DIR}/modules"

# Charge les fonctions communes
# shellcheck source=lib/common.sh
source "${LIB_DIR}/common.sh"

# Trap les erreurs avec ligne et code retour
trap 'trap_error $LINENO' ERR

# --- Configuration ---------------------------------------------------------
readonly LISA_VERSION="1.0.0"
ENV_FILE="${SCRIPT_DIR}/.env.bootstrap"

# Liste ordonnee des modules (sera enrichie progressivement)
readonly MODULES=(
    "01-prerequisites"
    "02-system-security"
    "03-os-tools"
    "04-python-stack"
    "05-openclaw"
    "06-network"
    "07-monitoring"
    "08-workspace"
    "09-systemd"
    "10-validation"
)


# ============================================================================
# COMMANDES
# ============================================================================

usage() {
    cat <<EOF
LISA Bootstrap v${LISA_VERSION}

Usage: $(basename "$0") [OPTIONS]

OPTIONS
    --dry-run           Affiche ce qui serait execute, sans rien faire
    --force             Re-execute tous les modules (ignore les marqueurs)
    --step MODULE       Execute uniquement le module specifie (ex: 02-system-security)
    --list              Liste les modules et leur etat d'avancement
    --env FILE          Utilise un fichier d'env alternatif (defaut: .env.bootstrap)
    --help, -h          Affiche cette aide

EXEMPLES
    sudo ./bootstrap-lisa.sh --list
    sudo ./bootstrap-lisa.sh --dry-run
    sudo ./bootstrap-lisa.sh
    sudo ./bootstrap-lisa.sh --step 05-openclaw

LOG : ${LOG_FILE}
EOF
}

cmd_list() {
    echo
    echo -e "${C_BOLD}Modules de bootstrap LISA${C_RESET}"
    echo "----------------------------------------"
    for module in "${MODULES[@]}"; do
        marker_summary "${module}"
    done
    echo
}

cmd_dry_run() {
    log_info "Mode DRY-RUN actif. Aucune modification ne sera appliquee."
    for module in "${MODULES[@]}"; do
        run_module "${module}"
    done
}

cmd_run_all() {
    log_step "=== Demarrage du bootstrap LISA v${LISA_VERSION} ==="
    log_info "Hostname  : ${VPS_HOSTNAME}"
    log_info "Admin     : ${ADMIN_EMAIL}"
    log_info "Log file  : ${LOG_FILE}"
    log_info "Modules   : ${#MODULES[@]}"
    echo

    for module in "${MODULES[@]}"; do
        run_module "${module}"
    done

    print_summary
}

cmd_run_single() {
    local target="$1"
    local found=false
    for module in "${MODULES[@]}"; do
        if [[ "${module}" == "${target}" ]]; then
            found=true
            break
        fi
    done
    if ! $found; then
        log_error "Module inconnu: ${target}"
        log_error "Modules disponibles: ${MODULES[*]}"
        exit 1
    fi
    # Force la reexecution du module cible
    marker_delete "${target}"
    run_module "${target}"
}

print_summary() {
    echo
    echo -e "${C_BOLD}=== Bootstrap termine ===${C_RESET}"
    cmd_list
    log_ok "Bootstrap LISA acheve. Prochaine etape : Livraison 2 (configuration OpenClaw)."
}


# ============================================================================
# MAIN
# ============================================================================

main() {
    # Parse arguments
    local mode="run_all"
    local single_target=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=true
                mode="dry_run"
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --step)
                [[ -z "${2:-}" ]] && { log_error "--step requiert un nom de module"; exit 1; }
                mode="run_single"
                single_target="$2"
                shift 2
                ;;
            --list)
                mode="list"
                shift
                ;;
            --env)
                [[ -z "${2:-}" ]] && { log_error "--env requiert un chemin de fichier"; exit 1; }
                ENV_FILE="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log_error "Option inconnue: $1"
                usage
                exit 1
                ;;
        esac
    done

    # Verifications systeme prerequises
    require_root
    init_directories
    load_env "${ENV_FILE}"

    # Verifications post-env
    require_var VPS_HOSTNAME
    require_var ADMIN_EMAIL

    # Dispatch
    case "${mode}" in
        list)        cmd_list ;;
        dry_run)     cmd_dry_run ;;
        run_single)  cmd_run_single "${single_target}" ;;
        run_all)     cmd_run_all ;;
    esac
}

main "$@"
