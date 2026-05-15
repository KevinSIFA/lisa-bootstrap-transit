"""Schémas Pydantic V6.1 — sortie unifiée LISA.

Schéma cible : {"invoices": [{"header": {...}, "lines": [{...}, ...]}]}
Aligné sur le prompt Gemini V6.1 (26 colonnes mappées vers SYDONIA CSV).

Règles clés :
- valeurs string mono-ligne (RFC 8259)
- decimaux toujours avec virgule (ex "1856,72"), JAMAIS point
- codes pays ISO 3166-1 alpha-2 majuscules (ex "FR", "DE/NP", "CN")
- "EU"/"UE"/"CEE"/"EEC" INTERDITS → résolution vers ISO2 du pays expéditeur
- champs optionnels OMIS si vides (jamais "" ni null)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================================
# Helpers validation
# ============================================================================

_DECIMAL_COMMA_RE = re.compile(r"^-?\d{1,12}(,\d{1,3})?$")
_HS_CODE_RE = re.compile(r"^\d{6,10}$")
_ISO2_RE = re.compile(r"^[A-Z]{2}(/NP)?$")
_ISO2_COMPOSED_RE = re.compile(r"^[A-Z]{2}(/[A-Z]{2}|/NP)?$")
_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EU_BANNED = {"EU", "UE", "CEE", "EEC", "EUROPEAN UNION"}


def _validate_decimal_comma(v: str, field_name: str) -> str:
    """Vérifie format decimal avec virgule (pas de point)."""
    if not isinstance(v, str):
        raise ValueError(f"{field_name} doit être une string, pas {type(v).__name__}")
    v = v.strip()
    if not _DECIMAL_COMMA_RE.match(v):
        raise ValueError(
            f"{field_name}={v!r} invalide : décimal avec virgule attendu (ex '1856,72')"
        )
    return v


def _validate_no_control_chars(v: str, field_name: str) -> str:
    """Vérifie qu'une valeur string est mono-ligne sans caractères de contrôle."""
    if "\n" in v or "\r" in v or "\t" in v:
        raise ValueError(f"{field_name} contient saut de ligne ou tabulation")
    if any(ord(c) < 0x20 for c in v):
        raise ValueError(f"{field_name} contient des caractères de contrôle")
    return v.strip()


# ============================================================================
# Header
# ============================================================================

class Header(BaseModel):
    """En-tête facture — règles V6.1."""

    # ── Identification (obligatoires) ──
    date: str = Field(..., description="Format ISO AAAA-MM-JJ")
    num: str = Field(..., description="Numéro facture, caractères spéciaux préservés")
    supplier: str = Field(..., description="Nom fournisseur MAJUSCULES")
    recipient: str = Field(..., description="Destinataire SIFA MAJUSCULES")
    total_ht: str = Field(..., description="Total HT format virgule ex '1856,72'")
    dossier: str = Field(..., description="1er bloc 6 chiffres du nom fichier")

    # ── Optionnels — OMIS si absents ──
    currency: Optional[str] = Field(None, description="ISO 4217 ou '????'")
    gross_weight: Optional[str] = None    # kg virgule 3 déc
    net_weight: Optional[str] = None      # kg virgule 3 déc
    volume: Optional[str] = None          # m³ virgule 3 déc
    dof: Optional[str] = None             # mention DOF restituée textuellement (max 500 chars)
    rex: Optional[str] = None             # FRREX + chiffres

    @field_validator("date")
    @classmethod
    def _v_date(cls, v: str) -> str:
        if not _DATE_ISO_RE.match(v):
            raise ValueError(f"date={v!r} invalide : format AAAA-MM-JJ attendu")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"date={v!r} invalide : {e}")
        return v

    @field_validator("num", "supplier", "recipient", "dossier")
    @classmethod
    def _v_strings(cls, v: str) -> str:
        return _validate_no_control_chars(v, "field")

    @field_validator("total_ht", "gross_weight", "net_weight", "volume")
    @classmethod
    def _v_decimal(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_decimal_comma(v, "decimal")

    @field_validator("currency")
    @classmethod
    def _v_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if v == "????":
            return v
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError(f"currency={v!r} doit être ISO 4217 (3 lettres) ou '????'")
        return v

    @field_validator("dof")
    @classmethod
    def _v_dof(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = _validate_no_control_chars(v, "dof")
        if len(v) > 500:
            v = v[:497].rsplit(" ", 1)[0] + "..."
        return v

    @field_validator("rex")
    @classmethod
    def _v_rex(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if not re.match(r"^[A-Z]{2}REX\d+$", v):
            raise ValueError(f"rex={v!r} format attendu 'FRREX...' (pays + REX + chiffres)")
        return v


# ============================================================================
# Lines : ProductLine et XfeeLine
# ============================================================================

class ProductLine(BaseModel):
    """Ligne produit standard — règles V6.1."""

    ref: str
    label: str
    origin: str                  # ISO2 ou ISO2/NP ou ISO2/ISO2 ou "????????"
    qty: int                     # entier
    amount: str                  # virgule 2 déc

    # Optionnels
    unit_price: Optional[str] = None     # virgule 2 déc
    hs_code: Optional[str] = None         # 6-10 chiffres
    packages: Optional[int] = None        # entier
    ean: Optional[str] = None
    alcohol: Optional[str] = None         # virgule
    units: Optional[int] = None           # entier
    sugar: Optional[str] = None
    weight: Optional[str] = None          # virgule 3 déc

    @field_validator("ref", "label")
    @classmethod
    def _v_strings(cls, v: str) -> str:
        return _validate_no_control_chars(v, "field")

    @field_validator("origin")
    @classmethod
    def _v_origin(cls, v: str) -> str:
        v = v.strip().upper()
        if v == "????????":
            return v
        if v in _EU_BANNED:
            raise ValueError(
                f"origin={v!r} INTERDIT : EU/UE/CEE/EEC doivent être résolus en ISO2 réel"
            )
        # Composé (DE/NP, FR/DE, etc.)
        if not _ISO2_COMPOSED_RE.match(v):
            raise ValueError(
                f"origin={v!r} format attendu ISO2 ou ISO2/NP ou ISO2/ISO2"
            )
        # Vérifier que les parties UE ne contiennent pas un faux ISO2 banni
        for part in v.split("/"):
            if part in _EU_BANNED:
                raise ValueError(f"origin={v!r} contient une partie bannie {part!r}")
        return v

    @field_validator("amount", "unit_price", "alcohol", "sugar", "weight")
    @classmethod
    def _v_decimal(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_decimal_comma(v, "decimal")

    @field_validator("hs_code")
    @classmethod
    def _v_hs_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Nettoyage : retire points / espaces / tirets
        cleaned = re.sub(r"[.\s\-]", "", v)
        if not _HS_CODE_RE.match(cleaned):
            raise ValueError(f"hs_code={v!r} doit faire 6-10 chiffres après nettoyage")
        return cleaned

    @field_validator("qty", "packages", "units")
    @classmethod
    def _v_positive_int(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if not isinstance(v, int):
            raise ValueError(f"valeur entière attendue, reçu {type(v).__name__}")
        if v < 0:
            raise ValueError(f"valeur entière positive attendue, reçu {v}")
        return v


class XfeeLine(BaseModel):
    """Ligne XFEE — toujours en dernier dans invoice.lines."""

    type: Literal["xfee"]
    ref: Literal["XFEE"]
    label: Literal["FRAIS EXCLUS - REMISE - ESCOMPTE"]
    qty: Literal[1]
    amount: str  # virgule, peut être négatif

    @field_validator("amount")
    @classmethod
    def _v_amount(cls, v: str) -> str:
        return _validate_decimal_comma(v, "xfee.amount")


# Union pour les lignes
InvoiceLine = Union[ProductLine, XfeeLine]


# ============================================================================
# Invoice (header + lines)
# ============================================================================

class Invoice(BaseModel):
    """Une facture complète."""

    header: Header
    lines: list[InvoiceLine]

    @model_validator(mode="after")
    def _v_lines_structure(self) -> "Invoice":
        if not self.lines:
            raise ValueError("invoice.lines doit contenir au moins 1 élément (le XFEE)")
        # Le dernier élément doit être XfeeLine
        last = self.lines[-1]
        if not isinstance(last, XfeeLine):
            raise ValueError("Le DERNIER élément de invoice.lines doit être un XfeeLine")
        # Aucun XfeeLine ailleurs qu'à la fin
        for i, line in enumerate(self.lines[:-1]):
            if isinstance(line, XfeeLine):
                raise ValueError(
                    f"XfeeLine trouvé en position {i} — il doit être uniquement le dernier"
                )
        return self


# ============================================================================
# Output racine : {"invoices": [...]}
# ============================================================================

class LisaOutput(BaseModel):
    """Racine du JSON produit par LISA — schéma V6.1 strict."""

    invoices: list[Invoice]

    # Métadonnées internes LISA (PAS dans le JSON V6.1 pur — section _meta optionnelle)
    # Si on veut tracer la provenance (script utilisé, version, latence, etc.)
    # on les met dans un champ _meta SÉPARÉ qui ne fait pas partie du standard SYDONIA.
    meta: Optional[dict] = Field(None, alias="_meta")

    class Config:
        populate_by_name = True


# ============================================================================
# Helpers de sérialisation (omettre None / valeurs vides)
# ============================================================================

def dump_v6_1_strict(output: LisaOutput) -> dict:
    """Sérialise un LisaOutput au format JSON V6.1 strict :
    - omet TOUS les champs None
    - omet le champ _meta interne (mais le retourne séparément)
    """
    return output.model_dump(by_alias=True, exclude_none=True)
