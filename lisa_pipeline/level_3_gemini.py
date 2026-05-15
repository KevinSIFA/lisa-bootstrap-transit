"""DEPRECATED — module renommé en V3 (mai 2026).

Ce module a été renommé en lisa_pipeline.gemini_fallback et migré vers
le nouveau SDK google-genai (remplace google-cloud-aiplatform legacy).
"""
raise ImportError(
    "lisa_pipeline.level_3_gemini a été renommé en lisa_pipeline.gemini_fallback. "
    "Utiliser lisa_pipeline.gemini_fallback.run_gemini_fallback(pdf_path) à la place."
)
