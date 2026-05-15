"""Grimoire RAG sqlite-vec — 3 catégories de leçons capitalisées.

Catégories (cf. spec §12) :
- extraction_quirk (auto) — patterns positions/regex utiles découverts en cours
- ocr_method (auto) — méthodes OCR/préprocessing qui ont marché
- business_rule (manuel via GAS) — règles métier saisies par déclarants SIFA

Le grimoire est interrogé :
- Avant calibration Opus (injection contexte)
- Avant orchestration Sonnet (règles transverses)
- À la demande pour audit/debug
"""
from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import (
    GRIMOIRE_CATEGORIES,
    GRIMOIRE_DB,
    GRIMOIRE_DEFAULT_TOP_K,
    GRIMOIRE_EMBEDDING_DIM,
    GRIMOIRE_MIN_SIMILARITY,
)


# ============================================================================
# Schéma SQLite
# ============================================================================
SCHEMA_INIT = """
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    supplier_slug TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    usage_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lessons_supplier ON lessons(supplier_slug);
CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category);
CREATE INDEX IF NOT EXISTS idx_lessons_active ON lessons(active);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(GRIMOIRE_DB))
    conn.executescript(SCHEMA_INIT)
    conn.commit()
    return conn


# ============================================================================
# Embeddings (réutilise supplier_identity.compute_embedding pour cohérence)
# ============================================================================
def _compute_embedding(text: str) -> list[float]:
    from .supplier_identity import compute_embedding
    return compute_embedding(text)


def _serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    from .supplier_identity import cosine_similarity
    return cosine_similarity(a, b)


# ============================================================================
# CRUD leçons
# ============================================================================
@dataclass
class Lesson:
    id: str
    supplier_slug: str
    doc_type: str
    category: str
    title: str
    content: str
    created_at: str
    created_by: str
    active: bool
    usage_count: int


def add_lesson(
    supplier_slug: str,
    category: str,
    content: str,
    *,
    doc_type: str = "*",
    title: str = "",
    created_by: str = "auto",
) -> str:
    """Ajoute une leçon au grimoire. Retourne l'id généré."""
    if category not in GRIMOIRE_CATEGORIES:
        raise ValueError(f"category={category!r} doit être dans {GRIMOIRE_CATEGORIES}")
    lid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    title = title or content[:80]
    embedding = _compute_embedding(f"{title}\n{content}")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO lessons (id, supplier_slug, doc_type, category, title, "
            "content, embedding, created_at, created_by, active, usage_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
            (lid, supplier_slug, doc_type, category, title, content,
             _serialize(embedding), now, created_by),
        )
        conn.commit()
    logger.info(f"Grimoire +leçon {lid[:8]} [{category}] {supplier_slug}/{doc_type}")
    return lid


def query_lessons(
    supplier_slug: str,
    *,
    doc_type: str = "*",
    categories: Optional[list[str]] = None,
    top_k: int = GRIMOIRE_DEFAULT_TOP_K,
    min_similarity: float = GRIMOIRE_MIN_SIMILARITY,
    query_text: Optional[str] = None,
) -> list[dict]:
    """Requête le grimoire avec scope par fournisseur et catégorie.

    Args:
        supplier_slug : slug fournisseur (ou "*" pour règles transverses)
        doc_type : type doc ("natif"|"scan_propre"|"scan_difficile") ou "*"
        categories : sous-ensemble de GRIMOIRE_CATEGORIES (None = toutes)
        top_k : nombre max résultats
        min_similarity : seuil cosine minimum si query_text fourni
        query_text : texte de requête pour scoring sémantique (optionnel)

    Returns:
        Liste de dicts {id, supplier_slug, doc_type, category, content, similarity}
        triés par similarity desc si query_text, sinon par created_at desc.
    """
    categories = categories or list(GRIMOIRE_CATEGORIES)

    # On récupère TOUTES les leçons compatibles, puis on filtre par similarité côté Python
    # (sqlite-vec serait plus rapide mais cette implémentation reste simple et portable)
    with _conn() as conn:
        # Inclut les leçons du fournisseur + les leçons transverses (supplier="*")
        # ET les leçons du doc_type + les leçons type="*"
        rows = conn.execute(
            f"SELECT id, supplier_slug, doc_type, category, title, content, "
            f"embedding, created_at, usage_count FROM lessons "
            f"WHERE active = 1 AND category IN ({','.join('?' for _ in categories)}) "
            f"AND (supplier_slug = ? OR supplier_slug = '*') "
            f"AND (doc_type = ? OR doc_type = '*')",
            (*categories, supplier_slug, doc_type),
        ).fetchall()

    results = []
    if query_text:
        query_emb = _compute_embedding(query_text)
        for r in rows:
            lid, slug, dt, cat, title, content, emb_blob, created_at, usage = r
            if emb_blob is None:
                continue
            emb = _deserialize(emb_blob)
            sim = _cosine(query_emb, emb)
            if sim >= min_similarity:
                results.append({
                    "id": lid, "supplier_slug": slug, "doc_type": dt,
                    "category": cat, "title": title, "content": content,
                    "similarity": sim, "created_at": created_at,
                    "usage_count": usage,
                })
        results.sort(key=lambda x: x["similarity"], reverse=True)
    else:
        for r in rows:
            lid, slug, dt, cat, title, content, emb_blob, created_at, usage = r
            results.append({
                "id": lid, "supplier_slug": slug, "doc_type": dt,
                "category": cat, "title": title, "content": content,
                "similarity": 1.0, "created_at": created_at,
                "usage_count": usage,
            })
        results.sort(key=lambda x: x["created_at"], reverse=True)

    results = results[:top_k]

    # Incrémenter usage_count pour les leçons retournées
    if results:
        with _conn() as conn:
            for r in results:
                conn.execute(
                    "UPDATE lessons SET usage_count = usage_count + 1 WHERE id = ?",
                    (r["id"],),
                )
            conn.commit()

    return results


def deactivate_lesson(lesson_id: str) -> bool:
    """Désactive une leçon (active=0). Retourne True si trouvée."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE lessons SET active = 0 WHERE id = ?", (lesson_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def list_lessons(
    supplier_slug: Optional[str] = None,
    category: Optional[str] = None,
    active_only: bool = True,
) -> list[dict]:
    """Liste les leçons (debug/admin)."""
    sql = "SELECT id, supplier_slug, doc_type, category, title, content, " \
          "created_at, created_by, active, usage_count FROM lessons WHERE 1=1"
    params = []
    if active_only:
        sql += " AND active = 1"
    if supplier_slug:
        sql += " AND supplier_slug = ?"
        params.append(supplier_slug)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY created_at DESC"

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "id": r[0], "supplier_slug": r[1], "doc_type": r[2],
            "category": r[3], "title": r[4], "content": r[5],
            "created_at": r[6], "created_by": r[7],
            "active": bool(r[8]), "usage_count": r[9],
        }
        for r in rows
    ]
