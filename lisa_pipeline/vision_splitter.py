"""Vision split — Gemini 3 Flash, 1 prompt par PDF entrant.

Détecte :
- Si le doc est une facture (sinon quarantaine)
- Combien de factures distinctes
- Fournisseur + n° par facture
- Complétude (manque dernière page ?)

Sortie : split du PDF en N sous-PDF nommés FOURNISSEUR_N°FACTURE.pdf
Format de réponse Gemini attendu (cf. spec §6) :
  BERTRAND_EXPORT_FAC65447 (COMPLETE) Page 1 à 4
  AMAZON_AU_65425-001 (INCOMPLETE) Page 6 à 8
Ou flags : NOT_INVOICE | NO_PRODUCTS
"""
from __future__ import annotations

import re
import unicodedata
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
    from google import genai
    from google.oauth2 import service_account
    GENAI_OK = True
except ImportError:
    GENAI_OK = False

from .config import (
    GEMINI_FLASH,
    GOOGLE_CLOUD_PROJECT,
    GOOGLE_VERTEX_LOCATION,
    PROMPTS_DIR,
    PROCESSING_DIR,
    get_google_service_account_path,
)


# Path du prompt système Vision split (byte-stable, chargé en bytes)
VISION_SPLIT_PROMPT_PATH = PROMPTS_DIR / "vision_split.txt"


@dataclass
class InvoiceSpec:
    """Une facture détectée par Vision dans un PDF entrant."""
    name: str               # FOURNISSEUR_N°FACTURE normalisé
    pages: list[int]        # 1-indexed
    complete: bool
    output_pdf: Optional[Path] = None


@dataclass
class VisionSplitResult:
    """Résultat de l'étape Vision split."""
    status: str             # "split" | "quarantine_not_invoice" | "quarantine_no_products" | "quarantine_incomplete_all"
    invoices: list[InvoiceSpec] = field(default_factory=list)
    incomplete_invoices: list[InvoiceSpec] = field(default_factory=list)
    quarantine_reason: Optional[str] = None
    raw_response: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0


# ============================================================================
# Helpers : normalisation nom fichier
# ============================================================================
def _strip_accents(s: str) -> str:
    """Retire les accents Unicode (NFD strip combining marks)."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def normalize_supplier_for_filename(raw: str) -> str:
    """MAJUSCULES + retire accents + espaces → _. Ne touche pas au reste."""
    s = _strip_accents(raw).upper()
    s = re.sub(r"\s+", "_", s.strip())
    # Retire les caractères vraiment interdits dans un nom de fichier
    s = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", s)
    return s


def normalize_invoice_num_for_filename(raw: str) -> str:
    """N° facture : MAJUSCULES, espaces → _, accents retirés. Caractères spéciaux (/, -, .) PRÉSERVÉS."""
    s = _strip_accents(raw).upper()
    s = re.sub(r"\s+", "_", s.strip())
    # On garde slash/tiret/point/underscore, on retire seulement les vraiment dangereux
    s = re.sub(r"[<>:\"\\|?*\x00-\x1f]", "", s)
    return s


# ============================================================================
# Parsing de la réponse Gemini Flash
# ============================================================================
_LINE_RE = re.compile(
    r"^(?P<name>[A-Z0-9_./\-]+)\s+"
    r"\((?P<status>COMPLETE|INCOMPLETE)\)\s+"
    r"Page\s+(?P<start>\d+)(?:\s+à\s+(?P<end>\d+))?",
    re.IGNORECASE,
)


def parse_vision_response(text: str) -> VisionSplitResult:
    """Parse la réponse texte de Gemini Flash en VisionSplitResult.

    La réponse attendue :
        NOT_INVOICE
    Ou :
        NO_PRODUCTS
    Ou :
        BERTRAND_EXPORT_FAC65447 (COMPLETE) Page 1 à 4
        AMAZON_AU_65425-001 (INCOMPLETE) Page 6 à 8
    """
    result = VisionSplitResult(status="split", raw_response=text)
    cleaned = text.strip()

    # Flags absolus
    if cleaned.upper().startswith("NOT_INVOICE"):
        result.status = "quarantine_not_invoice"
        result.quarantine_reason = "Vision Flash : ce document n'est pas une facture"
        return result
    if cleaned.upper().startswith("NO_PRODUCTS"):
        result.status = "quarantine_no_products"
        result.quarantine_reason = "Vision Flash : facture sans détail produits"
        return result

    # Sinon, parser les lignes
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            logger.warning(f"Ligne Vision Flash non parsée : {line!r}")
            continue
        name = m.group("name").strip()
        status_str = m.group("status").upper()
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        pages = list(range(start, end + 1))
        spec = InvoiceSpec(
            name=name,
            pages=pages,
            complete=(status_str == "COMPLETE"),
        )
        if spec.complete:
            result.invoices.append(spec)
        else:
            result.incomplete_invoices.append(spec)

    # Décision finale
    if not result.invoices and not result.incomplete_invoices:
        result.status = "quarantine_not_invoice"
        result.quarantine_reason = "Vision Flash : réponse incompréhensible"
    elif not result.invoices:
        # Toutes les factures du doc sont INCOMPLETE
        result.status = "quarantine_incomplete_all"
        result.quarantine_reason = "Toutes les factures détectées sont incomplètes"

    return result


# ============================================================================
# Split physique du PDF
# ============================================================================
def split_pdf_by_pages(
    source_pdf: Path, invoices: list[InvoiceSpec], output_dir: Path,
) -> list[InvoiceSpec]:
    """Découpe le PDF source en N sous-PDF selon les pages spécifiées.

    Retourne les InvoiceSpec enrichis avec output_pdf rempli.
    """
    if not PYMUPDF_OK:
        raise RuntimeError("PyMuPDF requis pour split PDF")

    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(source_pdf)) as src:
        for spec in invoices:
            output_pdf = output_dir / f"{spec.name}.pdf"
            new_doc = fitz.open()
            for p in spec.pages:
                # PDF pages 1-indexed dans Vision, 0-indexed dans PyMuPDF
                page_idx = p - 1
                if 0 <= page_idx < len(src):
                    new_doc.insert_pdf(src, from_page=page_idx, to_page=page_idx)
            new_doc.save(str(output_pdf))
            new_doc.close()
            spec.output_pdf = output_pdf
            logger.info(f"Split : {spec.name}.pdf ({len(spec.pages)} pages)")

    return invoices


# ============================================================================
# Appel Gemini Flash
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


def _load_vision_prompt() -> str:
    """Charge le prompt système Vision split en bytes-stable.

    Si le fichier n'existe pas, retourne un prompt de fallback intégré.
    """
    if VISION_SPLIT_PROMPT_PATH.exists():
        return VISION_SPLIT_PROMPT_PATH.read_bytes().decode("utf-8")

    # Prompt de fallback intégré (recommandé : externaliser dans prompts/ pour byte-stability)
    return """Tu analyses un PDF qui peut contenir une ou plusieurs factures commerciales.

Pour ce document, indique :
- Le nombre de factures distinctes
- Pour chaque facture : nom fournisseur, numéro de facture, plage de pages, complétude

Réponse au format strict, une ligne par facture :
<FOURNISSEUR>_<N°FACTURE> (COMPLETE|INCOMPLETE) Page <X> à <Y>

Règles :
- FOURNISSEUR en MAJUSCULES, espaces remplacés par _, accents retirés
- N°FACTURE : tel qu'imprimé, caractères spéciaux (/, -, .) préservés
- COMPLETE = total HT lisible + au moins 1 ligne produit + cohérence pagination
- INCOMPLETE = manque total / manque dernière page / pas de ligne produit
- Si le document n'est PAS une facture : réponds uniquement NOT_INVOICE
- Si le document est une facture mais sans aucun produit détaillé : NO_PRODUCTS

Exemples :
  BERTRAND_EXPORT_FAC65447 (COMPLETE) Page 1 à 4
  AMAZON_AU_65425-001 (INCOMPLETE) Page 6 à 8
"""


def run_vision_split(pdf_path: Path, output_dir: Optional[Path] = None) -> VisionSplitResult:
    """Pipeline Vision split complet.

    Args:
        pdf_path : PDF sanitized
        output_dir : où placer les PDF splittés (défaut : PROCESSING_DIR)

    Returns:
        VisionSplitResult avec status + invoices + raw_response
    """
    if output_dir is None:
        output_dir = PROCESSING_DIR

    if not GENAI_OK:
        return VisionSplitResult(
            status="quarantine_not_invoice",
            quarantine_reason="google-genai SDK indisponible",
        )

    if not pdf_path.exists():
        return VisionSplitResult(
            status="quarantine_not_invoice",
            quarantine_reason=f"PDF introuvable : {pdf_path}",
        )

    client = _init_genai_client()
    system_prompt = _load_vision_prompt()

    pdf_bytes = pdf_path.read_bytes()

    try:
        response = client.models.generate_content(
            model=GEMINI_FLASH,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.0,
            },
            contents=[
                {"mime_type": "application/pdf", "data": pdf_bytes},
                "Analyse ce document selon le format de réponse strict.",
            ],
        )
    except Exception as e:
        logger.error(f"Gemini Flash call failed sur {pdf_path.name}: {e}")
        return VisionSplitResult(
            status="quarantine_not_invoice",
            quarantine_reason=f"Erreur appel Gemini Flash : {e}",
        )

    text = response.text if hasattr(response, "text") else ""
    usage = getattr(response, "usage_metadata", None)
    tokens_in = getattr(usage, "prompt_token_count", 0) if usage else 0
    tokens_out = getattr(usage, "candidates_token_count", 0) if usage else 0
    cached = getattr(usage, "cached_content_token_count", 0) if usage else 0

    result = parse_vision_response(text)
    result.tokens_in = tokens_in
    result.tokens_out = tokens_out
    result.cached_tokens = cached

    # Si on a au moins une facture COMPLETE → split physique
    if result.status == "split" and result.invoices:
        try:
            split_pdf_by_pages(pdf_path, result.invoices, output_dir)
        except Exception as e:
            logger.error(f"Split physique échoué sur {pdf_path.name}: {e}")
            return VisionSplitResult(
                status="quarantine_not_invoice",
                quarantine_reason=f"Split PDF échoué : {e}",
                raw_response=text, tokens_in=tokens_in,
                tokens_out=tokens_out, cached_tokens=cached,
            )

    logger.success(
        f"Vision split {pdf_path.name} → status={result.status} "
        f"complete={len(result.invoices)} incomplete={len(result.incomplete_invoices)} "
        f"(tokens in={tokens_in} cached={cached} out={tokens_out})"
    )
    return result
