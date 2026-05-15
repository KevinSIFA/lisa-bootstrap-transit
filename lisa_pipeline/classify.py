"""Classification du type de document : natif / scan_propre / scan_difficile.

Critères (cf. spec §9) :
- natif : PyMuPDF extract_text() retourne > CLASSIFY_NATIVE_MIN_CHARS avec layout cohérent
- scan_propre : peu de texte natif ET Tesseract avg_confidence ≥ 75 %
- scan_difficile : peu de texte natif ET Tesseract avg_confidence < 75 %
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

try:
    import pytesseract
    from PIL import Image
    import io
    TESSERACT_OK = True
except ImportError:
    TESSERACT_OK = False

from .config import (
    CLASSIFY_NATIVE_MIN_CHARS,
    CLASSIFY_OCR_DIFFICILE_MIN_CONFIDENCE,
    CLASSIFY_OCR_PROPRE_MIN_CONFIDENCE,
)


@dataclass
class ClassifyResult:
    """Résultat de classification — utilisé en aval par script_runner."""
    doc_type: str                  # "natif" | "scan_propre" | "scan_difficile"
    native_text_chars: int = 0     # chars retournés par PyMuPDF sur p.1
    ocr_confidence: Optional[float] = None  # avg_confidence Tesseract sur p.1 si applicable
    page_count: int = 0
    error: Optional[str] = None


# ============================================================================
# Helpers
# ============================================================================
def _extract_native_text(pdf_path: Path, page_idx: int = 0) -> str:
    """Extrait le texte PyMuPDF de la page demandée (par défaut p.1)."""
    if not PYMUPDF_OK:
        return ""
    try:
        with fitz.open(str(pdf_path)) as doc:
            if page_idx >= len(doc):
                return ""
            return doc[page_idx].get_text("text") or ""
    except Exception as e:
        logger.warning(f"PyMuPDF échec sur {pdf_path.name} p.{page_idx}: {e}")
        return ""


def _ocr_confidence(pdf_path: Path, page_idx: int = 0, dpi: int = 200) -> Optional[float]:
    """Calcule avg_confidence Tesseract sur la page demandée.

    Returns:
        float entre 0 et 100, ou None si Tesseract indisponible / erreur.
    """
    if not (PYMUPDF_OK and TESSERACT_OK):
        return None
    try:
        with fitz.open(str(pdf_path)) as doc:
            if page_idx >= len(doc):
                return None
            page = doc[page_idx]
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        data = pytesseract.image_to_data(
            img, lang="fra+eng", output_type=pytesseract.Output.DICT
        )
        confidences = [int(c) for c in data["conf"] if c not in ("-1", -1)]
        if not confidences:
            return 0.0
        return sum(confidences) / len(confidences)
    except Exception as e:
        logger.warning(f"Tesseract échec sur {pdf_path.name} p.{page_idx}: {e}")
        return None


def _page_count(pdf_path: Path) -> int:
    if not PYMUPDF_OK:
        return 0
    try:
        with fitz.open(str(pdf_path)) as doc:
            return len(doc)
    except Exception:
        return 0


# ============================================================================
# Entrée principale
# ============================================================================
def classify(pdf_path: Path) -> ClassifyResult:
    """Classifie un PDF selon les 3 types LISA.

    Args:
        pdf_path : PDF sanitized (depuis /opt/lisa/processing/)

    Returns:
        ClassifyResult avec doc_type, métriques utilisées pour la décision.
    """
    if not PYMUPDF_OK:
        return ClassifyResult(
            doc_type="scan_difficile",  # défaut conservateur si PyMuPDF KO
            error="PyMuPDF indisponible — type forcé en scan_difficile"
        )

    page_count = _page_count(pdf_path)
    if page_count == 0:
        return ClassifyResult(
            doc_type="scan_difficile",
            error="PDF illisible ou sans pages"
        )

    # ── Test 1 : PDF natif ? ──
    native_text = _extract_native_text(pdf_path, page_idx=0)
    char_count = len(native_text.strip())

    if char_count >= CLASSIFY_NATIVE_MIN_CHARS:
        # Vérification additionnelle : présence de chiffres et au moins 3 lignes
        lines = [l for l in native_text.split("\n") if l.strip()]
        has_digits = any(c.isdigit() for c in native_text)
        if len(lines) >= 3 and has_digits:
            logger.info(f"{pdf_path.name} → NATIF ({char_count} chars)")
            return ClassifyResult(
                doc_type="natif",
                native_text_chars=char_count,
                page_count=page_count,
            )
        # Texte natif mais pauvre → on tombera en scan_propre / scan_difficile
        logger.debug(
            f"{pdf_path.name} : texte natif présent mais peu structuré "
            f"({len(lines)} lignes, digits={has_digits})"
        )

    # ── Test 2 : scan, niveau de qualité OCR ──
    ocr_conf = _ocr_confidence(pdf_path, page_idx=0)
    if ocr_conf is None:
        return ClassifyResult(
            doc_type="scan_difficile",
            native_text_chars=char_count,
            page_count=page_count,
            error="Tesseract indisponible — type forcé en scan_difficile"
        )

    if ocr_conf >= CLASSIFY_OCR_PROPRE_MIN_CONFIDENCE:
        logger.info(f"{pdf_path.name} → SCAN_PROPRE (conf {ocr_conf:.1f}%)")
        return ClassifyResult(
            doc_type="scan_propre",
            native_text_chars=char_count,
            ocr_confidence=ocr_conf,
            page_count=page_count,
        )
    else:
        logger.info(f"{pdf_path.name} → SCAN_DIFFICILE (conf {ocr_conf:.1f}%)")
        return ClassifyResult(
            doc_type="scan_difficile",
            native_text_chars=char_count,
            ocr_confidence=ocr_conf,
            page_count=page_count,
        )
