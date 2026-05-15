"""Identité fournisseur autonome — slug déterministe + embeddings sqlite-vec.

Stratégie 3 étages (cf. spec §7) :
1. Slug auto déterministe (retire suffixes sociaux, accents, ponctuation)
2. Comparaison embedding cosine vs catalogue existant
3. Fusion auto si > 0.92, review Telegram si 0.75-0.92, nouveau si < 0.75
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import (
    CATALOGUE_DIR,
    GRIMOIRE_DB,
    SUPPLIER_AUTO_MERGE_THRESHOLD,
    SUPPLIER_REVIEW_THRESHOLD,
    SUPPLIER_SLUG_SUFFIXES_SOCIAUX,
)


@dataclass
class SupplierMatchResult:
    """Résultat de la résolution d'identité fournisseur."""
    raw_name: str                  # nom tel que reçu (de Vision Flash)
    slug: str                      # slug canonique (catalogue dir)
    status: str                    # "matched" | "new" | "review"
    similarity: Optional[float] = None  # cosine si comparé
    matched_supplier_slug: Optional[str] = None  # si matched ou review
    matched_supplier_canonical: Optional[str] = None  # nom canonique du match
    alias_added: bool = False      # True si on a ajouté le raw_name comme alias d'un existant


# ============================================================================
# Slug déterministe (étage 1)
# ============================================================================
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


_SUFFIXES_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in SUPPLIER_SLUG_SUFFIXES_SOCIAUX) + r")\b\.?",
    re.IGNORECASE,
)


def slugify_supplier(raw_name: str) -> str:
    """Calcule un slug canonique pour un nom fournisseur.

    Pipeline déterministe :
    1. strip accents
    2. lowercase
    3. retire ponctuation
    4. retire suffixes sociaux (SARL, SAS, GmbH, Ltd, etc.)
    5. retire mots vides courants (de, du, le, la, the, ...)
    6. espaces → _
    7. retire underscores multiples

    Ex: "Citroën Automobiles S.A." → "citroen_automobiles"
        "BERTRAND EXPORT SARL" → "bertrand_export"
    """
    s = _strip_accents(raw_name).lower()
    # Retire ponctuation (mais garde / et - qui sont rares dans les noms fournisseur)
    s = re.sub(r"[.,;:'\"!\?()&]", " ", s)
    # Retire suffixes sociaux
    s = _SUFFIXES_RE.sub(" ", s)
    # Retire mots vides très courants (à étendre si besoin)
    s = re.sub(r"\b(?:de|du|des|le|la|les|the|of|and|et|von|der|den|el|al)\b", " ", s)
    # Normalise les espaces
    s = re.sub(r"\s+", "_", s.strip())
    # Underscores multiples → 1 seul
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


# ============================================================================
# Embeddings (étage 2)
# ============================================================================
def compute_embedding(text: str) -> list[float]:
    """Calcule l'embedding du nom normalisé.

    Stratégie :
    - Tentative 1 : Anthropic embedding API si dispo (modèle voyage-3-lite typique)
    - Tentative 2 : local via sentence-transformers all-MiniLM-L6-v2 (384 dim)
    - Tentative 3 : embedding caractères pauvre (fallback en dernier recours)
    """
    # NOTE : on garde une implémentation très simple en attendant le choix final
    # du provider d'embedding. À enrichir quand Anthropic embedding API ou
    # sentence-transformers seront installés et configurés.
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        return _MODEL.encode(text).tolist()
    except ImportError:
        logger.warning(
            "sentence-transformers indisponible — fallback embedding caractère pauvre"
        )
        # Fallback : embedding caractère (84 dim, [0,1])
        # NB : très grossier, à utiliser uniquement en dev/CI sans LLM ni network
        vec = [0.0] * 384
        for i, c in enumerate(text.lower()):
            idx = (ord(c) * 17 + i * 31) % 384
            vec[idx] += 1.0
        # Normalisation L2
        import math
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity entre deux vecteurs normalisés."""
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ============================================================================
# Stockage embeddings (sqlite-vec via grimoire.db ou table dédiée)
# ============================================================================
SUPPLIERS_TABLE_INIT = """
CREATE TABLE IF NOT EXISTS suppliers (
    slug TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    embedding BLOB,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(GRIMOIRE_DB))
    conn.execute(SUPPLIERS_TABLE_INIT)
    conn.commit()
    return conn


def _serialize_embedding(vec: list[float]) -> bytes:
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_embedding(blob: bytes) -> list[float]:
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ============================================================================
# Résolution d'identité (entrée principale)
# ============================================================================
def identify_supplier(raw_name: str) -> SupplierMatchResult:
    """Résout l'identité d'un fournisseur depuis un nom brut.

    Args:
        raw_name : nom tel qu'extrait par Vision Flash

    Returns:
        SupplierMatchResult avec status "matched"|"new"|"review"
    """
    slug = slugify_supplier(raw_name)
    embedding = compute_embedding(slug)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with _conn() as conn:
        # Vérifier si le slug existe déjà directement
        row = conn.execute(
            "SELECT slug, canonical_name, aliases FROM suppliers WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row:
            existing_slug, canonical, aliases_json = row
            import json
            aliases = json.loads(aliases_json)
            if raw_name not in aliases:
                aliases.append(raw_name)
                conn.execute(
                    "UPDATE suppliers SET aliases = ?, last_seen = ? WHERE slug = ?",
                    (json.dumps(aliases, ensure_ascii=False), now, slug),
                )
                conn.commit()
            logger.info(f"Supplier matched (exact slug) : {slug}")
            return SupplierMatchResult(
                raw_name=raw_name,
                slug=existing_slug,
                status="matched",
                similarity=1.0,
                matched_supplier_slug=existing_slug,
                matched_supplier_canonical=canonical,
                alias_added=(raw_name not in aliases[:-1] if raw_name in aliases else False),
            )

        # Sinon, comparer embedding aux fournisseurs existants
        rows = conn.execute(
            "SELECT slug, canonical_name, embedding FROM suppliers"
        ).fetchall()

        best_similarity = 0.0
        best_slug: Optional[str] = None
        best_canonical: Optional[str] = None

        for r_slug, r_canonical, r_emb_blob in rows:
            if r_emb_blob is None:
                continue
            r_emb = _deserialize_embedding(r_emb_blob)
            sim = cosine_similarity(embedding, r_emb)
            if sim > best_similarity:
                best_similarity = sim
                best_slug = r_slug
                best_canonical = r_canonical

        # Décision
        if best_similarity >= SUPPLIER_AUTO_MERGE_THRESHOLD and best_slug:
            # Fusion auto : ajoute alias au slug existant
            import json
            row = conn.execute(
                "SELECT aliases FROM suppliers WHERE slug = ?", (best_slug,)
            ).fetchone()
            aliases = json.loads(row[0]) if row else []
            if raw_name not in aliases:
                aliases.append(raw_name)
            conn.execute(
                "UPDATE suppliers SET aliases = ?, last_seen = ? WHERE slug = ?",
                (json.dumps(aliases, ensure_ascii=False), now, best_slug),
            )
            conn.commit()
            logger.info(
                f"Supplier matched (similarity {best_similarity:.3f}) : "
                f"{slug} → {best_slug} (auto-merge)"
            )
            return SupplierMatchResult(
                raw_name=raw_name,
                slug=best_slug,
                status="matched",
                similarity=best_similarity,
                matched_supplier_slug=best_slug,
                matched_supplier_canonical=best_canonical,
                alias_added=True,
            )

        elif best_similarity >= SUPPLIER_REVIEW_THRESHOLD and best_slug:
            # Zone grise : besoin de la review humaine via Telegram
            logger.warning(
                f"Supplier review needed (similarity {best_similarity:.3f}) : "
                f"raw={raw_name!r} → candidate={best_slug!r}"
            )
            return SupplierMatchResult(
                raw_name=raw_name,
                slug=slug,  # slug du nom raw, pas du match — Kevin tranche
                status="review",
                similarity=best_similarity,
                matched_supplier_slug=best_slug,
                matched_supplier_canonical=best_canonical,
            )

        else:
            # Création nouveau fournisseur
            import json
            canonical = raw_name.upper().strip()
            conn.execute(
                "INSERT INTO suppliers (slug, canonical_name, aliases, embedding, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (slug, canonical, json.dumps([raw_name], ensure_ascii=False),
                 _serialize_embedding(embedding), now, now),
            )
            conn.commit()
            # Créer aussi le dossier catalogue
            (CATALOGUE_DIR / slug).mkdir(parents=True, exist_ok=True)
            logger.success(f"Supplier NEW : {slug} (canonical={canonical!r})")
            return SupplierMatchResult(
                raw_name=raw_name,
                slug=slug,
                status="new",
                similarity=best_similarity,
            )


# ============================================================================
# Opérations manuelles (décisions Kevin via Telegram)
# ============================================================================
def merge_supplier_alias(existing_slug: str, new_alias: str) -> bool:
    """Force la fusion : ajoute new_alias comme alias de existing_slug.
    Décision Kevin OK sur prompt Telegram.
    """
    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _conn() as conn:
        row = conn.execute(
            "SELECT aliases FROM suppliers WHERE slug = ?", (existing_slug,)
        ).fetchone()
        if not row:
            logger.error(f"merge_supplier_alias : slug {existing_slug!r} introuvable")
            return False
        aliases = json.loads(row[0])
        if new_alias not in aliases:
            aliases.append(new_alias)
        conn.execute(
            "UPDATE suppliers SET aliases = ?, last_seen = ? WHERE slug = ?",
            (json.dumps(aliases, ensure_ascii=False), now, existing_slug),
        )
        conn.commit()
    logger.success(f"Alias {new_alias!r} ajouté à {existing_slug!r}")
    return True


def create_new_supplier(raw_name: str) -> SupplierMatchResult:
    """Force la création d'un nouveau fournisseur (décision Kevin NON sur prompt).
    Bypass la similarité.
    """
    import json
    from datetime import datetime, timezone
    slug = slugify_supplier(raw_name)
    embedding = compute_embedding(slug)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    canonical = raw_name.upper().strip()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO suppliers "
            "(slug, canonical_name, aliases, embedding, created_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (slug, canonical, json.dumps([raw_name], ensure_ascii=False),
             _serialize_embedding(embedding), now, now),
        )
        conn.commit()
        (CATALOGUE_DIR / slug).mkdir(parents=True, exist_ok=True)
    logger.success(f"Supplier FORCE-NEW : {slug}")
    return SupplierMatchResult(
        raw_name=raw_name, slug=slug, status="new", similarity=0.0,
    )
