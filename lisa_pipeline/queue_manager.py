"""Queue persistante SQLite pour les factures en cours de traitement.

Etats : pending -> processing -> done | quarantine
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import QUEUE_DB, QUEUE_DIR


# ============================================================================
# Schema SQL (cree par bootstrap, on le replique ici pour le mode dev local)
# ============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    drive_file_id TEXT,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    level INTEGER,
    supplier TEXT,
    received_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    attempts INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_sha256 ON queue(sha256);
CREATE INDEX IF NOT EXISTS idx_queue_received ON queue(received_at);
"""


@dataclass
class QueueItem:
    id: Optional[int]
    filename: str
    drive_file_id: Optional[str]
    sha256: Optional[str]
    status: str
    level: Optional[int]
    supplier: Optional[str]
    received_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    attempts: int
    error: Optional[str]


# ============================================================================
# Connexion
# ============================================================================
def _connect(db_path: Path = QUEUE_DB) -> sqlite3.Connection:
    """Ouvre une connexion SQLite avec WAL active."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_of_file(path: Path, chunk_size: int = 65536) -> str:
    """Calcule le SHA256 d'un fichier (pour deduplication)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


# ============================================================================
# Operations
# ============================================================================
def enqueue(
    filename: str,
    drive_file_id: str | None = None,
    sha256: str | None = None,
    db_path: Path = QUEUE_DB,
) -> int:
    """Ajoute un fichier a la queue en statut pending.

    Retourne l'id de la ligne ou l'id existant si deja en queue (dedup sha256).
    """
    with _connect(db_path) as conn:
        # Deduplication sur sha256 si fourni
        if sha256:
            row = conn.execute(
                "SELECT id, status FROM queue WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if row:
                logger.info(f"Doublon detecte (sha256={sha256[:8]}...) id={row['id']} status={row['status']}")
                return row["id"]

        cur = conn.execute(
            """INSERT INTO queue (filename, drive_file_id, sha256, status, received_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (filename, drive_file_id, sha256, _now_iso()),
        )
        new_id = cur.lastrowid
        logger.info(f"Enqueue id={new_id} {filename}")
        return new_id


def claim_next(db_path: Path = QUEUE_DB) -> Optional[QueueItem]:
    """Prend la prochaine facture pending et la passe en processing.

    Retourne None si la queue est vide.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT * FROM queue
               WHERE status = 'pending'
               ORDER BY received_at ASC
               LIMIT 1"""
        ).fetchone()
        if not row:
            return None

        conn.execute(
            """UPDATE queue
               SET status = 'processing',
                   started_at = ?,
                   attempts = attempts + 1
               WHERE id = ?""",
            (_now_iso(), row["id"]),
        )
        # Recharge pour refleter les changements
        row = conn.execute("SELECT * FROM queue WHERE id = ?", (row["id"],)).fetchone()
        return QueueItem(**dict(row))


def mark_done(
    item_id: int,
    level: int | None = None,
    supplier: str | None = None,
    db_path: Path = QUEUE_DB,
) -> None:
    """Marque une facture comme traitee avec succes."""
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE queue
               SET status = 'done',
                   completed_at = ?,
                   level = COALESCE(?, level),
                   supplier = COALESCE(?, supplier),
                   error = NULL
               WHERE id = ?""",
            (_now_iso(), level, supplier, item_id),
        )
        logger.success(f"Done id={item_id} level={level} supplier={supplier}")


def mark_quarantine(item_id: int, error: str, db_path: Path = QUEUE_DB) -> None:
    """Place une facture en quarantaine avec son message d'erreur."""
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE queue
               SET status = 'quarantine',
                   completed_at = ?,
                   error = ?
               WHERE id = ?""",
            (_now_iso(), error, item_id),
        )
        logger.error(f"Quarantine id={item_id}: {error}")


def stats(db_path: Path = QUEUE_DB) -> dict[str, int]:
    """Compte par statut. Utile pour /queue dans Telegram."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM queue GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


def list_pending(limit: int = 20, db_path: Path = QUEUE_DB) -> list[QueueItem]:
    """Liste les items en pending (par ordre FIFO)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM queue WHERE status = 'pending'
               ORDER BY received_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [QueueItem(**dict(r)) for r in rows]


def get(item_id: int, db_path: Path = QUEUE_DB) -> Optional[QueueItem]:
    """Recupere un item par id."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()
        return QueueItem(**dict(row)) if row else None
