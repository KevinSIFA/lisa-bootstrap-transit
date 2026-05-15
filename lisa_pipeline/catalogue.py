"""Catalogue par fournisseur — meta.yaml + scripts + samples + history.

Structure disque (cf. spec §8) :

    /opt/lisa/catalogue/<slug>/
        meta.yaml              # identité + aliases + business_rules + health
        method_natif.py        # script PDF natif (optionnel)
        method_scan_propre.py  # script OCR clean (optionnel)
        method_scan_difficile.py  # script OCR dégradé (optionnel)
        samples/
            natif/<date>_<num>.pdf
            natif/<date>_<num>.json     # golden
            scan_propre/...
            scan_difficile/...
        history.yaml           # journal des réparations/tests
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from .config import (
    CATALOGUE_DIR,
    DOC_TYPES,
    HEALTH_ACCEPTABLE_THRESHOLD,
    HEALTH_DRIFT_MINOR_THRESHOLD,
    HEALTH_SEALED_THRESHOLD,
    ROLLING_WINDOW_SIZE,
    SCRIPTS_RUNTIME_DIR,
)


# ============================================================================
# Helpers chemins
# ============================================================================
def supplier_dir(slug: str, root: Path = CATALOGUE_DIR) -> Path:
    return root / slug


def meta_path(slug: str, root: Path = CATALOGUE_DIR) -> Path:
    return supplier_dir(slug, root) / "meta.yaml"


def script_path_for(slug: str, doc_type: str, root: Path = CATALOGUE_DIR) -> Path:
    return supplier_dir(slug, root) / f"method_{doc_type}.py"


def history_path(slug: str, root: Path = CATALOGUE_DIR) -> Path:
    return supplier_dir(slug, root) / "history.yaml"


def samples_dir(slug: str, doc_type: str, root: Path = CATALOGUE_DIR) -> Path:
    return supplier_dir(slug, root) / "samples" / doc_type


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================================
# Modèle meta.yaml
# ============================================================================
@dataclass
class CatalogueMeta:
    """Métadonnées complètes d'un fournisseur dans le catalogue."""
    supplier_canonical: str
    slug: str
    aliases: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None  # 384 dim
    created_at: str = ""
    last_seen: str = ""
    # methods[doc_type] = {script, version, rolling_window[...], health_score, ...}
    methods: dict[str, dict] = field(default_factory=dict)
    # business_rules : leçons métier saisies par déclarants via GAS Instructions
    business_rules: list[dict] = field(default_factory=list)
    # Stats agrégées
    total_invoices_seen: int = 0
    total_invoices_success: int = 0
    total_invoices_quarantine: int = 0
    total_repairs: int = 0
    total_gemini_fallbacks: int = 0
    non_calibrable: bool = False  # marqueur "fournisseur impossible à scripter"


# ============================================================================
# Lecture / écriture meta.yaml
# ============================================================================
def load_meta(slug: str, root: Path = CATALOGUE_DIR) -> CatalogueMeta:
    """Charge meta.yaml, crée un squelette si absent."""
    p = meta_path(slug, root)
    if not p.exists():
        meta = CatalogueMeta(
            supplier_canonical=slug.upper().replace("_", " "),
            slug=slug,
            created_at=_now(),
            last_seen=_now(),
        )
        save_meta(meta, root)
        return meta

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return CatalogueMeta(
        supplier_canonical=data.get("supplier_canonical", slug),
        slug=data.get("slug", slug),
        aliases=data.get("aliases", []),
        embedding=data.get("embedding"),
        created_at=data.get("created_at", _now()),
        last_seen=data.get("last_seen", _now()),
        methods=data.get("methods", {}),
        business_rules=data.get("business_rules", []),
        total_invoices_seen=data.get("total_invoices_seen", 0),
        total_invoices_success=data.get("total_invoices_success", 0),
        total_invoices_quarantine=data.get("total_invoices_quarantine", 0),
        total_repairs=data.get("total_repairs", 0),
        total_gemini_fallbacks=data.get("total_gemini_fallbacks", 0),
        non_calibrable=data.get("non_calibrable", False),
    )


def save_meta(meta: CatalogueMeta, root: Path = CATALOGUE_DIR) -> None:
    """Persiste meta.yaml. Crée les dossiers nécessaires."""
    d = supplier_dir(meta.slug, root)
    d.mkdir(parents=True, exist_ok=True)
    for doc_type in DOC_TYPES:
        samples_dir(meta.slug, doc_type, root).mkdir(parents=True, exist_ok=True)
    meta.last_seen = _now()
    meta_path(meta.slug, root).write_text(
        yaml.safe_dump(meta.__dict__, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ============================================================================
# Gestion méthodes (scripts)
# ============================================================================
def register_method(
    slug: str, doc_type: str, script_content: str,
    version: str = "v1", root: Path = CATALOGUE_DIR,
) -> Path:
    """Enregistre un script Python comme méthode pour (slug, doc_type).

    - Persiste le script dans catalogue/<slug>/method_<doc_type>.py
    - Met à jour meta.yaml (version, last_calibration)
    - Copie également dans SCRIPTS_RUNTIME_DIR pour exécution runtime
    """
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type={doc_type!r} doit être dans {DOC_TYPES}")

    meta = load_meta(slug, root)
    sp = script_path_for(slug, doc_type, root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(script_content, encoding="utf-8")

    method_state = meta.methods.setdefault(doc_type, {})
    method_state.update({
        "script": sp.name,
        "version": version,
        "rolling_window": method_state.get("rolling_window", []),
        "health_score": method_state.get("health_score", 1.0),
        "last_calibration": _now(),
    })
    save_meta(meta, root)

    # Runtime copy
    runtime_script = SCRIPTS_RUNTIME_DIR / f"{slug}_{doc_type}.py"
    runtime_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sp, runtime_script)

    append_history(slug, {
        "action": "register_method",
        "doc_type": doc_type,
        "version": version,
        "at": _now(),
    }, root)
    logger.success(f"Méthode {doc_type} {version} enregistrée pour {slug}")
    return sp


# ============================================================================
# Rolling window + health score
# ============================================================================
def record_result(
    slug: str, doc_type: str, success: bool, confidence: float = 1.0,
    facture_id: str = "", root: Path = CATALOGUE_DIR,
) -> dict:
    """Enregistre le résultat d'une extraction dans la rolling window.

    Met à jour le health_score et déclenche les flags drift si nécessaire.
    """
    if doc_type not in DOC_TYPES:
        logger.warning(f"record_result : doc_type={doc_type!r} hors {DOC_TYPES}")
        return {}

    meta = load_meta(slug, root)
    method = meta.methods.setdefault(doc_type, {
        "rolling_window": [],
        "health_score": 1.0,
    })

    # Confidence : 1.0 ok / 0.0 fail / 0.5 partial / etc.
    outcome = "ok" if success else "fail"
    entry = {
        "date": _now(),
        "facture": facture_id,
        "outcome": outcome,
        "confidence": confidence,
    }
    method["rolling_window"].append(entry)
    # FIFO : garder seulement les ROLLING_WINDOW_SIZE dernières
    method["rolling_window"] = method["rolling_window"][-ROLLING_WINDOW_SIZE:]

    # Recalcul health_score
    window = method["rolling_window"]
    if window:
        method["health_score"] = round(
            sum(e["confidence"] for e in window) / len(window), 3
        )
    method["last_used"] = _now()

    # Flag drift selon les seuils
    hs = method["health_score"]
    if hs >= HEALTH_SEALED_THRESHOLD and len(window) >= ROLLING_WINDOW_SIZE:
        method["sealed_at"] = _now()
        method["drift_alert"] = False
    elif hs < HEALTH_DRIFT_MINOR_THRESHOLD:
        method["drift_alert"] = True
        method["drift_level"] = "major"
    elif hs < HEALTH_ACCEPTABLE_THRESHOLD:
        method["drift_alert"] = True
        method["drift_level"] = "minor"
    else:
        method["drift_alert"] = False

    # Stats agrégées
    if success:
        meta.total_invoices_success += 1
    meta.total_invoices_seen += 1

    save_meta(meta, root)
    return method


def get_health_state(slug: str, doc_type: str, root: Path = CATALOGUE_DIR) -> dict:
    """Retourne l'état health complet pour (slug, doc_type).

    Returns:
        {state, health_score, window_size, sealed, drift_alert, drift_level}
    """
    meta = load_meta(slug, root)
    method = meta.methods.get(doc_type, {})
    hs = method.get("health_score", 0.0)
    window_size = len(method.get("rolling_window", []))
    sealed = method.get("sealed_at") is not None and hs >= HEALTH_SEALED_THRESHOLD

    if hs >= HEALTH_SEALED_THRESHOLD and window_size >= ROLLING_WINDOW_SIZE:
        state = "sealed"
    elif hs >= HEALTH_ACCEPTABLE_THRESHOLD:
        state = "acceptable"
    elif hs >= HEALTH_DRIFT_MINOR_THRESHOLD:
        state = "drift_minor"
    else:
        state = "drift_major"

    return {
        "state": state,
        "health_score": hs,
        "window_size": window_size,
        "sealed": sealed,
        "drift_alert": method.get("drift_alert", False),
        "drift_level": method.get("drift_level", "none"),
    }


# ============================================================================
# Samples (PDF + JSON golden) pour rolling window tests
# ============================================================================
def add_sample(
    slug: str, doc_type: str, pdf_path: Path,
    golden_json_path: Optional[Path] = None,
    root: Path = CATALOGUE_DIR,
) -> Path:
    """Ajoute une facture sample au catalogue (PDF + JSON golden si présent).

    Respect FIFO : si > ROLLING_WINDOW_SIZE samples présents, retire le plus ancien.
    """
    sdir = samples_dir(slug, doc_type, root)
    sdir.mkdir(parents=True, exist_ok=True)

    dest = sdir / pdf_path.name
    shutil.copy2(pdf_path, dest)
    if golden_json_path and golden_json_path.exists():
        shutil.copy2(golden_json_path, sdir / f"{pdf_path.stem}.json")

    # FIFO cleanup
    samples = sorted(sdir.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
    while len(samples) > ROLLING_WINDOW_SIZE:
        oldest = samples.pop(0)
        json_companion = oldest.with_suffix(".json")
        try:
            oldest.unlink()
            if json_companion.exists():
                json_companion.unlink()
            logger.info(f"FIFO sample expulsé : {oldest.name}")
        except OSError as e:
            logger.warning(f"FIFO cleanup échoué pour {oldest}: {e}")

    logger.info(f"Sample ajouté : {dest}")
    return dest


def list_samples(slug: str, doc_type: str, root: Path = CATALOGUE_DIR) -> list[Path]:
    """Liste les PDF samples pour (slug, doc_type), tri ancienneté croissante."""
    sdir = samples_dir(slug, doc_type, root)
    if not sdir.exists():
        return []
    return sorted(sdir.glob("*.pdf"), key=lambda p: p.stat().st_mtime)


# ============================================================================
# Business rules (saisies par déclarants via GAS)
# ============================================================================
def add_business_rule(
    slug: str, rule: str, added_by: str = "declarant",
    root: Path = CATALOGUE_DIR,
) -> str:
    """Ajoute une règle métier au catalogue + au grimoire RAG.

    Args:
        slug : fournisseur
        rule : texte libre de la règle
        added_by : qui a saisi (user GAS, kevin, etc.)

    Returns:
        id unique de la règle.
    """
    meta = load_meta(slug, root)
    rid = f"br-{uuid.uuid4().hex[:8]}"
    meta.business_rules.append({
        "id": rid,
        "rule": rule,
        "added_by": added_by,
        "added_at": _now(),
        "active": True,
    })
    save_meta(meta, root)

    # Propagation au grimoire pour query par RAG
    from .grimoire import add_lesson
    add_lesson(
        supplier_slug=slug, category="business_rule",
        content=rule, created_by=added_by,
    )
    logger.success(f"Business rule {rid} ajoutée à {slug}: {rule[:80]}...")
    return rid


def deactivate_business_rule(slug: str, rule_id: str, root: Path = CATALOGUE_DIR) -> bool:
    meta = load_meta(slug, root)
    for r in meta.business_rules:
        if r["id"] == rule_id:
            r["active"] = False
            save_meta(meta, root)
            return True
    return False


# ============================================================================
# Historique
# ============================================================================
def append_history(slug: str, entry: dict, root: Path = CATALOGUE_DIR) -> None:
    """Append un événement à history.yaml."""
    p = history_path(slug, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if p.exists():
        history = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    history.append(entry)
    p.write_text(
        yaml.safe_dump(history, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ============================================================================
# Listing global
# ============================================================================
def list_suppliers(root: Path = CATALOGUE_DIR) -> list[CatalogueMeta]:
    """Liste tous les fournisseurs du catalogue."""
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "meta.yaml").exists():
            try:
                data = yaml.safe_load((d / "meta.yaml").read_text(encoding="utf-8"))
                if data and "slug" in data:
                    out.append(load_meta(data["slug"], root))
            except Exception as e:
                logger.warning(f"Skip catalogue {d.name}: {e}")
    return out
