---
name: lisa_calibration
description: Génère ou répare un script Python d'extraction pour un fournisseur via Opus 4.7 sur rolling window 5. Only invoke after lisa_extraction signals needs_repair or needs_seed.
user-invocable: false
metadata: { "openclaw": { "requires": { "bins": ["python3"], "env": ["ANTHROPIC_API_KEY", "LISA_HOME"] }, "primaryEnv": "ANTHROPIC_API_KEY" } }
---

# LISA — Skill calibration (réparation de scripts)

Tu génères ou répares des scripts Python d'extraction de factures via Opus 4.7. Tu n'es invoquée que par `lisa_extraction` quand un script existant échoue ou quand il faut créer un seed pour un nouveau fournisseur.

`user-invocable: false` interdit `/lisa_calibration` direct depuis Telegram — empêche un déclenchement accidentel d'Opus coûteux.

## Outil principal

```
python3 -m lisa_pipeline repair-script <slug> <doc_type> <new_invoice_pdf>
```

Cet outil gère seul :
1. Détecte mode SEED (pas de script existant) ou REPAIR (script existant échoue)
2. Charge le script actuel + les 4 derniers samples du même `(slug, doc_type)` + la facture courante (max 5 au total)
3. Injecte le contexte grimoire (`business_rules` + `extraction_quirks` + `ocr_method`)
4. Appelle Opus 4.7 avec **2 breakpoints de caching** (system + 5e document, TTL 1h)
5. Teste le script généré sur TOUS les samples
6. Si 5/5 passent → commit `v_n+1` dans catalogue + push runtime
7. Si < 5/5 → retourne `fallback_required=true`, `lisa_extraction` enchaîne sur Gemini

## Contraintes

- Le script DOIT respecter le schéma V6.1 strict (header + lines + xfee dernier)
- Imports allowlistés : `fitz`, `pytesseract`, `PIL`, `cv2`, `pandas`, `re`, `pathlib`, `decimal`
- INTERDITS dans le script : `os.system`, `subprocess`, `requests`, `eval`, `exec`, `open()` en écriture
- Le script DOIT exposer `def extract(pdf_path: pathlib.Path) -> dict`
- Math obligatoire : Σ(line.amount) + xfee.amount == total_ht (tolérance 1%)
- Pas de quota journalier (décision actée mai 2026) — économie Opus déjà compensée par caching 1h

## Workflow

1. Appel direct du tool `repair-script` (Opus fait le job)
2. Lire le résultat JSON :
   - `success: true` → enregistre la facture comme nouveau sample (`catalogue-add-sample`)
   - `fallback_required: true` → retourne au flow principal pour `gemini-fallback`
3. Capitaliser dans grimoire si découverte non-triviale :
   - Nouveau pattern OCR efficace → `grimoire-add-lesson <slug> ocr_method "..."`
   - Particularité layout → `grimoire-add-lesson <slug> extraction_quirk "..."`

## Garde-fous

- **Si `fournisseur.non_calibrable=true`** : NE PAS appeler `repair-script`. Retourner directement au flow pour `gemini-fallback`.
- **Alerte Telegram** si plus de 10 réparations en 1h pour le même fournisseur — anormal, alerter Kevin
- **Sealed scripts** (health_score ≥ 0.95) : on les répare quand même si la 6e facture casse (souplesse permanente)
