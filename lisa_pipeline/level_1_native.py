"""DEPRECATED — module supprimé en V3 (mai 2026).

Ce module a été remplacé par :
- lisa_pipeline.classify (détection type natif/scan_propre/scan_difficile)
- lisa_pipeline.script_runner (chargement et exécution des scripts catalogue)

Import ce module lève maintenant explicitement pour éviter les régressions.
"""
raise ImportError(
    "lisa_pipeline.level_1_native a été supprimé en V3. "
    "Utiliser lisa_pipeline.script_runner.run_script(pdf, slug, 'natif') à la place."
)
