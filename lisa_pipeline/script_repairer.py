"""Script repairer — Opus 4.7 répare ou seed un script catalogue.

Deux modes :
1. SEED : 1ère facture d'un fournisseur, pas de script existant → Opus génère un draft
2. REPAIR : script existant échoue sur facture courante → Opus le répare sur
            rolling window 5 (facture courante + 4 précédentes du même type)

Caching Anthropic exploité au max (cf. docs/prompt_caching_05_2026.md) :
- breakpoint #1 : system prompt stable (instructions calibration)
- breakpoint #2 : 5e document de l'input (rolling window, change peu)
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import anthropic
    ANTHROPIC_OK = True
except ImportError:
    ANTHROPIC_OK = False

from .catalogue import (
    list_samples, register_method, samples_dir, supplier_dir,
)
from .config import (
    ANTHROPIC_CACHE_TTL_OPUS,
    ANTHROPIC_OPUS,
    get_anthropic_api_key,
)
from .grimoire import query_lessons
from .models import LisaOutput
from .script_runner import run_script
from .validators import compare_against_golden, validate


@dataclass
class RepairResult:
    """Résultat d'une réparation/seed Opus."""
    success: bool
    script_code: Optional[str] = None
    script_version: Optional[str] = None
    error: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    tests_passed: int = 0
    tests_total: int = 0
    fallback_required: bool = False  # True si Opus a échoué → Gemini fallback


# ============================================================================
# Prompts système
# ============================================================================
SYSTEM_PROMPT_REPAIR = """Tu es un générateur expert de scripts Python d'extraction de factures
douanières au schéma SYDONIA V6.1 pour le Groupe SIFA en Nouvelle-Calédonie.

Tu produis du code Python DÉTERMINISTE utilisant :
- des positions strictes (x/y) via PyMuPDF
- des regex précises
- des positions de blocs de mots
- l'OCR la plus adaptée au type de document (Tesseract --psm 6, paramètres optimisés)

CONTRAINTES :
- Le script DOIT marcher sur les 5 (ou N selon ce qu'on te donne) factures samples fournies
- Le script DOIT retourner un dict Python conforme au schéma V6.1 strict :
    {"invoices": [{"header": {...}, "lines": [{...}, ..., XfeeLine]}]}
- Le script DOIT exposer une fonction `extract(pdf_path: pathlib.Path) -> dict`
- Imports allowlistés UNIQUEMENT : fitz (PyMuPDF), pytesseract, PIL, cv2, pandas, re, pathlib, decimal
- INTERDITS : os.system, subprocess, requests, eval, exec, open() en écriture
- La math DOIT vérifier : Σ(line.amount) + xfee.amount == total_ht (tolérance 1%)
- XfeeLine TOUJOURS en dernier dans lines, présent même si amount="0,00"
- Decimaux toujours avec virgule (ex "1856,72"), pas de point
- origin ISO 3166-1 alpha-2 majuscules (ex "FR", "DE/NP")
- JAMAIS "EU"/"UE"/"CEE"/"EEC" dans origin

INPUT que tu reçois :
- Le script actuel (Python, version v_n) — peut être vide si SEED
- N factures samples (PDF en base64) + leur JSON golden attendu (le script doit reproduire ces JSON)
- Le contexte grimoire : business_rules + extraction_quirks + ocr_method spécifiques au fournisseur

OUTPUT attendu :
Retourne UNIQUEMENT le script Python complet entre balises <script>...</script>.
Pas d'explication, pas de markdown, pas de commentaire hors balises."""


# ============================================================================
# Helpers
# ============================================================================
def _read_pdf_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _extract_script_from_response(text: str) -> Optional[str]:
    """Extrait le code Python entre <script>...</script>."""
    m = re.search(r"<script>(.*?)</script>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback : si Opus a oublié les balises, chercher un bloc Python plausible
    if "def extract(" in text:
        return text.strip()
    return None


def _grimoire_context_str(supplier_slug: str, doc_type: str) -> str:
    """Récupère et formate le contexte grimoire pour le prompt Opus."""
    lessons = query_lessons(
        supplier_slug=supplier_slug,
        doc_type=doc_type,
        categories=["business_rule", "extraction_quirk", "ocr_method"],
        top_k=10,
    )
    if not lessons:
        return "(aucune leçon enregistrée pour ce fournisseur)"
    lines = []
    for l in lessons:
        lines.append(f"- [{l['category']}] {l['content']}")
    return "\n".join(lines)


def _current_script_str(supplier_slug: str, doc_type: str) -> str:
    """Lit le script actuel s'il existe."""
    from .catalogue import script_path_for
    p = script_path_for(supplier_slug, doc_type)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "(aucun script existant — mode SEED)"


# ============================================================================
# Appel Opus avec caching
# ============================================================================
def _call_opus(
    supplier_slug: str, doc_type: str,
    samples: list[Path], current_script: str, mode: str,
) -> tuple[Optional[str], dict]:
    """Appelle Opus 4.7 avec 2 breakpoints de caching.

    Returns:
        (script_code | None, usage_metadata)
    """
    if not ANTHROPIC_OK:
        return None, {"error": "anthropic SDK indisponible"}

    client = anthropic.Anthropic(api_key=get_anthropic_api_key())

    # Construction du user message : N documents PDF + le script actuel + grimoire + prompt
    user_content: list[dict] = []
    for i, sample in enumerate(samples):
        item = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": _read_pdf_b64(sample),
            },
        }
        # Breakpoint #2 sur le DERNIER document (rolling window change peu de fois par jour)
        if i == len(samples) - 1:
            item["cache_control"] = {"type": "ephemeral", "ttl": ANTHROPIC_CACHE_TTL_OPUS}
        user_content.append(item)

    grimoire_ctx = _grimoire_context_str(supplier_slug, doc_type)

    user_content.append({
        "type": "text",
        "text": (
            f"MODE: {mode}\n"
            f"FOURNISSEUR: {supplier_slug}\n"
            f"TYPE DE DOCUMENT: {doc_type}\n\n"
            f"SCRIPT ACTUEL :\n```python\n{current_script}\n```\n\n"
            f"CONTEXTE GRIMOIRE :\n{grimoire_ctx}\n\n"
            f"Génère un script Python qui marche pour TOUS les samples fournis."
        ),
    })

    try:
        response = client.messages.create(
            model=ANTHROPIC_OPUS,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT_REPAIR,
                    "cache_control": {"type": "ephemeral", "ttl": ANTHROPIC_CACHE_TTL_OPUS},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        logger.error(f"Opus call failed pour {supplier_slug}/{doc_type}: {e}")
        return None, {"error": str(e)}

    # Extract usage
    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
    }

    # Extract script
    text = response.content[0].text if response.content else ""
    script_code = _extract_script_from_response(text)
    return script_code, usage


# ============================================================================
# Entrée principale : seed ou repair
# ============================================================================
def seed_or_repair(
    supplier_slug: str, doc_type: str,
    new_invoice_pdf: Path,
) -> RepairResult:
    """SEED si pas de script existant, REPAIR sinon.

    Args:
        supplier_slug : slug fournisseur
        doc_type : "natif"|"scan_propre"|"scan_difficile"
        new_invoice_pdf : la facture courante (celle qui a déclenché)

    Returns:
        RepairResult avec success/script_code/tests
    """
    current_script = _current_script_str(supplier_slug, doc_type)
    is_seed = (current_script == "(aucun script existant — mode SEED)")
    mode = "SEED" if is_seed else "REPAIR"

    # Récupérer samples
    samples = list_samples(supplier_slug, doc_type)
    # Ajouter la nouvelle facture en fin (rolling window)
    samples_to_include = samples + [new_invoice_pdf]
    # Garder maximum 5
    samples_to_include = samples_to_include[-5:]

    if is_seed and len(samples_to_include) == 1:
        logger.info(f"SEED {supplier_slug}/{doc_type} : 1 sample seulement")
    elif is_seed:
        logger.info(f"SEED {supplier_slug}/{doc_type} : {len(samples_to_include)} samples")
    else:
        logger.info(f"REPAIR {supplier_slug}/{doc_type} sur {len(samples_to_include)} samples")

    script_code, usage = _call_opus(
        supplier_slug, doc_type, samples_to_include, current_script, mode,
    )

    if not script_code:
        err = usage.get("error", "Opus n'a pas retourné de script")
        return RepairResult(
            success=False, error=err, fallback_required=True,
        )

    # Tester sur tous les samples
    # Étape 1 : enregistrer le script comme candidat dans un emplacement tmp
    # Étape 2 : pour chaque sample, l'exécuter et comparer math vs golden
    from .catalogue import meta_path, load_meta
    tmp_script = supplier_dir(supplier_slug) / f".tmp_method_{doc_type}.py"
    tmp_script.parent.mkdir(parents=True, exist_ok=True)
    tmp_script.write_text(script_code, encoding="utf-8")

    tests_passed = 0
    tests_total = len(samples_to_include)
    try:
        for sample_pdf in samples_to_include:
            # On charge directement le script tmp sans passer par catalogue/runtime
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"lisa_tmp_{supplier_slug}_{doc_type}", str(tmp_script)
            )
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                raw = mod.extract(sample_pdf)
                output = LisaOutput.model_validate(raw)
                result = validate(output)
                if result.success:
                    tests_passed += 1
                else:
                    logger.warning(
                        f"Test {sample_pdf.name} KO : {result.errors[0] if result.errors else 'unknown'}"
                    )
            except Exception as e:
                logger.warning(f"Test {sample_pdf.name} crashé : {e}")
    finally:
        if tmp_script.exists():
            tmp_script.unlink()

    success = (tests_passed == tests_total)

    if success:
        # Commit du script comme nouvelle version
        meta = load_meta(supplier_slug)
        old_version = meta.methods.get(doc_type, {}).get("version", "v0")
        try:
            new_version = f"v{int(old_version.lstrip('v')) + 1}"
        except ValueError:
            new_version = "v1"
        register_method(supplier_slug, doc_type, script_code, version=new_version)
        logger.success(
            f"{mode} OK pour {supplier_slug}/{doc_type} version {new_version} "
            f"({tests_passed}/{tests_total} tests)"
        )
    else:
        logger.error(
            f"{mode} KO pour {supplier_slug}/{doc_type} "
            f"({tests_passed}/{tests_total} tests) — fallback Gemini requis"
        )

    return RepairResult(
        success=success,
        script_code=script_code if success else None,
        script_version=new_version if success else None,
        tokens_in=usage.get("input_tokens", 0),
        tokens_out=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_create_tokens=usage.get("cache_creation_input_tokens", 0),
        tests_passed=tests_passed,
        tests_total=tests_total,
        fallback_required=not success,
    )
