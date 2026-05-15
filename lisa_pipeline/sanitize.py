"""Sanitize PDF — 3 passes de défense en profondeur (cf. spec §5).

Garantit qu'un PDF entrant Drive Inbox est safe avant Vision Flash :
- Pas de JS embarqué / actions cachées
- Pas de chiffrement / restrictions
- Pas de métadonnées sensibles
- Structure réparée si possible (linearize)
- Bornes taille / pages
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except ImportError:
    PYMUPDF_OK = False

from .config import (
    SANITIZE_MAX_FILE_SIZE_BYTES,
    SANITIZE_MAX_PAGES,
)


# Mots-clés JavaScript résiduels dans le contenu PDF post-sanitize
JS_RESIDUAL_KEYWORDS = (b"/JS", b"/JavaScript", b"/AA", b"/OpenAction")


@dataclass
class SanitizeResult:
    success: bool
    output_path: Optional[Path] = None
    sha256: Optional[str] = None
    page_count: int = 0
    file_size_bytes: int = 0
    duplicate_of: Optional[str] = None  # sha256 de l'original si doublon
    error_code: Optional[str] = None
    error_message: Optional[str] = None


# ============================================================================
# Helpers
# ============================================================================
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Wrapper subprocess avec timeout et capture."""
    return subprocess.run(
        cmd, capture_output=True, text=False, timeout=timeout, check=False
    )


def _has_js_residual(pdf_path: Path) -> bool:
    """Détecte les patterns JavaScript résiduels après qpdf."""
    try:
        with pdf_path.open("rb") as f:
            content = f.read()
    except OSError:
        return False
    return any(kw in content for kw in JS_RESIDUAL_KEYWORDS)


# ============================================================================
# Pipeline sanitize complet
# ============================================================================
def sanitize_pdf(input_pdf: Path, output_pdf: Path) -> SanitizeResult:
    """Pipeline complet sanitize 3 passes.

    Args:
        input_pdf : PDF brut depuis Drive Inbox
        output_pdf : destination sanitized (sera créé)

    Returns:
        SanitizeResult avec success/error_code/sha256
    """
    if not input_pdf.exists():
        return SanitizeResult(
            success=False, error_code="input_not_found",
            error_message=f"PDF input introuvable : {input_pdf}"
        )

    # ── PASSE 1 : garde-fous bornes ──
    file_size = input_pdf.stat().st_size
    if file_size > SANITIZE_MAX_FILE_SIZE_BYTES:
        return SanitizeResult(
            success=False, error_code="oversized",
            error_message=f"Fichier > {SANITIZE_MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB "
                          f"({file_size // (1024 * 1024)} MB)"
        )

    if PYMUPDF_OK:
        try:
            with fitz.open(str(input_pdf)) as doc:
                page_count = len(doc)
                if page_count > SANITIZE_MAX_PAGES:
                    return SanitizeResult(
                        success=False, error_code="oversized",
                        error_message=f"Trop de pages : {page_count} > {SANITIZE_MAX_PAGES}"
                    )
        except Exception as e:
            return SanitizeResult(
                success=False, error_code="qpdf_repair_failed",
                error_message=f"PyMuPDF ne peut pas ouvrir : {e}"
            )

    # ── PASSE 2 : qpdf ──
    tmp1 = Path(tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name)
    try:
        qpdf_cmd = [
            "qpdf",
            "--decrypt",
            "--object-streams=generate",
            "--remove-restrictions",
            "--linearize",
            "--warning-exit-0",  # warnings non bloquants
            str(input_pdf), str(tmp1),
        ]
        result = _run(qpdf_cmd, timeout=120)

        if result.returncode != 0:
            # Vérifier si c'est un problème de password
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore")
            if "password" in stderr.lower() or "encrypted" in stderr.lower():
                return SanitizeResult(
                    success=False, error_code="encrypted_unknown_password",
                    error_message=f"PDF chiffré, password requis : {stderr[:200]}"
                )
            return SanitizeResult(
                success=False, error_code="qpdf_repair_failed",
                error_message=f"qpdf exit {result.returncode} : {stderr[:200]}"
            )

        # ── PASSE 3 : exiftool ──
        exiftool_cmd = [
            "exiftool", "-all=", "-overwrite_original", str(tmp1),
        ]
        result = _run(exiftool_cmd, timeout=60)
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore")
            return SanitizeResult(
                success=False, error_code="exiftool_failed",
                error_message=f"exiftool exit {result.returncode} : {stderr[:200]}"
            )

        # ── PASSE 4 : validation post-sanitize ──
        if _has_js_residual(tmp1):
            return SanitizeResult(
                success=False, error_code="js_residual",
                error_message="JavaScript ou OpenAction résiduel détecté après sanitize"
            )

        if PYMUPDF_OK:
            try:
                with fitz.open(str(tmp1)) as doc:
                    if len(doc) == 0:
                        return SanitizeResult(
                            success=False, error_code="qpdf_repair_failed",
                            error_message="PDF post-sanitize sans pages"
                        )
                    # Test extraction texte p.1 ne lève pas
                    _ = doc[0].get_text("text")
                    page_count = len(doc)
            except Exception as e:
                return SanitizeResult(
                    success=False, error_code="qpdf_repair_failed",
                    error_message=f"PyMuPDF KO post-sanitize : {e}"
                )

        # ── Tout OK : déplace vers output_pdf ──
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp1), str(output_pdf))

        sha = _sha256_file(output_pdf)
        size = output_pdf.stat().st_size

        logger.success(
            f"Sanitize OK : {input_pdf.name} → {output_pdf.name} "
            f"({size} bytes, {page_count} pages, sha={sha[:12]}...)"
        )

        return SanitizeResult(
            success=True,
            output_path=output_pdf,
            sha256=sha,
            page_count=page_count,
            file_size_bytes=size,
        )

    except subprocess.TimeoutExpired:
        return SanitizeResult(
            success=False, error_code="timeout",
            error_message="qpdf ou exiftool ont dépassé le timeout"
        )
    except FileNotFoundError as e:
        return SanitizeResult(
            success=False, error_code="binary_missing",
            error_message=f"Binaire manquant : {e}"
        )
    finally:
        # Cleanup tmp si pas déplacé
        if tmp1.exists():
            try:
                tmp1.unlink()
            except OSError:
                pass
