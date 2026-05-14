#!/usr/bin/env bash
# ============================================================================
# 04-python-stack.sh — Pile Python isolee dans /opt/lisa/venv
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 04 : pile Python (venv + bibliotheques)"

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
    libxslt1-dev

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


# --- 3. Generation du requirements.txt --------------------------------------
log_info "Ecriture de ${REQ_FILE}..."
cat > "${REQ_FILE}" <<'EOF'
# LISA — Bibliotheques Python (pipeline d'extraction de factures)

# Extraction PDF
pymupdf==1.24.10
pymupdf4llm==0.0.17

# OCR
pytesseract==0.3.13
rapidocr-onnxruntime==1.3.24

# Preprocessing image
opencv-python-headless==4.10.0.84
pillow==10.4.0

# Manipulation de donnees
pandas==2.2.3
numpy==1.26.4
rapidfuzz==3.10.1

# IA APIs
anthropic==0.39.0
google-cloud-aiplatform==1.71.1
google-api-python-client==2.149.0
google-auth==2.35.0
google-auth-httplib2==0.2.0

# Telegram bot
python-telegram-bot==21.6

# Base vectorielle
sqlite-vec==0.1.6

# Validation
pydantic==2.9.2
python-dotenv==1.0.1

# Utilitaires
requests==2.32.3
click==8.1.7
tenacity==9.0.0
loguru==0.7.2
EOF
chown openclaw:openclaw "${REQ_FILE}"
log_ok "requirements.txt ecrit"


# --- 4. Installation des bibliotheques --------------------------------------
log_info "Installation des bibliotheques Python (3-5 min)..."
sudo -u openclaw "${VENV_PATH}/bin/pip" install \
    --quiet \
    --disable-pip-version-check \
    -r "${REQ_FILE}"

log_info "Verification des imports critiques..."
sudo -u openclaw "${VENV_PATH}/bin/python" - <<'PYEOF'
import sys
modules = [
    "fitz", "pymupdf4llm", "pytesseract", "cv2", "pandas",
    "anthropic", "google.cloud.aiplatform", "telegram",
    "sqlite_vec", "pydantic", "dotenv", "loguru",
]
errors = []
for m in modules:
    try:
        __import__(m)
    except ImportError as e:
        errors.append(f"{m}: {e}")
if errors:
    print("ERREURS D'IMPORT :")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print(f"OK : {len(modules)} modules importables")
PYEOF
log_ok "Tous les imports Python critiques OK"


# --- 5. Activation du venv pour openclaw ------------------------------------
bashrc="/home/openclaw/.bashrc"
if [[ -f "${bashrc}" ]] && ! grep -q "LISA venv" "${bashrc}"; then
    cat >> "${bashrc}" <<EOF

# LISA venv (active automatiquement)
if [[ -f ${VENV_PATH}/bin/activate ]]; then
    source ${VENV_PATH}/bin/activate
fi
EOF
    log_ok "Venv active automatiquement pour openclaw"
fi


log_ok "Module 04 termine"
