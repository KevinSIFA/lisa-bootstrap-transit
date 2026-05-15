#!/usr/bin/env bash
# ============================================================================
# 04-python-stack.sh — Pile Python isolee dans /opt/lisa/venv (V3, mai 2026)
# ============================================================================
# Changements V2 → V3 :
# - google-cloud-aiplatform (legacy) → google-genai (nouveau SDK unifié)
# - + sentence-transformers (embeddings supplier_identity + grimoire RAG)
# - + qpdf binding python (PyMuPDF déjà couvre sanitize)
# - python-telegram-bot retiré : OpenClaw gère le channel Telegram nativement
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 04 : pile Python V3 (venv + bibliotheques mai 2026)"

export DEBIAN_FRONTEND=noninteractive
wait_for_apt_lock

LISA_HOME="/opt/lisa"
VENV_PATH="${LISA_HOME}/venv"
REQ_FILE="${LISA_HOME}/requirements.txt"


# --- 1. Python 3.12 et dependances systeme ----------------------------------
log_info "Installation de Python 3.12 et dependances systeme..."
apt-get -qq -y install \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    python3-pip \
    libjpeg-dev \
    zlib1g-dev \
    libxml2-dev \
    libxslt1-dev \
    libgl1 \
    libglib2.0-0 \
    libgomp1

py_version=$(python3.12 --version 2>&1)
log_ok "Python installe : ${py_version}"


# --- 2. Creation de l'environnement virtuel ---------------------------------
log_info "Preparation de /opt/lisa/..."
mkdir -p "${LISA_HOME}"
chown openclaw:openclaw "${LISA_HOME}"

if [[ -d "${VENV_PATH}" ]]; then
    log_ok "Venv deja present : ${VENV_PATH}"
else
    log_info "Creation du venv : ${VENV_PATH}"
    sudo -u openclaw python3.12 -m venv "${VENV_PATH}"
    log_ok "Venv cree"
fi

sudo -u openclaw "${VENV_PATH}/bin/python" -m pip install --quiet --upgrade pip setuptools wheel


# --- 3. Generation du requirements.txt V3 -----------------------------------
log_info "Ecriture de ${REQ_FILE}..."
cat > "${REQ_FILE}" <<'REQEOF'
# LISA V3 — Bibliotheques Python (mai 2026)

# Extraction PDF
pymupdf==1.24.10
pymupdf4llm==0.0.17

# OCR
pytesseract==0.3.13

# Preprocessing image
opencv-python-headless==4.10.0.84
pillow==10.4.0

# Manipulation de donnees
pandas==2.2.3
numpy==1.26.4
rapidfuzz==3.10.1

# Anthropic SDK (Sonnet + Opus + Haiku) — caching natif TTL 1h
anthropic==0.45.0

# Google GenAI SDK (nouveau, remplace google-cloud-aiplatform legacy)
# Couvre Gemini Flash + Gemini Pro via Vertex AI ou AI Studio
google-genai==0.4.0

# Google Auth (Drive API, service accounts)
google-auth==2.35.0
google-auth-httplib2==0.2.0
google-api-python-client==2.149.0

# Embeddings locaux pour supplier_identity + grimoire RAG (384 dim)
sentence-transformers==3.3.1

# Base vectorielle (grimoire RAG)
sqlite-vec==0.1.6

# Validation
pydantic==2.9.2
python-dotenv==1.0.1

# Utilitaires
PyYAML==6.0.2
requests==2.32.3
click==8.1.7
tenacity==9.0.0
loguru==0.7.2
REQEOF
chown openclaw:openclaw "${REQ_FILE}"
log_ok "requirements.txt V3 ecrit"


# --- 4. Installation des bibliotheques --------------------------------------
log_info "Installation des bibliotheques Python (5-10 min — sentence-transformers est lourd)..."
sudo -u openclaw "${VENV_PATH}/bin/pip" install \
    --quiet \
    --disable-pip-version-check \
    -r "${REQ_FILE}"

log_info "Verification des imports critiques..."
sudo -u openclaw "${VENV_PATH}/bin/python" - <<'IMPORTEOF'
import sys
modules = [
    # Extraction
    ("fitz", "PyMuPDF"),
    ("pymupdf4llm", "PyMuPDF4LLM"),
    ("pytesseract", "Tesseract binding"),
    ("cv2", "OpenCV"),
    ("PIL", "Pillow"),
    # IA
    ("anthropic", "Anthropic SDK"),
    ("google.genai", "Google GenAI SDK (nouveau)"),
    ("google.oauth2.service_account", "Google Auth"),
    # Embeddings + RAG
    ("sentence_transformers", "Sentence Transformers (384 dim)"),
    ("sqlite_vec", "sqlite-vec"),
    # Validation
    ("pydantic", "Pydantic"),
    ("yaml", "PyYAML"),
    # Utilitaires
    ("loguru", "Loguru"),
    ("pandas", "Pandas"),
    ("rapidfuzz", "RapidFuzz"),
]
errors = []
for module_name, label in modules:
    try:
        __import__(module_name)
        print(f"  OK  {label} ({module_name})")
    except ImportError as e:
        errors.append(f"{module_name} ({label}): {e}")
        print(f"  KO  {label} ({module_name}): {e}")
if errors:
    print("\nERREURS D'IMPORT :")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print(f"\nOK : {len(modules)} modules importables")
IMPORTEOF
log_ok "Tous les imports Python V3 critiques OK"


# --- 5. Pre-download du modele sentence-transformers (Hugging Face) --------
log_info "Pre-download du modele d'embedding (all-MiniLM-L6-v2, ~90 MB)..."
sudo -u openclaw "${VENV_PATH}/bin/python" - <<'DLEOF' || log_warn "Pre-download echoue, sera retente au runtime"
from sentence_transformers import SentenceTransformer
m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
print(f"Model loaded: {m.get_sentence_embedding_dimension()} dim")
DLEOF


# --- 6. Activation du venv pour openclaw ------------------------------------
bashrc="/home/openclaw/.bashrc"
if [[ -f "${bashrc}" ]] && ! grep -q "LISA venv" "${bashrc}"; then
    cat >> "${bashrc}" <<BASHEOF2

# LISA venv (active automatiquement)
if [[ -f ${VENV_PATH}/bin/activate ]]; then
    source ${VENV_PATH}/bin/activate
fi
BASHEOF2
    log_ok "Venv active automatiquement pour openclaw"
fi


# --- 7. Verification que la pile LISA elle-meme s'importe ------------------
LISA_V2_REPO="${LISA_V2_REPO:-/opt/lisa/lisa-v2-repo}"
if [[ -d "${LISA_V2_REPO}/lisa_pipeline" ]]; then
    log_info "Verification importabilite lisa_pipeline depuis ${LISA_V2_REPO}..."
    if sudo -u openclaw bash -c "cd ${LISA_V2_REPO} && ${VENV_PATH}/bin/python -c 'import lisa_pipeline.config; import lisa_pipeline.models; import lisa_pipeline.validators; print(\"lisa_pipeline OK\")'"; then
        log_ok "lisa_pipeline importable"
    else
        log_warn "lisa_pipeline pas encore importable (normal si modules pas tous deployes)"
    fi
else
    log_warn "Repo LISA V2 absent (${LISA_V2_REPO}) — verification importabilite skipped"
fi


log_ok "Module 04 V3 termine"
