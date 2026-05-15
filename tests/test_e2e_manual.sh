#!/usr/bin/env bash
# ============================================================================
# Test E2E manuel — LISA V3
# ============================================================================
# Procedure de validation manuelle pour une facture test.
# A executer sur le VPS apres deploiement V3 (config + skills + prompts + venv).
#
# Chaque etape s'arrete sur erreur (set -e). Inspect le JSON sur stdout entre
# les etapes pour valider que tout se chaine correctement.
#
# Usage : sudo -iu openclaw bash tests/test_e2e_manual.sh <chemin_pdf_test>
# ============================================================================

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <chemin_pdf_test>"
    echo "Ex:    $0 /opt/lisa/test-factures/FEX-DOC-000000009997.pdf"
    exit 1
fi

INPUT_PDF="$1"
LISA_HOME="${LISA_HOME:-/opt/lisa}"
VENV="${LISA_HOME}/venv"

if [[ ! -f "${INPUT_PDF}" ]]; then
    echo "Fichier introuvable : ${INPUT_PDF}"
    exit 1
fi

if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "Venv introuvable : ${VENV}"
    exit 1
fi

cd "${LISA_HOME}/lisa-v2-repo" || cd /opt/lisa/lisa-v2-repo
PY="${VENV}/bin/python"

echo "════════════════════════════════════════════════════════════════"
echo "  LISA V3 — Test E2E manuel"
echo "  Input : ${INPUT_PDF}"
echo "  Repo  : $(pwd)"
echo "════════════════════════════════════════════════════════════════"
echo ""

# ── Etape 1 : Sanitize ─────────────────────────────────────────────
echo "── Etape 1/8 : sanitize ───────────────────────────────────────"
PROCESSED="${LISA_HOME}/processing/$(basename ${INPUT_PDF})"
mkdir -p "${LISA_HOME}/processing"
${PY} -m lisa_pipeline sanitize "${INPUT_PDF}" "${PROCESSED}"
echo ""
echo "Verif fichier produit : ${PROCESSED}"
ls -la "${PROCESSED}"
echo ""
read -p "Continuer vers vision-split ? [enter] "

# ── Etape 2 : Vision split ─────────────────────────────────────────
echo "── Etape 2/8 : vision-split ───────────────────────────────────"
${PY} -m lisa_pipeline vision-split "${PROCESSED}"
echo ""
echo "Verifier : status=split, invoices listees avec noms FOURNISSEUR_N°"
ls -la "${LISA_HOME}/processing/"
echo ""
read -p "Continuer (choisir un PDF splitté manuellement) ? [enter] "

# Pour la suite, on suppose que vision split a produit /opt/lisa/processing/<NAME>.pdf
# Si une seule facture, on continue avec le PROCESSED original
INVOICE_PDF="${INVOICE_PDF:-${PROCESSED}}"
echo "INVOICE_PDF utilise pour la suite : ${INVOICE_PDF}"
echo "(override : export INVOICE_PDF=/path/to/specific.pdf)"
read -p "Continuer ? [enter] "

# ── Etape 3 : Identité fournisseur ─────────────────────────────────
echo "── Etape 3/8 : identify-supplier ──────────────────────────────"
# Le nom est extrait du nom de fichier (avant le premier _N°)
SUPPLIER_RAW=$(basename "${INVOICE_PDF}" .pdf | cut -d'_' -f1)
echo "Nom brut extrait : ${SUPPLIER_RAW}"
SUPPLIER_JSON=$(${PY} -m lisa_pipeline identify-supplier "${SUPPLIER_RAW}")
echo "${SUPPLIER_JSON}"
SLUG=$(echo "${SUPPLIER_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["slug"])')
echo ""
echo "SLUG resolu : ${SLUG}"
read -p "Continuer ? [enter] "

# ── Etape 4 : Classification type ──────────────────────────────────
echo "── Etape 4/8 : classify-type ──────────────────────────────────"
CLASSIFY_JSON=$(${PY} -m lisa_pipeline classify-type "${INVOICE_PDF}")
echo "${CLASSIFY_JSON}"
DOC_TYPE=$(echo "${CLASSIFY_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["doc_type"])')
echo ""
echo "DOC_TYPE resolu : ${DOC_TYPE}"
read -p "Continuer ? [enter] "

# ── Etape 5 : Tentative script ─────────────────────────────────────
echo "── Etape 5/8 : apply-script ───────────────────────────────────"
set +e
${PY} -m lisa_pipeline apply-script "${INVOICE_PDF}" "${SLUG}" "${DOC_TYPE}"
APPLY_EXIT=$?
set -e
echo ""
echo "Exit code apply-script : ${APPLY_EXIT}  (0=OK, 1=fail, 2=needs_seed)"
read -p "Continuer ? [enter] "

# ── Etape 6 : Repair ou Gemini fallback selon resultat ─────────────
if [[ ${APPLY_EXIT} -ne 0 ]]; then
    echo "── Etape 6/8 : repair-script (apply a echoue) ───────────────"
    set +e
    ${PY} -m lisa_pipeline repair-script "${SLUG}" "${DOC_TYPE}" "${INVOICE_PDF}"
    REPAIR_EXIT=$?
    set -e
    echo ""
    echo "Exit code repair-script : ${REPAIR_EXIT}"
    read -p "Continuer ? [enter] "

    if [[ ${REPAIR_EXIT} -ne 0 ]]; then
        echo "── Etape 7/8 : gemini-fallback (repair a echoue) ─────────"
        ${PY} -m lisa_pipeline gemini-fallback "${INVOICE_PDF}"
        echo ""
        read -p "Verifier le JSON retourne, continuer ? [enter] "
    fi
fi

# ── Etape 8 : Catalogue + Health ───────────────────────────────────
echo "── Etape 8/8 : etat catalogue + health ────────────────────────"
${PY} -m lisa_pipeline catalogue-meta "${SLUG}"
echo ""
${PY} -m lisa_pipeline catalogue-health "${SLUG}" "${DOC_TYPE}"
echo ""

echo "════════════════════════════════════════════════════════════════"
echo "  Test E2E termine"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Verifications finales a faire manuellement :"
echo "  1. ls ${LISA_HOME}/catalogue/${SLUG}/ — dossier fournisseur cree"
echo "  2. ls ${LISA_HOME}/catalogue/${SLUG}/samples/${DOC_TYPE}/ — sample ajoute"
echo "  3. ${PY} -m lisa_pipeline grimoire-list --slug ${SLUG} — leçons capitalisees"
echo "  4. cat ${LISA_HOME}/logs/*.log | tail -50 — pas d'erreur"
