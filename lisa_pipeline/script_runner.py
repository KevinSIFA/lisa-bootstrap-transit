"""Script runner — charge un script Python catalogue, l'exécute sur un PDF, valide la math.

Le script catalogue est un module Python qui expose une fonction `extract(pdf_path)`
retournant un dict conforme au schéma V6.1 (header + lines).

Le runner :
1. Charge le module via importlib (chemin /opt/lisa/catalogue/<slug>/method_<type>.py)
2. Appelle extract(pdf_path)
3. Valide via Pydantic LisaOutput
4. Valide math via validators.validate_math
5. Retourne ScriptRunResult avec success/invoice/error
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from .catalogue import (
    record_result, script_path_for, supplier_dir,
)
from .config import CATALOGUE_DIR
from .models import Invoice, LisaOutput
from .validators import ValidationResult, validate, validate_math


@dataclass
class ScriptRunResult:
    """Résultat d'une exécution de script catalogue."""
    success: bool
    invoice: Optional[Invoice] = None
    output: Optional[LisaOutput] = None
    error: Optional[str] = None
    math_check: Optional[dict] = None
    script_path: Optional[Path] = None
    script_version: Optional[str] = None
    needs_repair: bool = False
    needs_seed: bool = False  # True si aucun script existant pour ce (slug, type)


# ============================================================================
# Chargement dynamique du script
# ============================================================================
def _load_script_module(script_path: Path):
    """Charge un module Python depuis un chemin donné. Le module DOIT exposer extract(pdf_path)."""
    spec = importlib.util.spec_from_file_location(
        f"lisa_script_{script_path.stem}", str(script_path)
    )
    if not spec or not spec.loader:
        raise ImportError(f"Impossible de charger {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "extract"):
        raise AttributeError(
            f"Script {script_path.name} doit exposer une fonction extract(pdf_path)"
        )
    return module


# ============================================================================
# Entrée principale : applique un script catalogue
# ============================================================================
def run_script(
    pdf_path: Path,
    supplier_slug: str,
    doc_type: str,
    record_outcome: bool = True,
) -> ScriptRunResult:
    """Charge le script (supplier_slug, doc_type) et l'applique au PDF.

    Args:
        pdf_path : PDF sanitized à extraire
        supplier_slug : slug catalogue du fournisseur
        doc_type : "natif" | "scan_propre" | "scan_difficile"
        record_outcome : si True, met à jour catalogue rolling_window/health

    Returns:
        ScriptRunResult avec success/invoice/needs_repair/needs_seed
    """
    script_path = script_path_for(supplier_slug, doc_type)

    if not script_path.exists():
        # Aucun script → seed nécessaire
        return ScriptRunResult(
            success=False,
            error=f"Pas de script pour ({supplier_slug}, {doc_type}) — seed à générer",
            needs_seed=True,
        )

    # Charger et exécuter
    try:
        module = _load_script_module(script_path)
        raw_output = module.extract(pdf_path)
    except Exception as e:
        logger.error(f"Script {script_path.name} a planté sur {pdf_path.name}: {e}")
        if record_outcome:
            record_result(supplier_slug, doc_type, success=False, confidence=0.0)
        return ScriptRunResult(
            success=False,
            error=f"Script crashé : {e}",
            script_path=script_path,
            needs_repair=True,
        )

    # Valider via Pydantic
    try:
        output = LisaOutput.model_validate(raw_output)
    except Exception as e:
        logger.warning(f"Sortie script {script_path.name} non conforme V6.1: {e}")
        if record_outcome:
            record_result(supplier_slug, doc_type, success=False, confidence=0.2)
        return ScriptRunResult(
            success=False,
            error=f"Validation schéma V6.1 KO : {e}",
            script_path=script_path,
            needs_repair=True,
        )

    # Valider math sur la 1re invoice (LISA pipeline = 1 facture par PDF post-split)
    if not output.invoices:
        if record_outcome:
            record_result(supplier_slug, doc_type, success=False, confidence=0.0)
        return ScriptRunResult(
            success=False,
            error="Sortie sans factures",
            script_path=script_path,
            needs_repair=True,
        )

    invoice = output.invoices[0]
    math = validate_math(invoice)
    if not math["ok"]:
        logger.warning(
            f"Math KO pour {pdf_path.name} avec {script_path.name} : "
            f"computed={math.get('computed')} expected={math.get('expected')} "
            f"delta={math.get('delta')}"
        )
        if record_outcome:
            record_result(supplier_slug, doc_type, success=False, confidence=0.3)
        return ScriptRunResult(
            success=False,
            invoice=invoice,
            output=output,
            math_check=math,
            error=f"Math KO : delta={math.get('delta')}",
            script_path=script_path,
            needs_repair=True,
        )

    # Tout OK
    logger.success(
        f"Script {script_path.name} OK sur {pdf_path.name} "
        f"(supplier={supplier_slug}, type={doc_type})"
    )
    if record_outcome:
        record_result(supplier_slug, doc_type, success=True, confidence=1.0)

    return ScriptRunResult(
        success=True,
        invoice=invoice,
        output=output,
        math_check=math,
        script_path=script_path,
    )


# ============================================================================
# Essayer tous les scripts d'un fournisseur (cas Z5 — avant Opus)
# ============================================================================
def try_all_supplier_scripts(
    pdf_path: Path,
    supplier_slug: str,
    excluded_types: Optional[set[str]] = None,
) -> Optional[ScriptRunResult]:
    """Avant de lancer Opus, tente tous les scripts existants du fournisseur
    (sauf ceux dans excluded_types). Si l'un d'eux marche, c'est probablement
    une erreur de classification de type.

    Returns:
        ScriptRunResult success=True si un script marche, None sinon.
    """
    excluded_types = excluded_types or set()
    sdir = supplier_dir(supplier_slug)
    if not sdir.exists():
        return None

    for script_file in sdir.glob("method_*.py"):
        # Extraire le type depuis le nom du fichier method_natif.py → "natif"
        try:
            doc_type = script_file.stem.replace("method_", "")
        except Exception:
            continue
        if doc_type in excluded_types:
            continue

        logger.info(f"Tentative script alternatif ({doc_type}) pour {pdf_path.name}")
        result = run_script(pdf_path, supplier_slug, doc_type, record_outcome=False)
        if result.success:
            logger.success(
                f"Script alternatif {doc_type} fonctionne ! "
                f"(évite réparation Opus)"
            )
            return result
    return None
