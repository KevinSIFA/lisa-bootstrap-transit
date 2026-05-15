# Prompt Caching Anthropic + Gemini — Référence MAI 2026

> Document consolidé pour Kevin / projet LISA — 15 mai 2026
>
> Référence partagée pour l'exploitation du caching sur les 5 appels LLM de LISA (Sonnet orchestrateur, Opus calibration, Haiku heartbeat, Gemini Flash Vision split, Gemini Pro fallback V6.1).

---

## Méthodologie

Deux agents de recherche ont travaillé en parallèle (15/05/2026) :
- Anthropic prompt caching → fetch officiel `platform.claude.com` (Prompt caching + Pricing)
- Gemini context caching → fetch officiel `ai.google.dev` + `cloud.google.com` (Vertex AI context cache, pricing, blog)

Toutes les affirmations sont sourcées. Les zones incertaines portent un encadré `⚠️ Non vérifié`.

---

## Table des matières

1. [Synthèse exécutive pour LISA](#1-synthèse-exécutive-pour-lisa)
2. [Anthropic — Prompt caching mai 2026](#2-anthropic--prompt-caching-mai-2026)
3. [Gemini — Context caching mai 2026](#3-gemini--context-caching-mai-2026)
4. [Décisions et patterns LISA](#4-décisions-et-patterns-lisa)
5. [Snippets Python prêts à coller](#5-snippets-python-prêts-à-coller)
6. [Monitoring et garde-fous](#6-monitoring-et-garde-fous)

---

## 1. Synthèse exécutive pour LISA

### Verdict par appel LLM

| Appel | Caching à activer | TTL | Économie estimée |
|-------|-------------------|-----|------------------|
| **Sonnet 4.6 orchestrateur** (110/jour, ~5K tokens stables) | Anthropic automatic caching, TTL **1h** explicite | 1h | ~−89% sur l'input |
| **Opus 4.7 calibration** (5-15/jour, ~10K instructions + 5 factures samples) | 2 breakpoints Anthropic explicites, TTL **1h** | 1h | ~−85% sur l'input |
| **Haiku 4.5 heartbeat** (50/jour, <200 tokens) | **Pas de caching** — sous le seuil 4096 | n/a | 0 |
| **Gemini 3 Flash split** (110/jour, ~2K system prompt) | **Implicit caching automatique** uniquement (sous le seuil 4K explicit) | n/a | ~−45% via implicit best-effort |
| **Gemini 3.1 Pro fallback V6.1** (5-10/jour, 10-12K tokens) | **Implicit caching uniquement** — volume trop bas pour rentabiliser explicit | n/a | ~−10% global (input seul cacheable) |

### Trois enseignements majeurs

1. **Anthropic > Gemini sur le caching économique en bas volume.** À 5-15 appels/jour, Gemini explicit caching coûte plus cher que pas de caching (à cause du frais de stockage horaire). Anthropic est rentable dès 2 hits.

2. **Gemini fait du caching implicit automatique** depuis 2.5. Best-effort, 75-90% de discount sur les tokens cachés détectés sans config. Suffisant pour LISA avec son volume bas — mais exige un prompt **byte-stable** (charge en `open(..., "rb")`).

3. **Le caching Anthropic ne traverse pas Vertex AI proprement.** Si LISA utilise Anthropic via Vertex (déconseillé), pas d'automatic caching, pas de workspace isolation. → **Toujours appeler Anthropic directement (Claude API)**, pas via Vertex.

---

## 2. Anthropic — Prompt caching mai 2026

### 2.1 Mécanisme actuel

Deux TTL en GA (general availability, plus besoin de beta header) :
- **5 minutes** (défaut)
- **1 heure** (`"ttl": "1h"`)

**Automatic caching** (nouveau 2026) : `cache_control` au niveau racine, le système place le breakpoint et le fait avancer. Disponible Claude API + AWS + Microsoft Foundry. **PAS sur Vertex/Bedrock**.

**Workspace isolation** depuis le 5 février 2026 (avant : org-level). Tous les appels LISA doivent partir du même workspace pour partager les caches.

### 2.2 Seuils et limites

| Modèle | Minimum cacheable |
|--------|-------------------|
| Claude Opus 4.7 / 4.6 | **4 096 tokens** |
| Claude Sonnet 4.6 | **1 024 tokens** |
| Claude Haiku 4.5 | **4 096 tokens** |
| Claude Haiku 3.5 | 2 048 tokens |

- **Max 4 breakpoints** `cache_control` par requête
- **Lookback window de 20 blocs** : si conversation gonfle de +20 blocs entre 2 requêtes sans breakpoint intermédiaire, hit manqué
- Hiérarchie figée : `tools` → `system` → `messages`

### 2.3 Tarification (officielle mai 2026)

| Opération | Multiplicateur vs base input |
|-----------|------------------------------|
| **5-min cache write** | 1.25× |
| **1-hour cache write** | 2× |
| **Cache hit (read)** | 0.10× (−90%) |

| Modèle | Base input | 5m write | 1h write | Cache read | Output |
|--------|-----------|----------|----------|------------|--------|
| **Claude Opus 4.7** | $5/MTok | $6.25 | $10.00 | **$0.50** | $25 |
| **Claude Sonnet 4.6** | $3/MTok | $3.75 | $6.00 | **$0.30** | $15 |
| **Claude Haiku 4.5** | $1/MTok | $1.25 | $2.00 | **$0.10** | $5 |

**Point de rentabilité** :
- TTL 5min : **rentable dès 1 cache hit**
- TTL 1h : **rentable dès 2 cache hits**

### 2.4 Pre-warming (nouveau 2026)

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=0,                 # 0 = warmup seulement, pas de gen
    system=[{
        "type": "text",
        "text": "<gros system prompt LISA>",
        "cache_control": {"type": "ephemeral"}
    }],
    messages=[{"role": "user", "content": "warmup"}]
)
```

Réponse : `content: []`, `stop_reason: "max_tokens"`, `cache_creation_input_tokens > 0`. Pas de frais output.

> 💡 **Pertinent LISA** : un cron Haiku qui pre-warm Sonnet toutes les 4 min maintient le cache 5min chaud à coût quasi-nul.

### 2.5 Invalidation du cache

| Changement | Tools | System | Messages |
|------------|-------|--------|----------|
| Tool definitions modifiées | ✘ | ✘ | ✘ |
| Toggle web search | ✓ | ✘ | ✘ |
| `tool_choice` | ✓ | ✓ | ✘ |
| Ajout/retrait d'images | ✓ | ✓ | ✘ |
| Paramètres thinking | ✓ | ✓ | ✘ |

**Pièges à éviter** :
- **Timestamps dans le system prompt** → cache invalide à chaque seconde. Mettre la date dans le user message.
- **Ordre des clés JSON** : Swift et Go randomisent → cache cassé. Vérifier les SDK.
- **Concurrent requests** : cache n'est dispo qu'après réponse de la 1re. Attendre avant requêtes parallèles.

### 2.6 Métriques

```python
usage = response.usage
total_input = (
    usage.cache_read_input_tokens 
    + usage.cache_creation_input_tokens 
    + usage.input_tokens
)
hit_ratio = usage.cache_read_input_tokens / total_input
```

Cible production saine : **> 80%** pour agent à system stable.

### 2.7 Plateformes

| Plateforme | Auto caching | 1h TTL | Workspace isolation |
|------------|--------------|--------|---------------------|
| Claude API (1st party) | ✓ | ✓ | ✓ |
| Claude on AWS | ✓ | ✓ | ✓ |
| Microsoft Foundry (beta) | ✓ | ✓ | ✓ |
| Vertex AI | ✘ | ✓ | ✘ |
| Bedrock | ✘ | ✘ | ✘ |

> 💡 **Pertinent LISA** : Anthropic Claude API direct (pas via Vertex). C'est déjà la décision actée (clé `ANTHROPIC_API_KEY` directe).

---

## 3. Gemini — Context caching mai 2026

### 3.1 Deux mécanismes complémentaires

| Mécanisme | Activation | Discount | Stockage facturé |
|-----------|-----------|----------|------------------|
| **Implicit caching** | Auto, ON par défaut sur 2.5+ | 75-90% off | Non |
| **Explicit caching** (`cachedContents`) | Manuel via API | 90% (2.5+) / 75% (Gemini 3 Pro selon source) | Oui (TTL × $/h) |

**Implicit caching** : Google détecte préfixe identique byte-stable → facture cached tokens à 10% du tarif input. Aucun frais stockage. Aucune config requise.

**Explicit caching** : tu crées une ressource `cachedContents` avec TTL → tu obtiens un `name` → tu passes ce nom dans chaque `generateContent` ultérieur. Discount garanti mais frais de stockage horaire.

### 3.2 Modèles supportés (mai 2026)

- Tous les 2.5+ supportent caching
- **Gemini 3 Flash Preview** (`gemini-3-flash-preview`, sortie 17 déc 2025) : supporté
- **Gemini 3.1 Pro Preview** (`gemini-3.1-pro-preview`) : supporté
- Gemini 2.0 Flash/Flash-Lite : EOL annoncé 1er juin 2026

### 3.3 Seuils

| Famille modèles | Minimum tokens cache explicite |
|-----------------|-------------------------------|
| Gemini 2.0 / 2.5 | 2 048 |
| **Gemini 3 / 3.1** | **4 096** |

> ⚠️ **Pertinent LISA Flash** : ton system prompt de split (~2K tokens) est **sous le seuil 4K** pour l'explicit. Tu ne peux compter que sur l'implicit caching. Pour franchir le seuil : padder avec few-shot examples stables.

### 3.4 TTL

- **Défaut** : 60 minutes
- **Min pratique** : ~1 minute
- **Max** : pas de borne documentée
- Refresh dynamique : `caches.update(ttl=...)` à tout moment

### 3.5 Tarification mai 2026

**Gemini 3 Flash Preview** :
| Item | USD / 1M tokens |
|------|-----------------|
| Input standard | $0.50 |
| Output | $3.00 |
| Cache read (hit) | **$0.05** (−90%) |
| Cache storage | **$1.00/heure** |

**Gemini 3.1 Pro Preview** (≤ 200K context) :
| Item | USD / 1M tokens |
|------|-----------------|
| Input standard | $2.00 |
| Output (thinking inclus) | $12.00 |
| Cache read (hit) | **$0.20** (−90%) ou **$0.50** (−75% selon source) |
| Cache storage | **$4.50/heure** |
| Long context >200K | $4 / $18 |

> ⚠️ **Non vérifié** : conflit de sources sur le discount Gemini 3 Pro (90% vs 75%). Conservateur : retenir −75%.

### 3.6 Calcul breakeven LISA Gemini Pro

**V6.1 = 12 000 tokens cachés** :
- Création (input miss) : 12K × $2/M = **$0.024**
- Stockage 24h : 12K × $4.50/M × 24 = **$1.30/jour**
- Économie par hit (−75%) : 12K × ($2 - $0.50) /M = **$0.018/appel**

**Breakeven TTL 24h** : $1.30 / $0.018 ≈ **72 appels/jour**

→ LISA fait 5-10 appels Pro/jour → **explicit caching 24h = perte nette**

**Stratégies viables** :
1. **Implicit caching seul** (recommandé) : zéro stockage, 75-90% off automatique si appels rapprochés
2. **TTL 10-15 min refresh on-demand** : breakeven < 4 appels par fenêtre
3. **Si volume monte > 30 appels/jour** : explicit avec TTL 1h refresh → rentable

### 3.7 Pièges Gemini

- **Byte-sensitive** : virgule, espace, ordre clés JSON, retour ligne → cache miss
- **Région** : cache lié à la région de création (pas portable)
- **Mise à jour modèle** : nouveau snapshot → caches non migrés
- **Thinking tokens** : facturés au tarif output, **non impactés par le caching** (seul l'input bénéficie)

### 3.8 Vertex AI vs Gemini API direct

| Aspect | Gemini API direct | Vertex AI |
|--------|-------------------|-----------|
| Auth | API key | Service account / ADC |
| Régions cache | Global | Régional |
| SDK recommandé | `google-genai` | `google-genai` (vertexai=True) |
| Pour LISA | Non (prod) | **Oui** (service account `lisa-496301`) |

---

## 4. Décisions et patterns LISA

### 4.1 Sonnet 4.6 orchestrateur

```python
import anthropic
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT_LISA,  # ~5K tokens stable
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    messages=[
        {"role": "user", "content": f"Facture: {invoice_path}"}
    ]
)
```

**Pourquoi TTL 1h plutôt que 5min** :
- 110 appels/jour ≈ 1 toutes les 13 min en moyenne
- Si appels uniformément espacés > 5min → cache 5min meurt à chaque fois
- TTL 1h : 1 write/h (2× input) + 23 reads/h gratuits (0.1×) = -85% sur l'input

### 4.2 Opus 4.7 calibration

```python
response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=8192,
    system=[
        {
            "type": "text",
            "text": CALIBRATION_INSTRUCTIONS,  # ~10K tokens
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    messages=[{
        "role": "user",
        "content": [
            {"type": "document", "source": {...}},  # facture 1
            {"type": "document", "source": {...}},  # facture 2
            {"type": "document", "source": {...}},  # facture 3
            {"type": "document", "source": {...}},  # facture 4
            {"type": "document", "source": {...},
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},  # facture 5 = breakpoint
            {"type": "text", "text": f"Script actuel:\n{current_script}\n\nGénère un script déterministe pour ces 5 factures."}
        ]
    }]
)
```

**2 breakpoints** :
1. Sur le system prompt (instructions calibration stables)
2. Sur la 5e facture (rolling window — change uniquement quand nouvelle facture arrive)

### 4.3 Gemini 3 Flash split-and-route

```python
from google import genai
client = genai.Client(vertexai=True, project="lisa-496301", location="us-central1")

# Pas d'explicit caching (sous le seuil 4K)
# Compter sur l'implicit. Garantir byte-stability du system prompt.

VISION_SPLIT_PROMPT = open("prompts/vision_split.txt", "rb").read().decode("utf-8")  # immutable

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    config={
        "system_instruction": VISION_SPLIT_PROMPT,
        "temperature": 0.0,
    },
    contents=[
        {"mime_type": "application/pdf", "data": pdf_bytes},
        "Indique pour ce document le nombre de factures, leurs numéros, fournisseurs, complétude."
    ]
)

# Vérifier hit rate
print(f"cached={response.usage_metadata.cached_content_token_count}")
```

### 4.4 Gemini 3.1 Pro fallback V6.1

Phase 1 (court terme, volume bas) :
```python
PROMPT_V6_1 = open("prompts/lisa_gemini_v6_1.txt", "rb").read().decode("utf-8")

response = client.models.generate_content(
    model="gemini-3.1-pro-preview",
    config={
        "system_instruction": PROMPT_V6_1,  # implicit caching uniquement
        "temperature": 0.0,
        "thinking_config": {"thinking_level": "medium"},
    },
    contents=[
        {"mime_type": "application/pdf", "data": pdf_bytes},
        "Extrais cette facture au schéma SYDONIA NC."
    ]
)
```

Phase 2 (si volume > 30 appels/jour) — activer explicit caching :
```python
from google.genai.types import CreateCachedContentConfig

cache = client.caches.create(
    model="gemini-3.1-pro-preview",
    config=CreateCachedContentConfig(
        system_instruction=PROMPT_V6_1,
        display_name="lisa-v6.1-fallback",
        ttl="3600s",  # 1h, refresh à chaque appel
    ),
)
# Stocker cache.name (Redis ou fichier état)
# Refresh TTL avant expiration
# Recréer en cas de NotFound
```

### 4.5 Haiku 4.5 heartbeat — pas de caching

Le seuil 4 096 tokens est trop élevé pour un heartbeat (~200 tokens). Le cache ne prendrait pas. Comportement : `cache_creation_input_tokens == 0` et `cache_read_input_tokens == 0` — aucune erreur, simplement pas de cache.

---

## 5. Snippets Python prêts à coller

### 5.1 Validation cache hit Anthropic

```python
def log_anthropic_cache_metrics(response, model_name: str):
    """Log les métriques de cache pour audit."""
    u = response.usage
    total_input = (
        u.cache_read_input_tokens 
        + u.cache_creation_input_tokens 
        + u.input_tokens
    )
    hit_ratio = u.cache_read_input_tokens / max(total_input, 1)
    
    logger.info(
        f"{model_name} cache_hit_ratio={hit_ratio:.2%} "
        f"read={u.cache_read_input_tokens} "
        f"create={u.cache_creation_input_tokens} "
        f"fresh={u.input_tokens} "
        f"out={u.output_tokens}"
    )
    
    if hit_ratio < 0.7 and total_input > 4096:
        logger.warning(f"{model_name}: cache hit ratio low ({hit_ratio:.2%}). Investigate prompt stability.")
```

### 5.2 Pre-warm cron pour Sonnet

```python
def prewarm_sonnet_cache():
    """Lancé par cron Haiku toutes les 4 min entre 7h et 22h Europe/Paris."""
    try:
        anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=0,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT_LISA,
                "cache_control": {"type": "ephemeral", "ttl": "1h"}
            }],
            messages=[{"role": "user", "content": "warmup"}]
        )
    except Exception as e:
        logger.error(f"Sonnet prewarm failed: {e}")
```

### 5.3 Validation byte-stability Gemini

```python
import hashlib

def assert_prompt_stable(prompt_path: str, expected_hash: str):
    """Garantit qu'un prompt n'a pas changé entre 2 runs."""
    with open(prompt_path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if h != expected_hash:
        raise ValueError(
            f"Prompt {prompt_path} hash mismatch! "
            f"got={h} expected={expected_hash}. Cache will miss."
        )

# Au démarrage du gateway LISA :
assert_prompt_stable("prompts/lisa_gemini_v6_1.txt", "abc123...")
```

---

## 6. Monitoring et garde-fous

### 6.1 Métriques critiques par modèle

| Modèle | Métrique | Cible saine | Alerte si |
|--------|----------|-------------|-----------|
| Sonnet 4.6 | `cache_read_input_tokens / total_input` | > 80% | < 70% |
| Opus 4.7 | `cache_read_input_tokens / total_input` | > 70% | < 50% |
| Gemini 3 Flash | `cached_content_token_count / prompt_token_count` | > 50% | < 30% |
| Gemini 3.1 Pro | `cached_content_token_count / prompt_token_count` | > 50% | < 20% |

### 6.2 Garde-fous opérationnels

1. **Hash check des prompts au démarrage** (cf. snippet 5.3). Si un prompt a dérivé sans qu'on s'en rende compte, cache miss garanti.
2. **Alerte Telegram quotidienne** si cache hit ratio < seuil sur 24h glissantes (job Haiku cron).
3. **Pas de timestamp dans les system prompts** — toujours dans le user message si besoin.
4. **Versionner les prompts dans Git** avec contrôle d'intégrité (hash dans le repo).
5. **Workspace Anthropic unique** pour dev/staging/prod (pas de splits qui empêcheraient le partage de cache si on a un fallback inter-env).

### 6.3 Économies estimées LISA (sur l'année)

Hypothèses : 40 000 factures/an, 110/jour, prix mai 2026.

| Modèle | Sans cache | Avec cache | Économie |
|--------|-----------|-----------|----------|
| Sonnet 4.6 orch | ~$600/an | ~$70/an | ~$530/an |
| Opus 4.7 calib | ~$300/an | ~$45/an | ~$255/an |
| Gemini Flash split | ~$30/an | ~$15/an | ~$15/an |
| Gemini Pro fallback | ~$150/an | ~$130/an | ~$20/an |
| **Total** | **~$1 080/an** | **~$260/an** | **~$820/an (~76%)** |

> Ordre de grandeur uniquement, à valider par instrumentation prod.

---

## Sources

**Anthropic** :
- [Prompt caching - Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Pricing - Claude API Docs](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic API Pricing 2026 - finout.io](https://www.finout.io/blog/anthropic-api-pricing)
- [Automatic Prompt Caching - Medium](https://medium.com/ai-software-engineer/anthropic-just-fixed-the-biggest-hidden-cost-in-ai-agents-using-automatic-prompt-caching-9d47c95903c5)

**Gemini** :
- [Context caching - Gemini API](https://ai.google.dev/gemini-api/docs/caching)
- [Vertex AI context caching blog](https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching)
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [Gemini 3.1 Pro Pricing — Verdent](https://www.verdent.ai/guides/gemini-3-1-pro-pricing)
- [Gemini 3 Flash pricing — pricepertoken](https://pricepertoken.com/pricing-page/model/google-gemini-3-flash-preview)
- [Gemini 2.5 implicit caching - Google Devs Blog](https://developers.googleblog.com/gemini-2-5-models-now-support-implicit-caching/)
- [Authenticate to Vertex AI](https://docs.cloud.google.com/vertex-ai/docs/authentication)

---

*Document compilé le 15 mai 2026 — version 1.0 — sera actualisé si Anthropic ou Google publient des évolutions majeures (nouveaux modèles, changement de pricing, nouvelles fonctionnalités caching).*
