#!/usr/bin/env bash
# ============================================================================
# 08-workspace.sh — Arborescence LISA + grimoire sqlite-vec + queue persistante
# ============================================================================
# - Cree l'arborescence /opt/lisa/{inbox,processing,outbox,archive,quarantine,...}
# - Initialise le grimoire (sqlite-vec) avec son schema
# - Initialise la queue persistante (sqlite + dossiers)
# - Prepare le repertoire catalogue Git (clone deferred au cron quotidien)
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 08 : workspace LISA"

LISA_HOME="/opt/lisa"
SQLITE_VEC_LIB="${LISA_HOME}/sqlite-vec/vec0"


# --- 1. Arborescence /opt/lisa/ --------------------------------------------
log_info "Creation de l'arborescence ${LISA_HOME}/..."
mkdir -p \
    "${LISA_HOME}/inbox" \
    "${LISA_HOME}/processing" \
    "${LISA_HOME}/outbox" \
    "${LISA_HOME}/archive" \
    "${LISA_HOME}/quarantine" \
    "${LISA_HOME}/grimoire" \
    "${LISA_HOME}/scripts" \
    "${LISA_HOME}/logs" \
    "${LISA_HOME}/catalogue" \
    "${LISA_HOME}/queue/pending" \
    "${LISA_HOME}/queue/processing" \
    "${LISA_HOME}/queue/done" \
    "${LISA_HOME}/queue/quarantine"

# Permissions : openclaw proprietaire de tout
chown -R openclaw:openclaw "${LISA_HOME}"
chmod 750 "${LISA_HOME}"

log_ok "Arborescence LISA creee :"
log_ok "  inbox / processing / outbox / archive / quarantine"
log_ok "  grimoire / scripts / logs / catalogue"
log_ok "  queue/{pending,processing,done,quarantine}"


# --- 2. Initialisation du grimoire (sqlite-vec) ----------------------------
GRIMOIRE_DB="${LISA_HOME}/grimoire/grimoire.db"

if [[ -f "${GRIMOIRE_DB}" ]]; then
    log_ok "Grimoire deja initialise : ${GRIMOIRE_DB}"
else
    log_info "Initialisation du grimoire sqlite-vec..."
    sudo -u openclaw sqlite3 "${GRIMOIRE_DB}" <<EOF
-- Schema du grimoire LISA : capitalisation par fournisseur

-- Lecons apprises (texte libre + embedding pour recherche semantique)
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    category TEXT,                         -- ex: 'ocr_method', 'preprocessing', 'edge_case'
    note TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lessons_supplier ON lessons(supplier);
CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category);

-- Embeddings vectoriels (dimension 768, type Gemini embeddings)
.load ${SQLITE_VEC_LIB}
CREATE VIRTUAL TABLE IF NOT EXISTS lessons_vec USING vec0(
    lesson_id INTEGER PRIMARY KEY,
    embedding FLOAT[768]
);

-- Catalogue des fournisseurs et scores de sante
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    script_path TEXT,                      -- chemin du script Python par fournisseur
    pipeline_level INTEGER,                -- 1, 2, ou 3
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    last_seen TEXT,
    last_calibration TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers(name);

-- Historique des calibrations Opus
CREATE TABLE IF NOT EXISTS calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    triggered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT,                           -- 'success', 'failure'
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_calibrations_supplier ON calibrations(supplier);

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
EOF
    log_ok "Grimoire initialise : ${GRIMOIRE_DB}"
fi


# --- 3. Initialisation de la queue persistante ------------------------------
QUEUE_DB="${LISA_HOME}/queue/queue.db"

if [[ -f "${QUEUE_DB}" ]]; then
    log_ok "Queue deja initialisee : ${QUEUE_DB}"
else
    log_info "Initialisation de la queue persistante..."
    sudo -u openclaw sqlite3 "${QUEUE_DB}" <<EOF
-- Queue de traitement LISA

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    drive_file_id TEXT,                    -- ID Google Drive du fichier source
    sha256 TEXT,                           -- hash pour deduplication
    status TEXT NOT NULL DEFAULT 'pending', -- pending, processing, done, quarantine
    level INTEGER,                         -- 1 / 2 / 3 du pipeline
    supplier TEXT,                         -- detecte apres classification
    received_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    attempts INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_sha256 ON queue(sha256);
CREATE INDEX IF NOT EXISTS idx_queue_received ON queue(received_at);

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
EOF
    log_ok "Queue initialisee : ${QUEUE_DB}"
fi


# --- 4. Preparation du depot catalogue (Git) -------------------------------
CATALOGUE_DIR="${LISA_HOME}/catalogue"
if [[ -d "${CATALOGUE_DIR}/.git" ]]; then
    log_ok "Catalogue Git deja initialise"
else
    log_info "Initialisation du depot catalogue (sans clone distant pour l'instant)..."
    sudo -u openclaw git -C "${CATALOGUE_DIR}" init -q -b main 2>/dev/null || true
    # On configure le remote si la variable est definie
    if [[ -n "${CATALOGUE_GIT_REMOTE:-}" ]]; then
        sudo -u openclaw git -C "${CATALOGUE_DIR}" remote add origin "${CATALOGUE_GIT_REMOTE}" 2>/dev/null || true
    fi
    # README initial
    cat > "${CATALOGUE_DIR}/README.md" <<EOF
# LISA Catalogue

Scripts par fournisseur generes par LISA via calibration Opus 4.7.

Push automatique tous les jours a 23h via cron.
EOF
    chown -R openclaw:openclaw "${CATALOGUE_DIR}"
    sudo -u openclaw git -C "${CATALOGUE_DIR}" add README.md 2>/dev/null || true
    sudo -u openclaw git -C "${CATALOGUE_DIR}" -c user.email="lisa@${VPS_HOSTNAME}" -c user.name="LISA Bot" commit -q -m "Initial catalogue" 2>/dev/null || true
    log_ok "Catalogue initialise (premier push manuel ou via cron)"
fi


log_ok "Module 08 termine"
