#!/usr/bin/env bash
# ============================================================================
# 10-validation.sh — Healthcheck final du bootstrap
# ============================================================================
# Passe en revue toutes les briques installees par les modules precedents
# et affiche un rapport coloré. Sort en erreur si une assertion critique echoue.
# ============================================================================

set -uo pipefail   # pas de -e ici : on veut accumuler les fails

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 10 : healthcheck final"


# --- Compteurs --------------------------------------------------------------
declare -i tests_total=0
declare -i tests_ok=0
declare -i tests_fail=0
declare -a failures=()

check() {
    local desc="$1"
    local cmd="$2"
    tests_total=$((tests_total + 1))
    if eval "${cmd}" &>/dev/null; then
        echo -e "  ${C_GREEN}[PASS]${C_RESET} ${desc}"
        tests_ok=$((tests_ok + 1))
    else
        echo -e "  ${C_RED}[FAIL]${C_RESET} ${desc}"
        tests_fail=$((tests_fail + 1))
        failures+=("${desc}")
    fi
}


# --- 1. Systeme de base -----------------------------------------------------
echo
echo -e "${C_BOLD}=== Systeme de base ===${C_RESET}"
check "OS Ubuntu 24.04" "grep -q 'VERSION_ID=\"24.04\"' /etc/os-release"
check "User openclaw existe" "id openclaw"
check "Home /home/openclaw existe" "test -d /home/openclaw"
check "Hostname conforme" "[[ \$(hostname) == '${VPS_HOSTNAME}' ]]"
check "Fuseau horaire ${VPS_TIMEZONE}" "[[ \$(timedatectl show --property=Timezone --value) == '${VPS_TIMEZONE}' ]]"
check "Swap actif" "swapon --show | grep -q swap"


# --- 2. Securite -----------------------------------------------------------
echo
echo -e "${C_BOLD}=== Securite ===${C_RESET}"
check "UFW actif" "ufw status | grep -q 'Status: active'"
check "fail2ban actif" "systemctl is-active --quiet fail2ban"
check "SSH password auth disabled" "sshd -T 2>/dev/null | grep -q 'passwordauthentication no'"
check "SSH 22 bloque sur internet" "ufw status | grep -q '22/tcp.*DENY'"
check "SSH accessible via Tailscale" "ufw status | grep -q '22/tcp on tailscale0'"
check "unattended-upgrades actif" "systemctl is-active --quiet unattended-upgrades"


# --- 3. Outils OS ----------------------------------------------------------
echo
echo -e "${C_BOLD}=== Outils OS ===${C_RESET}"
check "Tesseract installe (avec fra)" "tesseract --list-langs 2>&1 | grep -q fra"
check "exiftool installe" "command -v exiftool"
check "qpdf installe" "command -v qpdf"
check "Git installe" "command -v git"
check "sqlite3 installe" "command -v sqlite3"
check "Extension sqlite-vec presente" "test -f /opt/lisa/sqlite-vec/vec0.so"
check "Build tools (cmake)" "command -v cmake"


# --- 4. Python -------------------------------------------------------------
echo
echo -e "${C_BOLD}=== Python ===${C_RESET}"
check "Python 3.12 installe" "command -v python3.12"
check "Venv LISA existe" "test -x /opt/lisa/venv/bin/python"
check "Anthropic SDK importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import anthropic'"
check "Vertex AI SDK importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import google.cloud.aiplatform'"
check "PyMuPDF importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import fitz'"
check "OpenCV importable (libGL OK)" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import cv2'"
check "Tesseract Python importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import pytesseract'"
check "Telegram bot importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import telegram'"
check "sqlite-vec Python importable" "sudo -u openclaw /opt/lisa/venv/bin/python -c 'import sqlite_vec'"


# --- 5. OpenClaw -----------------------------------------------------------
echo
echo -e "${C_BOLD}=== OpenClaw ===${C_RESET}"
check "Node.js installe" "command -v node"
check "OpenClaw CLI installe" "command -v openclaw"
check "OpenClaw config.json present" "test -f /home/openclaw/.openclaw/config.json"
check "Config gateway loopback" "grep -q '\"host\": \"127.0.0.1\"' /home/openclaw/.openclaw/config.json"


# --- 6. Reseau (Tailscale) -------------------------------------------------
echo
echo -e "${C_BOLD}=== Reseau ===${C_RESET}"
check "Tailscale installe" "command -v tailscale"
check "Tailscaled actif" "systemctl is-active --quiet tailscaled"
check "VPS connecte au tailnet" "tailscale status 2>/dev/null | grep -q '^100\\.'"
check "IPv4 Tailscale obtenue" "tailscale ip -4 | grep -qE '^100\\.[0-9]+\\.[0-9]+\\.[0-9]+'"


# --- 7. Monitoring (Netdata) -----------------------------------------------
echo
echo -e "${C_BOLD}=== Monitoring ===${C_RESET}"
check "Netdata installe" "command -v netdata"
check "Netdata actif" "systemctl is-active --quiet netdata"
check "Netdata bind loopback" "ss -tlnp | grep -E ':19999' | grep -q 127.0.0.1"
check "Alertes Telegram configurees" "grep -q 'SEND_TELEGRAM=\"YES\"' /etc/netdata/health_alarm_notify.conf"


# --- 8. Workspace LISA -----------------------------------------------------
echo
echo -e "${C_BOLD}=== Workspace LISA ===${C_RESET}"
check "Dossier inbox existe" "test -d /opt/lisa/inbox"
check "Dossier outbox existe" "test -d /opt/lisa/outbox"
check "Dossier quarantine existe" "test -d /opt/lisa/quarantine"
check "Dossier grimoire existe" "test -d /opt/lisa/grimoire"
check "Grimoire DB initialise" "test -f /opt/lisa/grimoire/grimoire.db"
check "Grimoire schema (table lessons)" "sqlite3 /opt/lisa/grimoire/grimoire.db '.tables' | grep -q lessons"
check "Queue DB initialise" "test -f /opt/lisa/queue/queue.db"
check "Queue schema (table queue)" "sqlite3 /opt/lisa/queue/queue.db '.tables' | grep -q queue"
check "Dossier catalogue existe" "test -d /opt/lisa/catalogue"
check "Permissions openclaw sur /opt/lisa" "[[ \$(stat -c %U /opt/lisa) == 'openclaw' ]]"


# --- 9. Secrets ------------------------------------------------------------
echo
echo -e "${C_BOLD}=== Secrets ===${C_RESET}"
check "Service account Google present" "test -f /opt/lisa/secrets/lisa-service-account.json"
check "Deploy key GitHub present" "test -f /opt/lisa/secrets/lisa_deploy_key"
check "Permissions secrets 600" "[[ \$(stat -c %a /opt/lisa/secrets/lisa-service-account.json) == '600' ]]"


# --- 10. Cron et logs ------------------------------------------------------
echo
echo -e "${C_BOLD}=== Cron & logs ===${C_RESET}"
check "Cron LISA installe" "test -f /etc/cron.d/lisa"
check "Script cron-push-catalogue" "test -x /opt/lisa/scripts/cron-push-catalogue.sh"
check "Script cron-healthcheck" "test -x /opt/lisa/scripts/cron-healthcheck.sh"
check "Logrotate LISA configure" "test -f /etc/logrotate.d/lisa"
check "Dossier /var/log/lisa existe" "test -d /var/log/lisa"


# --- Resume final ----------------------------------------------------------
echo
echo -e "${C_BOLD}=== Resume ===${C_RESET}"
echo -e "Tests passes : ${C_GREEN}${tests_ok}${C_RESET} / ${tests_total}"
if (( tests_fail > 0 )); then
    echo -e "Tests echoues: ${C_RED}${tests_fail}${C_RESET}"
    echo
    echo -e "${C_BOLD}Echecs :${C_RESET}"
    for f in "${failures[@]}"; do
        echo -e "  ${C_RED}-${C_RESET} ${f}"
    done
    echo
    log_error "Le bootstrap est incomplet. Corrige les points ci-dessus avant la Livraison 2."
    exit 1
fi

echo
log_ok "Toutes les verifications passent. Bootstrap LISA complet."
log_ok "Le VPS est pret pour la Livraison 2 (configuration OpenClaw)."
