"""CLI LISA — entrypoint utilisé par l'agent OpenClaw via le tool `exec`.

Toutes les commandes retournent du JSON sur stdout pour parsing par l'agent.

Usage :
    python -m lisa_pipeline <command> [args...]

Commandes :
    sanitize <input_pdf> <output_pdf>
    vision-split <pdf> [--output-dir DIR]
    identify-supplier "<raw_name>"
    supplier-merge <existing_slug> "<new_alias>"
    supplier-create "<new_name>"
    classify-type <pdf>
    apply-script <pdf> <slug> <doc_type>
    try-all-scripts <pdf> <slug>
    repair-script <slug> <doc_type> <new_invoice_pdf>
    gemini-fallback <pdf>
    validate-math <json_file>
    queue-stats
    queue-add <filename> [--drive-id ID]
    queue-next
    catalogue-list
    catalogue-meta <slug>
    catalogue-health <slug> <doc_type>
    catalogue-add-sample <slug> <doc_type> <pdf> [--golden JSON]
    catalogue-add-rule <slug> "<rule_text>" [--added-by USER]
    grimoire-add-lesson <slug> <category> "<content>" [--doc-type TYPE]
    grimoire-query <slug> [--doc-type TYPE] [--category CAT] [--top-k N]
    grimoire-list [--slug SLUG] [--category CAT]
    drive-pull [--max N]
    drive-push <json_file>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from loguru import logger


# ============================================================================
# Helpers
# ============================================================================
def _to_jsonable(obj: Any) -> Any:
    """Convertit dataclasses, Paths, Pydantic models en JSON-able."""
    if is_dataclass(obj):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj


def _print(payload: Any) -> None:
    """Imprime un JSON propre sur stdout."""
    print(json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2))


# ============================================================================
# Commandes — sanitize
# ============================================================================
def cmd_sanitize(args: argparse.Namespace) -> int:
    from . import sanitize as sanitize_mod
    r = sanitize_mod.sanitize_pdf(Path(args.input), Path(args.output))
    _print(r)
    return 0 if r.success else 1


# ============================================================================
# Commandes — Vision split
# ============================================================================
def cmd_vision_split(args: argparse.Namespace) -> int:
    from . import vision_splitter
    output_dir = Path(args.output_dir) if args.output_dir else None
    r = vision_splitter.run_vision_split(Path(args.pdf), output_dir)
    _print(r)
    return 0 if r.status == "split" else 1


# ============================================================================
# Commandes — identité fournisseur
# ============================================================================
def cmd_identify_supplier(args: argparse.Namespace) -> int:
    from . import supplier_identity
    r = supplier_identity.identify_supplier(args.raw_name)
    _print(r)
    return 0


def cmd_supplier_merge(args: argparse.Namespace) -> int:
    from . import supplier_identity
    ok = supplier_identity.merge_supplier_alias(args.existing_slug, args.new_alias)
    _print({"success": ok, "existing_slug": args.existing_slug, "new_alias": args.new_alias})
    return 0 if ok else 1


def cmd_supplier_create(args: argparse.Namespace) -> int:
    from . import supplier_identity
    r = supplier_identity.create_new_supplier(args.new_name)
    _print(r)
    return 0


# ============================================================================
# Commandes — classification + extraction
# ============================================================================
def cmd_classify_type(args: argparse.Namespace) -> int:
    from . import classify
    r = classify.classify(Path(args.pdf))
    _print(r)
    return 0


def cmd_apply_script(args: argparse.Namespace) -> int:
    from . import script_runner
    r = script_runner.run_script(Path(args.pdf), args.slug, args.doc_type)
    _print(r)
    return 0 if r.success else (2 if r.needs_seed else 1)


def cmd_try_all_scripts(args: argparse.Namespace) -> int:
    from . import script_runner
    r = script_runner.try_all_supplier_scripts(Path(args.pdf), args.slug)
    if r is None:
        _print({"success": False, "message": "Aucun script alternatif disponible"})
        return 1
    _print(r)
    return 0


def cmd_repair_script(args: argparse.Namespace) -> int:
    from . import script_repairer
    r = script_repairer.seed_or_repair(
        args.slug, args.doc_type, Path(args.new_invoice_pdf),
    )
    _print(r)
    return 0 if r.success else 1


def cmd_gemini_fallback(args: argparse.Namespace) -> int:
    from . import gemini_fallback
    r = gemini_fallback.run_gemini_fallback(Path(args.pdf))
    _print(r)
    return 0 if r.success else 1


# ============================================================================
# Commandes — validation
# ============================================================================
def cmd_validate_math(args: argparse.Namespace) -> int:
    from . import validators
    from .models import LisaOutput
    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    output = LisaOutput.model_validate(data)
    r = validators.validate(output)
    _print(r)
    return 0 if r.success else 1


# ============================================================================
# Commandes — queue
# ============================================================================
def cmd_queue_stats(_args: argparse.Namespace) -> int:
    from . import queue_manager
    _print(queue_manager.stats())
    return 0


def cmd_queue_add(args: argparse.Namespace) -> int:
    from . import queue_manager
    new_id = queue_manager.enqueue(
        args.filename, drive_file_id=args.drive_id, sha256=args.sha256,
    )
    _print({"id": new_id, "filename": args.filename})
    return 0


def cmd_queue_next(_args: argparse.Namespace) -> int:
    from . import queue_manager
    item = queue_manager.claim_next()
    _print(item)
    return 0 if item else 2


# ============================================================================
# Commandes — catalogue
# ============================================================================
def cmd_catalogue_list(_args: argparse.Namespace) -> int:
    from . import catalogue
    suppliers = catalogue.list_suppliers()
    _print([{
        "slug": s.slug,
        "canonical": s.supplier_canonical,
        "methods": list(s.methods.keys()),
        "last_seen": s.last_seen,
        "total_seen": s.total_invoices_seen,
        "total_success": s.total_invoices_success,
        "non_calibrable": s.non_calibrable,
    } for s in suppliers])
    return 0


def cmd_catalogue_meta(args: argparse.Namespace) -> int:
    from . import catalogue
    meta = catalogue.load_meta(args.slug)
    _print(meta)
    return 0


def cmd_catalogue_health(args: argparse.Namespace) -> int:
    from . import catalogue
    state = catalogue.get_health_state(args.slug, args.doc_type)
    _print(state)
    return 0


def cmd_catalogue_add_sample(args: argparse.Namespace) -> int:
    from . import catalogue
    golden = Path(args.golden) if args.golden else None
    dest = catalogue.add_sample(args.slug, args.doc_type, Path(args.pdf), golden)
    _print({"sample_added": str(dest)})
    return 0


def cmd_catalogue_add_rule(args: argparse.Namespace) -> int:
    from . import catalogue
    rid = catalogue.add_business_rule(
        args.slug, args.rule, added_by=args.added_by,
    )
    _print({"rule_id": rid, "slug": args.slug})
    return 0


# ============================================================================
# Commandes — grimoire
# ============================================================================
def cmd_grimoire_add_lesson(args: argparse.Namespace) -> int:
    from . import grimoire
    lid = grimoire.add_lesson(
        supplier_slug=args.slug,
        category=args.category,
        content=args.content,
        doc_type=args.doc_type,
    )
    _print({"lesson_id": lid})
    return 0


def cmd_grimoire_query(args: argparse.Namespace) -> int:
    from . import grimoire
    categories = [args.category] if args.category else None
    lessons = grimoire.query_lessons(
        supplier_slug=args.slug,
        doc_type=args.doc_type or "*",
        categories=categories,
        top_k=args.top_k,
        query_text=args.query_text,
    )
    _print(lessons)
    return 0


def cmd_grimoire_list(args: argparse.Namespace) -> int:
    from . import grimoire
    lessons = grimoire.list_lessons(
        supplier_slug=args.slug, category=args.category,
    )
    _print(lessons)
    return 0


# ============================================================================
# Commandes — Drive
# ============================================================================
def cmd_drive_pull(args: argparse.Namespace) -> int:
    from . import drive_io
    from .config import INBOX_DIR
    pairs = drive_io.pull_inbox_to_local(INBOX_DIR, max_files=args.max)
    _print([{"id": f.id, "name": f.name, "local": str(p)} for f, p in pairs])
    return 0


def cmd_drive_push(args: argparse.Namespace) -> int:
    from . import drive_io
    file_id = drive_io.push_outbox(Path(args.json_file))
    _print({"drive_file_id": file_id, "json": args.json_file})
    return 0


# ============================================================================
# Argparse
# ============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lisa_pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    # sanitize
    s = sub.add_parser("sanitize", help="Sanitize un PDF (qpdf + exiftool)")
    s.add_argument("input"); s.add_argument("output")
    s.set_defaults(func=cmd_sanitize)

    # vision-split
    s = sub.add_parser("vision-split", help="Split + détection fournisseurs via Gemini Flash")
    s.add_argument("pdf"); s.add_argument("--output-dir", default=None)
    s.set_defaults(func=cmd_vision_split)

    # supplier identity
    s = sub.add_parser("identify-supplier", help="Résout l'identité d'un fournisseur (slug + embedding)")
    s.add_argument("raw_name")
    s.set_defaults(func=cmd_identify_supplier)

    s = sub.add_parser("supplier-merge", help="Force fusion alias dans un slug existant")
    s.add_argument("existing_slug"); s.add_argument("new_alias")
    s.set_defaults(func=cmd_supplier_merge)

    s = sub.add_parser("supplier-create", help="Force création nouveau fournisseur")
    s.add_argument("new_name")
    s.set_defaults(func=cmd_supplier_create)

    # classification
    s = sub.add_parser("classify-type", help="Classifie un PDF en natif/scan_propre/scan_difficile")
    s.add_argument("pdf"); s.set_defaults(func=cmd_classify_type)

    # extraction
    s = sub.add_parser("apply-script", help="Applique le script catalogue pour (slug, doc_type)")
    s.add_argument("pdf"); s.add_argument("slug"); s.add_argument("doc_type")
    s.set_defaults(func=cmd_apply_script)

    s = sub.add_parser("try-all-scripts", help="Teste tous les scripts du fournisseur (avant Opus)")
    s.add_argument("pdf"); s.add_argument("slug")
    s.set_defaults(func=cmd_try_all_scripts)

    s = sub.add_parser("repair-script", help="Répare/seed un script via Opus 4.7")
    s.add_argument("slug"); s.add_argument("doc_type"); s.add_argument("new_invoice_pdf")
    s.set_defaults(func=cmd_repair_script)

    s = sub.add_parser("gemini-fallback", help="Fallback extraction Gemini V6.1")
    s.add_argument("pdf"); s.set_defaults(func=cmd_gemini_fallback)

    # validate
    s = sub.add_parser("validate-math", help="Valide un JSON LisaOutput")
    s.add_argument("json_file"); s.set_defaults(func=cmd_validate_math)

    # queue
    s = sub.add_parser("queue-stats", help="Stats queue par statut")
    s.set_defaults(func=cmd_queue_stats)

    s = sub.add_parser("queue-add", help="Enqueue un fichier")
    s.add_argument("filename"); s.add_argument("--drive-id"); s.add_argument("--sha256")
    s.set_defaults(func=cmd_queue_add)

    s = sub.add_parser("queue-next", help="Récupère la prochaine facture pending")
    s.set_defaults(func=cmd_queue_next)

    # catalogue
    s = sub.add_parser("catalogue-list", help="Liste les fournisseurs du catalogue")
    s.set_defaults(func=cmd_catalogue_list)

    s = sub.add_parser("catalogue-meta", help="Affiche meta.yaml d'un fournisseur")
    s.add_argument("slug"); s.set_defaults(func=cmd_catalogue_meta)

    s = sub.add_parser("catalogue-health", help="État health d'un script catalogue")
    s.add_argument("slug"); s.add_argument("doc_type")
    s.set_defaults(func=cmd_catalogue_health)

    s = sub.add_parser("catalogue-add-sample", help="Ajoute un sample au catalogue (FIFO 5)")
    s.add_argument("slug"); s.add_argument("doc_type"); s.add_argument("pdf")
    s.add_argument("--golden", default=None)
    s.set_defaults(func=cmd_catalogue_add_sample)

    s = sub.add_parser("catalogue-add-rule", help="Ajoute une business_rule au catalogue + grimoire")
    s.add_argument("slug"); s.add_argument("rule")
    s.add_argument("--added-by", default="declarant")
    s.set_defaults(func=cmd_catalogue_add_rule)

    # grimoire
    s = sub.add_parser("grimoire-add-lesson", help="Ajoute une leçon au grimoire RAG")
    s.add_argument("slug"); s.add_argument("category"); s.add_argument("content")
    s.add_argument("--doc-type", default="*")
    s.set_defaults(func=cmd_grimoire_add_lesson)

    s = sub.add_parser("grimoire-query", help="Query le grimoire pour un fournisseur")
    s.add_argument("slug"); s.add_argument("--doc-type", default=None)
    s.add_argument("--category", default=None); s.add_argument("--top-k", type=int, default=10)
    s.add_argument("--query-text", default=None)
    s.set_defaults(func=cmd_grimoire_query)

    s = sub.add_parser("grimoire-list", help="Liste les leçons du grimoire (debug/admin)")
    s.add_argument("--slug", default=None); s.add_argument("--category", default=None)
    s.set_defaults(func=cmd_grimoire_list)

    # drive
    s = sub.add_parser("drive-pull", help="Pull PDFs depuis Drive Inbox")
    s.add_argument("--max", type=int, default=10)
    s.set_defaults(func=cmd_drive_pull)

    s = sub.add_parser("drive-push", help="Push JSON vers Drive Outbox")
    s.add_argument("json_file")
    s.set_defaults(func=cmd_drive_push)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        logger.exception("CLI error")
        _print({"error": str(e), "type": type(e).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
