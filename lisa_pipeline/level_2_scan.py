"""DEPRECATED — module supprimé en V3 (mai 2026).

Ce module a été remplacé par :
- lisa_pipeline.classify (détection scan_propre/scan_difficile)
- lisa_pipeline.script_runner (chargement et exécution scripts OCR catalogue)
"""
raise ImportError(
    "lisa_pipeline.level_2_scan a été supprimé en V3. "
    "Utiliser lisa_pipeline.script_runner.run_script(pdf, slug, 'scan_propre'|'scan_difficile')."
)
