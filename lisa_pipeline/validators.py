"""Validation des extractions LISA — math + cohérence V6.1.

Le validateur math est CRITIQUE : il détermine si une facture passe le filtre
"script déterministe a réussi" ou si on doit réparer / fallback Gemini.

Règle absolue : Σ(line.amount des ProductLine) + xfee.amount == total_ht
(à VALIDATE_MATH_TOLERANCE_RATIO % près ou VALIDATE_MATH_TOLERANCE_ABS € en absolu).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from .config import (
    VALIDATE_MATH_TOLERANCE_ABS,
    VALIDATE_MATH_TOLERANCE_RATIO,
)
from .models import Invoice, LisaOutput, ProductLine, XfeeLine


@dataclass
class ValidationResult:
    """Résultat d'une validation. ok==True ssi aucune erreur bloquante."""

    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    math_check: Optional[dict] = None  # détails du math check (somme, expected, delta)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.success = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ============================================================================
# Parsing des montants français (virgule)
# ============================================================================
def parse_amount(s: str) -> Decimal:
    """Convertit '1856,72' → Decimal('1856.72'). Lève si invalide."""
    if not isinstance(s, str):
        raise InvalidOperation(f"montant attendu en string, reçu {type(s).__name__}")
    cleaned = s.replace(",", ".").strip()
    return Decimal(cleaned)


# ============================================================================
# Validation math (la règle d'or)
# ============================================================================
def validate_math(invoice: Invoice) -> dict:
    """Vérifie Σ(ProductLine.amount) + XfeeLine.amount == header.total_ht.

    Retourne un dict avec les détails du check :
        {ok: bool, sum_products: str, xfee: str, expected: str, delta: str, tolerance_used: str}
    """
    try:
        total_ht = parse_amount(invoice.header.total_ht)
    except InvalidOperation as e:
        return {
            "ok": False, "error": f"total_ht invalide : {e}",
            "sum_products": None, "xfee": None, "expected": None, "delta": None,
        }

    sum_products = Decimal(0)
    xfee_amount = Decimal(0)
    for line in invoice.lines:
        try:
            amount = parse_amount(line.amount)
        except InvalidOperation as e:
            return {
                "ok": False, "error": f"montant ligne invalide : {e}",
                "sum_products": None, "xfee": None, "expected": None, "delta": None,
            }
        if isinstance(line, XfeeLine):
            xfee_amount = amount
        elif isinstance(line, ProductLine):
            sum_products += amount

    computed = sum_products + xfee_amount
    delta = abs(computed - total_ht)
    tolerance = max(
        total_ht * Decimal(str(VALIDATE_MATH_TOLERANCE_RATIO)),
        Decimal(str(VALIDATE_MATH_TOLERANCE_ABS)),
    )
    ok = delta <= tolerance

    return {
        "ok": ok,
        "sum_products": str(sum_products),
        "xfee": str(xfee_amount),
        "computed": str(computed),
        "expected": str(total_ht),
        "delta": str(delta),
        "tolerance_used": str(tolerance),
    }


# ============================================================================
# Validation complétude
# ============================================================================
def validate_completeness(invoice: Invoice) -> list[str]:
    """Vérifie la complétude métier d'une facture (au-delà du schéma Pydantic).
    Retourne la liste des warnings (pas bloquants).
    """
    warnings: list[str] = []

    if not invoice.header.recipient.strip():
        warnings.append("header.recipient vide")
    if not invoice.header.supplier.strip():
        warnings.append("header.supplier vide")

    product_lines = [l for l in invoice.lines if isinstance(l, ProductLine)]
    if not product_lines:
        warnings.append("Aucune ProductLine (uniquement XFEE)")

    # Vérif unit_price × qty ≈ amount pour chaque ProductLine (tolérance 1 % ou 1 cent)
    for i, line in enumerate(product_lines):
        if line.unit_price is None:
            continue
        try:
            up = parse_amount(line.unit_price)
            amt = parse_amount(line.amount)
            expected = up * Decimal(line.qty)
            if expected > 0:
                delta_ratio = abs(expected - amt) / expected
                if delta_ratio > Decimal("0.01") and abs(expected - amt) > Decimal("0.01"):
                    warnings.append(
                        f"Line {i} ({line.ref}) : unit_price × qty = {expected}, "
                        f"mais amount = {amt} (delta {abs(expected - amt)})"
                    )
        except InvalidOperation:
            warnings.append(f"Line {i} : montants invalides")

    # Vérif date pas dans le futur, pas trop ancienne
    # (sera implémenté si nécessaire — pour l'instant on trust le schéma Pydantic)

    return warnings


# ============================================================================
# Validation cohérence inter-lignes
# ============================================================================
def validate_consistency(invoice: Invoice) -> list[str]:
    """Vérifie la cohérence inter-lignes. Retourne warnings."""
    warnings: list[str] = []

    product_lines = [l for l in invoice.lines if isinstance(l, ProductLine)]

    # Origines dupliquées non cohérentes (rare mais possible)
    refs_seen: dict[str, str] = {}  # ref → origin
    for line in product_lines:
        if line.ref in refs_seen and refs_seen[line.ref] != line.origin:
            warnings.append(
                f"Réf {line.ref} apparaît avec origines différentes : "
                f"{refs_seen[line.ref]} vs {line.origin}"
            )
        refs_seen[line.ref] = line.origin

    # Si dof présent, mention "préférentielle" attendue (mais V6.1 fait déjà cette vérif amont)
    if invoice.header.dof:
        dof_lower = invoice.header.dof.lower()
        if not any(kw in dof_lower for kw in ("préférentiel", "preferential", "präferenz")):
            warnings.append(
                "header.dof présent mais ne contient pas le noyau 'préférentiel*' "
                "(non bloquant, mais suspect)"
            )

    return warnings


# ============================================================================
# Entrée principale
# ============================================================================
def validate(output: LisaOutput) -> ValidationResult:
    """Valide un LisaOutput complet (toutes les factures).

    Retourne un ValidationResult agrégé. Si success=False, la facture doit
    être réparée ou envoyée en fallback. Les warnings sont non bloquants.
    """
    result = ValidationResult(success=True)

    if not output.invoices:
        result.add_error("LisaOutput.invoices vide")
        return result

    for idx, invoice in enumerate(output.invoices):
        # Math (bloquant)
        math = validate_math(invoice)
        if idx == 0:
            result.math_check = math
        if not math["ok"]:
            result.add_error(
                f"Invoice {idx} ({invoice.header.num}) : math KO — "
                f"computed={math.get('computed')} expected={math.get('expected')} "
                f"delta={math.get('delta')}"
            )

        # Complétude (warnings)
        for w in validate_completeness(invoice):
            result.add_warning(f"Invoice {idx} : {w}")

        # Cohérence (warnings)
        for w in validate_consistency(invoice):
            result.add_warning(f"Invoice {idx} : {w}")

    return result


# ============================================================================
# Comparaison sortie script vs JSON golden (pour test rolling-window)
# ============================================================================
def compare_against_golden(
    extracted: LisaOutput, golden: LisaOutput,
) -> ValidationResult:
    """Compare une sortie extraite vs un JSON de référence.

    Utilisé quand on teste un script réparé sur les 5 samples : on vérifie que
    chaque sample produit toujours le même JSON qu'avant.

    Retourne un ValidationResult avec les écarts détaillés.
    """
    result = ValidationResult(success=True)

    if len(extracted.invoices) != len(golden.invoices):
        result.add_error(
            f"Nombre de factures différent : extracted={len(extracted.invoices)} "
            f"vs golden={len(golden.invoices)}"
        )
        return result

    for idx, (ext, gold) in enumerate(zip(extracted.invoices, golden.invoices)):
        # Comparaison header champ par champ
        ext_h = ext.header.model_dump(exclude_none=True)
        gold_h = gold.header.model_dump(exclude_none=True)

        # Champs clés DOIVENT correspondre exactement
        for key in ("num", "supplier", "total_ht", "dossier", "date"):
            if ext_h.get(key) != gold_h.get(key):
                result.add_error(
                    f"Invoice {idx} : header.{key} diff "
                    f"extracted={ext_h.get(key)!r} golden={gold_h.get(key)!r}"
                )

        # Comparaison lines : nombre + sum amounts
        if len(ext.lines) != len(gold.lines):
            result.add_warning(
                f"Invoice {idx} : nb lines diff ({len(ext.lines)} vs {len(gold.lines)})"
            )

        try:
            ext_sum = sum(parse_amount(l.amount) for l in ext.lines)
            gold_sum = sum(parse_amount(l.amount) for l in gold.lines)
            if abs(ext_sum - gold_sum) > Decimal("0.05"):
                result.add_error(
                    f"Invoice {idx} : Σ(amounts) diff "
                    f"extracted={ext_sum} golden={gold_sum}"
                )
        except InvalidOperation as e:
            result.add_error(f"Invoice {idx} : montants invalides ({e})")

    return result
