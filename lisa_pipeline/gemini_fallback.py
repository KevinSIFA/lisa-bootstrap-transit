"""Fallback Gemini 3.1 Pro Vision avec prompt V6.1 — dernier recours.

Quand utilisé (cf. spec §11) :
- Réparation Opus a échoué (script généré ne valide pas sur samples)
- SEED Opus a échoué sur 1ère facture
- Document scan_difficile avec Tesseract confidence < 60%
- Fournisseur marqué non_calibrable

SDK : google-genai (nouveau, remplace google-cloud-aiplatform legacy).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from google import genai
    from google.oauth2 import service_account
    GENAI_OK = True
except ImportError:
    GENAI_OK = False

from .config import (
    GEMINI_PRO,
    GEMINI_THINKING_LEVEL,
    GOOGLE_CLOUD_PROJECT,
    GOOGLE_VERTEX_LOCATION,
    PROMPTS_DIR,
    get_google_service_account_path,
)
from .models import Invoice, LisaOutput


# Path du prompt système V6.1 (byte-stable, chargé en bytes pour caching implicit)
PROMPT_V6_1_PATH = PROMPTS_DIR / "lisa_gemini_v6_1.txt"


@dataclass
class GeminiFallbackResult:
    """Résultat extraction Gemini Pro fallback."""
    success: bool
    invoice: Optional[Invoice] = None
    output: Optional[LisaOutput] = None
    raw_response: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    duration_s: float = 0.0
    error: Optional[str] = None


# ============================================================================
# Helpers
# ============================================================================
def _init_genai_client():
    creds = service_account.Credentials.from_service_account_file(
        str(get_google_service_account_path()),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return genai.Client(
        vertexai=True,
        project=GOOGLE_CLOUD_PROJECT,
        location=GOOGLE_VERTEX_LOCATION,
        credentials=creds,
    )


def _load_v6_1_prompt() -> str:
    """Charge le prompt V6.1 en byte-stable (open en 'rb' puis decode).

    Pas de fallback intégré ici — le prompt V6.1 est trop long (~12K tokens)
    pour être hardcodé. Si le fichier manque, lever explicitement.
    """
    if not PROMPT_V6_1_PATH.exists():
        raise FileNotFoundError(
            f"Prompt V6.1 introuvable : {PROMPT_V6_1_PATH}\n"
            f"Le prompt doit être stocké en byte-stable dans ce fichier."
        )
    return PROMPT_V6_1_PATH.read_bytes().decode("utf-8")


def _extract_json_from_response(text: str) -> dict:
    """Extrait le JSON de la réponse Gemini (peut être wrappée en markdown)."""
    import json
    cleaned = text.strip()
    # Retire markdown backticks
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Trouve le premier { et le dernier } (robuste aux préambules)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Aucun JSON détecté dans la réponse Gemini")
    return json.loads(cleaned[start:end + 1])


# ============================================================================
# Entrée principale
# ============================================================================
def run_gemini_fallback(pdf_path: Path) -> GeminiFallbackResult:
    """Envoie le PDF à Gemini 3.1 Pro avec prompt V6.1 et parse la réponse.

    Args:
        pdf_path : PDF sanitized (depuis processing/)

    Returns:
        GeminiFallbackResult avec invoice/tokens/duration
    """
    if not GENAI_OK:
        return GeminiFallbackResult(
            success=False, error="google-genai SDK indisponible"
        )

    if not pdf_path.exists():
        return GeminiFallbackResult(
            success=False, error=f"PDF introuvable : {pdf_path}"
        )

    try:
        system_prompt = _load_v6_1_prompt()
    except FileNotFoundError as e:
        return GeminiFallbackResult(success=False, error=str(e))

    try:
        client = _init_genai_client()
    except Exception as e:
        return GeminiFallbackResult(
            success=False, error=f"Vertex init failed : {e}"
        )

    pdf_bytes = pdf_path.read_bytes()

    t0 = time.time()
    try:
        response = client.models.generate_content(
            model=GEMINI_PRO,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.0,
                "max_output_tokens": 8192,
                "response_mime_type": "application/json",
                "thinking_config": {"thinking_level": GEMINI_THINKING_LEVEL},
            },
            contents=[
                {"mime_type": "application/pdf", "data": pdf_bytes},
                "Extrais cette facture selon le schéma SYDONIA V6.1 strict.",
            ],
        )
    except Exception as e:
        return GeminiFallbackResult(
            success=False, error=f"Gemini Pro call failed : {e}",
            duration_s=time.time() - t0,
        )

    duration = time.time() - t0
    text = response.text if hasattr(response, "text") else ""
    usage = getattr(response, "usage_metadata", None)
    tokens_in = getattr(usage, "prompt_token_count", 0) if usage else 0
    tokens_out = getattr(usage, "candidates_token_count", 0) if usage else 0
    cached = getattr(usage, "cached_content_token_count", 0) if usage else 0

    # Parser le JSON
    try:
        data = _extract_json_from_response(text)
    except Exception as e:
        return GeminiFallbackResult(
            success=False, raw_response=text,
            tokens_in=tokens_in, tokens_out=tokens_out, cached_tokens=cached,
            duration_s=duration, error=f"JSON parse failed : {e}",
        )

    # Valider via Pydantic
    try:
        output = LisaOutput.model_validate(data)
    except Exception as e:
        return GeminiFallbackResult(
            success=False, raw_response=text,
            tokens_in=tokens_in, tokens_out=tokens_out, cached_tokens=cached,
            duration_s=duration,
            error=f"Validation schéma V6.1 KO : {e}",
        )

    if not output.invoices:
        return GeminiFallbackResult(
            success=False, raw_response=text,
            tokens_in=tokens_in, tokens_out=tokens_out, cached_tokens=cached,
            duration_s=duration, error="Gemini retourne 0 invoices",
        )

    invoice = output.invoices[0]
    logger.success(
        f"Gemini fallback OK : {pdf_path.name} en {duration:.1f}s "
        f"(in={tokens_in} cached={cached} out={tokens_out})"
    )

    return GeminiFallbackResult(
        success=True, invoice=invoice, output=output,
        raw_response=text,
        tokens_in=tokens_in, tokens_out=tokens_out, cached_tokens=cached,
        duration_s=duration,
    )
