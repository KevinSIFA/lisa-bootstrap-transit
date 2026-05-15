"""DEPRECATED — module renommé en V3 (mai 2026).

Ce module a été renommé en lisa_pipeline.script_repairer.

Différences majeures V2 → V3 :
- 2 breakpoints de caching Anthropic (system + 5e document)
- Modes SEED (1ère facture) ou REPAIR (rolling window 5)
- Plus de hard cap "3 calibrations/jour" (décision actée mai 2026)
- Bootstrap dès la 1ère facture (Option B)
- Injection contexte grimoire (business_rules + extraction_quirks + ocr_method)
"""
raise ImportError(
    "lisa_pipeline.calibrator a été renommé en lisa_pipeline.script_repairer. "
    "Utiliser lisa_pipeline.script_repairer.seed_or_repair(slug, doc_type, pdf) à la place."
)
