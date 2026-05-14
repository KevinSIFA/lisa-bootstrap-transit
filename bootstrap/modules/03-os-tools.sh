#!/usr/bin/env bash
# ============================================================================
# 03-os-tools.sh — Outils OS pour l'extraction et la securite PDF
# ============================================================================
# Installe au niveau systeme (apt) :
#   - Tesseract OCR + langpacks (fra + eng + osd)
#   - exiftool (nettoyage metadonnees PDF)
#   - qpdf (manipulation/securite PDF)
#   - git (versioning catalogue)
#   - Outils de compilation (pour pip install plus tard)
#   - sqlite3 + extension sqlite-vec (compile depuis source)
# ============================================================================

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
# shellcheck source=../lib/common.sh
source "${LIB_DIR}/common.sh"

log_step "Module 03 : outils OS pour extraction"

export DEBIAN_FRONTEND=noninteractive


# --- 1. Tesseract OCR + langpacks -------------------------------------------
log_info "Installation de Tesseract OCR et langpacks..."
apt-get -qq -y install \
    tesseract-ocr \
    tesseract-ocr-fra \
    tesseract-ocr-eng \
    tesseract-ocr-osd

tess_version=$(tesseract --version 2>&1 | head -1)
log_ok "Tesseract installe : ${tess_version}"


# --- 2. exiftool, qpdf, outils PDF ------------------------------------------
log_info "Installation des outils PDF (exiftool, qpdf, poppler)..."
apt-get -qq -y install \
    libimage-exiftool-perl \
    qpdf \
    poppler-utils \
    ghostscript

log_ok "Outils PDF installes (exiftool $(exiftool -ver), qpdf $(qpdf --version | head -1 | awk '{print $NF}'))"


# --- 3. Git + outils de compilation -----------------------------------------
log_info "Installation de git et chaine de compilation..."
apt-get -qq -y install \
    git \
    build-essential \
    pkg-config \
    libssl-dev \
    libffi-dev \
    cmake

log_ok "Git $(git --version | awk '{print $3}') + chaine de compilation installes"


# --- 4. SQLite + extension sqlite-vec ---------------------------------------
log_info "Installation de SQLite 3 et de l'extension sqlite-vec..."
apt-get -qq -y install sqlite3 libsqlite3-dev

# sqlite-vec : on installe depuis les binaires precompiles (Linux x86_64)
# Plus simple et plus fiable que la compilation depuis source
SQLITE_VEC_VERSION="v0.1.6"
SQLITE_VEC_DIR="/opt/lisa/sqlite-vec"

if [[ -f "${SQLITE_VEC_DIR}/vec0.so" ]]; then
    log_ok "sqlite-vec deja installe : ${SQLITE_VEC_DIR}/vec0.so"
else
    mkdir -p "${SQLITE_VEC_DIR}"
    cd "${SQLITE_VEC_DIR}"

    # Detection architecture
    arch=$(uname -m)
    case "${arch}" in
        x86_64)  asset="sqlite-vec-${SQLITE_VEC_VERSION#v}-loadable-linux-x86_64.tar.gz" ;;
        aarch64) asset="sqlite-vec-${SQLITE_VEC_VERSION#v}-loadable-linux-aarch64.tar.gz" ;;
        *)
            log_error "Architecture non supportee pour sqlite-vec : ${arch}"
            exit 1
            ;;
    esac

    url="https://github.com/asg017/sqlite-vec/releases/download/${SQLITE_VEC_VERSION}/${asset}"
    log_info "Telechargement : ${url}"
    curl -fL -o sqlite-vec.tar.gz "${url}"
    tar -xzf sqlite-vec.tar.gz
    rm -f sqlite-vec.tar.gz
    chmod +x ./*.so

    if [[ ! -f "vec0.so" ]]; then
        log_error "Echec installation sqlite-vec : vec0.so introuvable"
        exit 1
    fi
    log_ok "sqlite-vec installe : ${SQLITE_VEC_DIR}/vec0.so"
fi

# Test de chargement de l'extension
if echo "SELECT vec_version();" | sqlite3 -cmd ".load ${SQLITE_VEC_DIR}/vec0" ":memory:" > /dev/null 2>&1; then
    log_ok "sqlite-vec se charge correctement dans SQLite"
else
    log_warn "sqlite-vec installe mais ne se charge pas (sera retente plus tard)"
fi


# --- 5. Verification finale -------------------------------------------------
log_info "Verification finale des outils..."
for cmd in tesseract exiftool qpdf git sqlite3 cmake; do
    if command -v "${cmd}" &>/dev/null; then
        log_ok "  - ${cmd} : $(command -v ${cmd})"
    else
        log_error "  - ${cmd} : INTROUVABLE"
        exit 1
    fi
done


log_ok "Module 03 termine"
