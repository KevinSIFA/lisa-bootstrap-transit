# Stratégie modèles IA — LISA

Document de référence pour le pipeline d'extraction. Date : 14 mai 2026.

## IDs API exacts (vérifiés mai 2026)

| Rôle | Modèle | ID API | Prix in/out / M tokens |
|---|---|---|---|
| Orchestration primary | **Sonnet 4.6** | `claude-sonnet-4-6-20260514` | $3 / $15 |
| Thinking (cascade auto) | **Opus 4.7** | `claude-opus-4-7` | $5 / $25 |
| Tâches secondaires | **Haiku 4.5** | `claude-haiku-4-5-20251001` | $1 / $5 |
| Fallback niveau 3 | **Gemini 3.1 Pro thinking** | `gemini-3-1-pro-preview` (level=`high`) | -75% si cache hit |

**Important** : Sonnet 4.7 n'existe pas en mai 2026. Le dernier Sonnet est 4.6.
Opus est en 4.7 (releasé 16 avril 2026).

## Mécanique OpenClaw — cascade native

OpenClaw a un système deux-tiers natif. Pas besoin de coder la cascade manuellement.

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "anthropic/claude-sonnet-4-6-20260514",
        "thinking": "anthropic/claude-opus-4-7"
      }
    }
  }
}
```

- `primary` répond aux requêtes standards (80 % des cas)
- `thinking` est invoqué automatiquement quand OpenClaw détecte un besoin de raisonnement (multi-step, debug, architecture)

## Prompt caching — règles 2026

### Anthropic

⚠️ **TTL par défaut passé de 1h à 5 min en mars 2026.**

Pour garder un cache 1h, il faut **explicitement** déclarer :
```python
{
  "type": "text",
  "text": "...prompt système long...",
  "cache_control": { "type": "ephemeral", "ttl": "1h" }
}
```

- 5 min cache = rentable après 1 read
- 1h cache = rentable après 2 reads (write 2× base, read 0.1× base)
- Max 4 cache breakpoints par requête
- Stabilité du préfixe critique : 1 caractère diff = cache miss
- Min tokens pour cache : 1024 (Sonnet) ou 2048 (Opus)

### Gemini

- Context Caching avec TTL configurable
- Stockage : $4.50 / M tokens / heure
- Read cache : $0.20 / M tokens (<= 200k) ou $0.40 / M tokens (> 200k)
- Renewal automatique via cron Haiku (toutes les 12h par exemple)

## Détail par usage

### Orchestrateur (Sonnet 4.6 primary / Opus 4.7 thinking)

**Rôle** : reçoit messages opérateur Telegram, décide niveau pipeline, gère retries.

**Cache 1h explicite** :
- Identité LISA (SOUL.md, IDENTITY.md, USER.md)
- État système et catalogue fournisseurs top 20
- Schema SYDONIA (28 colonnes)

**Volume estimé** : 80 % Sonnet, 20 % Opus thinking (cascade auto OpenClaw).

### Haiku 4.5 — tâches secondaires

**Usage LISA** :
- Cron healthcheck quotidien (analyse logs)
- Classification rapide d'un PDF (natif vs scan)
- Pré-filtrage messages Telegram (commande vs question)
- Renewal des caches Gemini (toutes les 12h)
- Validation cohérence simple sur CSV produits

**Cache 1h** sur prompts de classification (très répétitifs).

### Opus 4.7 — calibration scripts fournisseurs

**Usage** : génération du script Python par nouveau fournisseur (one-shot).

**Cache 1h** :
- Prompt calibrateur (~5000 tokens stables)
- Template SYDONIA 28 colonnes
- 3 exemples de scripts existants

**Volume** : max 3 calibrations/jour (hard cap).
**Coût estimé** : ~65 F/script (avec nouveau tokenizer +35 %).

### Gemini 3.1 Pro thinking — fallback niveau 3

**Usage** : factures impossibles aux niveaux 1 et 2 (scans dégradés, formats exotiques).

**Thinking level** : `high` (max reasoning depth, vision + raisonnement).

**Context cache** :
- Prompt V6.1 niveau 3 (structure SYDONIA, règles d'or, ~20 exemples)
- Renewal via cron Haiku toutes les 12h

**Volume estimé** : 5 % des factures.

## Variables d'environnement (à ajouter au .env.bootstrap)

```bash
# === Anthropic ===
ANTHROPIC_MODEL_PRIMARY=claude-sonnet-4-6-20260514
ANTHROPIC_MODEL_THINKING=claude-opus-4-7
ANTHROPIC_MODEL_HAIKU=claude-haiku-4-5-20251001
ANTHROPIC_CACHE_TTL=1h

# === Google ===
GEMINI_MODEL_FALLBACK=gemini-3-1-pro-preview
GEMINI_THINKING_LEVEL=high
GEMINI_CACHE_TTL_HOURS=12
```

## Détection d'échec et escalade

| Étape | Critère d'échec | Action |
|---|---|---|
| Niveau 1 (PDF natif) | PyMuPDF extrait < 100 tokens, ou pas de structure | → Niveau 2 |
| Niveau 2 (OCR scan) | Tesseract score < seuil, ou regex ne matche pas | → Niveau 3 |
| Niveau 3 (Gemini) | JSON invalide ou incomplet | → Quarantine + alerte Telegram |
| Calibration Opus | Script ne valide pas sur 3 factures samples | → Quarantine + alerte |

Pas de retry automatique sur la calibration (cap 3/jour).

## Sources

- [Anthropic Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Gemini 3 thinking docs](https://ai.google.dev/gemini-api/docs/thinking)
- [OpenClaw configuration](https://docs.openclaw.ai/gateway/configuration)
