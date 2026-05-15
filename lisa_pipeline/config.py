"""Configuration centrale LISA pipeline.

Charge les variables d'environnement, expose les chemins et les IDs modèles.
Tout le reste du pipeline importe depuis ici.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# ============================================================================
# Chemins disque
# ============================================================================
LISA_HOME = Path(os.environ.get("LISA_HOME", "/opt/lisa"))

# Sous-arborescence runtime
INBOX_DIR = LISA_HOME / "inbox"          # PDFs bruts depuis Drive
PROCESSING_DIR = LISA_HOME / "processing"  # PDFs sanitized en cours
OUTBOX_DIR = LISA_HOME / "outbox"        # JSON résultats avant push Drive
ARCHIVE_DIR = LISA_HOME / "archive"      # PDFs traités (sera push Drive Archive)
QUARANTINE_DIR = LISA_HOME / "quarantine"
CATALOGUE_DIR = LISA_HOME / "catalogue"  # 1 dossier par fournisseur
SAMPLES_ROOT = CATALOGUE_DIR              # samples sous catalogue/<slug>/samples/<type>/
SCRIPTS_RUNTIME_DIR = LISA_HOME / "scripts"  # copie active des scripts par fournisseur
PROMPTS_DIR = LISA_HOME / "prompts"      # prompts byte-stables (V6.1, vision split, orchestrator)
GRIMOIRE_DB = LISA_HOME / "grimoire.db"  # sqlite-vec
QUEUE_DB = LISA_HOME / "queue.db"        # queue persistante sqlite
LOGS_DIR = LISA_HOME / "logs"
SECRETS_DIR = LISA_HOME / "secrets"

# Sous-dossiers quarantaine (un par cas)
QUARANTINE_NOT_INVOICE = QUARANTINE_DIR / "not_invoice"
QUARANTINE_NO_PRODUCTS = QUARANTINE_DIR / "no_products"
QUARANTINE_INCOMPLETE = QUARANTINE_DIR / "incomplete"
QUARANTINE_OVERSIZED = QUARANTINE_DIR / "oversized"
QUARANTINE_ENCRYPTED = QUARANTINE_DIR / "encrypted"
QUARANTINE_REPAIR_FAILED = QUARANTINE_DIR / "repair_failed"
QUARANTINE_JS_RESIDUAL = QUARANTINE_DIR / "js_residual"
QUARANTINE_REJECTED = QUARANTINE_DIR / "rejected"  # rejet manuel via GAS

# Garde-fous sanitize
SANITIZE_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
SANITIZE_MAX_PAGES = 100


# ============================================================================
# Modèles IA — IDs OpenClaw / SDK natifs
# ============================================================================

# Anthropic — orchestrateur, calibration, heartbeat
ANTHROPIC_SONNET = "claude-sonnet-4-6"
ANTHROPIC_OPUS = "claude-opus-4-7"
ANTHROPIC_HAIKU = "claude-haiku-4-5"

# Google Gemini — vision split + fallback
GEMINI_FLASH = "gemini-3-flash-preview"      # split + classification facture/non-facture
GEMINI_PRO = "gemini-3.1-pro-preview"        # fallback extraction V6.1

# Niveau thinking Gemini Pro (cf. doc Gemini 3.x)
GEMINI_THINKING_LEVEL = "medium"

# Région Vertex AI (à valider en live ; voir Z2 doc OpenClaw §7.2)
GOOGLE_VERTEX_LOCATION = os.environ.get("GOOGLE_VERTEX_LOCATION", "us-central1")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "lisa-496301")
GOOGLE_SERVICE_ACCOUNT_JSON = Path(
    os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "/opt/lisa/secrets/lisa-service-account.json")
)


# ============================================================================
# Prompt caching — voir docs/prompt_caching_05_2026.md
# ============================================================================

# Anthropic
ANTHROPIC_CACHE_TTL_SONNET = "1h"   # ttl explicite via cache_control
ANTHROPIC_CACHE_TTL_OPUS = "1h"
# Haiku heartbeat : pas de caching (<4096 tokens seuil minimum)

# Anthropic API key (workspace unique dev/staging/prod)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Gemini — implicit caching auto en phase 1 (volume <30/jour)
# Si volume > 30/jour, activer EXPLICIT_GEMINI_CACHE=true et basculer explicit avec TTL 1h
EXPLICIT_GEMINI_CACHE = os.environ.get("EXPLICIT_GEMINI_CACHE", "false").lower() == "true"
GEMINI_CACHE_TTL_SECONDS = int(os.environ.get("GEMINI_CACHE_TTL_SECONDS", "3600"))


# ============================================================================
# Grimoire RAG — 3 catégories validées
# ============================================================================
GRIMOIRE_CATEGORIES = ("extraction_quirk", "ocr_method", "business_rule")
GRIMOIRE_EMBEDDING_DIM = 384
GRIMOIRE_DEFAULT_TOP_K = 10
GRIMOIRE_MIN_SIMILARITY = 0.7


# ============================================================================
# Identité fournisseur — seuils fusion
# ============================================================================
SUPPLIER_SLUG_SUFFIXES_SOCIAUX = (
    # Français
    "sarl", "sa", "sas", "snc", "sci", "scm", "scp", "sasu", "eurl", "eirl",
    # Anglo
    "ltd", "llc", "inc", "corp", "co", "plc", "pllc", "lp", "llp",
    # Allemand
    "gmbh", "ag", "kg", "ohg", "ug", "ek", "gbr",
    # Italien
    "srl", "spa", "snc",
    # Néerlandais
    "bv", "nv",
    # Espagnol
    "sl", "sa", "slu",
)

# Cosine similarity sqlite-vec
SUPPLIER_AUTO_MERGE_THRESHOLD = 0.92
SUPPLIER_REVIEW_THRESHOLD = 0.75


# ============================================================================
# Classification type de document
# ============================================================================
CLASSIFY_NATIVE_MIN_CHARS = 500          # PyMuPDF text length pour natif
CLASSIFY_OCR_PROPRE_MIN_CONFIDENCE = 75  # Tesseract avg_confidence
CLASSIFY_OCR_DIFFICILE_MIN_CONFIDENCE = 60  # Sous ce seuil, escalade direct Gemini

DOC_TYPES = ("natif", "scan_propre", "scan_difficile")


# ============================================================================
# Health score — seuils décision
# ============================================================================
HEALTH_SEALED_THRESHOLD = 0.95           # script scellé (mais pas définitivement)
HEALTH_ACCEPTABLE_THRESHOLD = 0.80
HEALTH_DRIFT_MINOR_THRESHOLD = 0.60      # drift mineur : réparation à prochaine erreur
# Sous HEALTH_DRIFT_MINOR_THRESHOLD : drift majeur, réparation immédiate

ROLLING_WINDOW_SIZE = 5


# ============================================================================
# Validation math
# ============================================================================
# Tolérance pour Σ(line.amount) + xfee.amount == total_ht
VALIDATE_MATH_TOLERANCE_RATIO = 0.01     # 1% (couvre les arrondis comptables)
VALIDATE_MATH_TOLERANCE_ABS = 0.05        # Au moins 0.05 € de tolérance absolue


# ============================================================================
# Telegram — alertes
# ============================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_IDS = tuple(
    int(x.strip()) for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
)


# ============================================================================
# OpenClaw — alertes proactives
# ============================================================================
REPAIR_RATE_ALERT_PER_HOUR = 10  # alerte non-bloquante si >10 réparations en 1h


# ============================================================================
# Drive Google
# ============================================================================
DRIVE_INBOX_FOLDER_ID = os.environ.get("DRIVE_INBOX_FOLDER_ID", "")
DRIVE_OUTBOX_FOLDER_ID = os.environ.get("DRIVE_OUTBOX_FOLDER_ID", "")
DRIVE_ARCHIVE_FOLDER_ID = os.environ.get("DRIVE_ARCHIVE_FOLDER_ID", "")
DRIVE_QUARANTINE_FOLDER_ID = os.environ.get("DRIVE_QUARANTINE_FOLDER_ID", "")


# ============================================================================
# Création des dossiers manquants (idempotent, appelé au démarrage)
# ============================================================================
def ensure_dirs() -> None:
    """Crée toute l'arborescence runtime si absente. Appelé une fois au démarrage."""
    for d in (
        INBOX_DIR, PROCESSING_DIR, OUTBOX_DIR, ARCHIVE_DIR,
        QUARANTINE_NOT_INVOICE, QUARANTINE_NO_PRODUCTS, QUARANTINE_INCOMPLETE,
        QUARANTINE_OVERSIZED, QUARANTINE_ENCRYPTED, QUARANTINE_REPAIR_FAILED,
        QUARANTINE_JS_RESIDUAL, QUARANTINE_REJECTED,
        CATALOGUE_DIR, SCRIPTS_RUNTIME_DIR, PROMPTS_DIR, LOGS_DIR, SECRETS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def get_anthropic_api_key() -> str:
    """Récupère la clé Anthropic, lève KeyError si absente."""
    if not ANTHROPIC_API_KEY:
        raise KeyError(
            "ANTHROPIC_API_KEY absente. Voir /etc/lisa/openclaw.env ou sops secrets."
        )
    return ANTHROPIC_API_KEY


def get_google_service_account_path() -> Path:
    """Récupère le chemin du service account JSON, lève FileNotFoundError si absent."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON.exists():
        raise FileNotFoundError(
            f"Service account Google introuvable : {GOOGLE_SERVICE_ACCOUNT_JSON}"
        )
    return GOOGLE_SERVICE_ACCOUNT_JSON
