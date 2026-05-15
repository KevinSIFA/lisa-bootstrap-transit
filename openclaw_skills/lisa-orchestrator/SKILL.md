---
name: lisa_orchestrator
description: Pilote la queue + drive I/O + Telegram. Use for queue inspection, status checks, push to Drive Outbox, archive moves, alerts.
user-invocable: true
command-dispatch: tool
command-tool: exec
command-arg-mode: raw
metadata: { "openclaw": { "requires": { "bins": ["python3"], "env": ["LISA_HOME", "TELEGRAM_BOT_TOKEN"] } } }
---

# LISA — Skill orchestrateur (queue + Drive + alertes)

Tu gères la queue de traitement, les I/O Drive, et les alertes Telegram. C'est un skill "plomberie" utilisé pour les opérations courantes.

`command-dispatch: tool` + `command-tool: exec` + `command-arg-mode: raw` : quand Kevin tape depuis Telegram `/lisa_orchestrator queue-stats`, OpenClaw envoie directement `python3 -m lisa_pipeline queue-stats` à exec **sans passer par le modèle**. Économise tokens + latence.

## Outils

### Queue
| Commande | Rôle |
|---|---|
| `python3 -m lisa_pipeline queue-stats` | Compte par statut |
| `python3 -m lisa_pipeline queue-next` | Récupère la prochaine facture pending |
| `python3 -m lisa_pipeline queue-add <filename> [--drive-id ID]` | Enqueue manuel |

### Drive
| Commande | Rôle |
|---|---|
| `python3 -m lisa_pipeline drive-pull --max N` | Tire jusqu'à N PDFs de Inbox vers `/opt/lisa/inbox/` |
| `python3 -m lisa_pipeline drive-push <json>` | Pousse résultat dans Outbox |

### Catalogue / status
| Commande | Rôle |
|---|---|
| `python3 -m lisa_pipeline catalogue-list` | Liste fournisseurs + stats |
| `python3 -m lisa_pipeline catalogue-meta <slug>` | Détail meta.yaml d'un fournisseur |
| `python3 -m lisa_pipeline catalogue-health <slug> <doc_type>` | État health d'un script |

### Grimoire
| Commande | Rôle |
|---|---|
| `python3 -m lisa_pipeline grimoire-list [--slug X] [--category Y]` | Liste leçons (debug/admin) |
| `python3 -m lisa_pipeline grimoire-query <slug>` | Query par fournisseur |

## Workflow

Tu es invoqué pour des actions atomiques précises, pas pour orchestrer le flow d'extraction (c'est `lisa_extraction` qui pilote ça).

Exemples d'usages :
- Kevin tape `/lisa_orchestrator queue-stats` → tu retournes le JSON
- `/lisa_orchestrator catalogue-health bertrand_export natif` → état du script natif
- `/lisa_orchestrator drive-pull --max 5` → tire 5 PDFs Inbox

## Garde-fous

- **Pas de pull > 10 jobs simultanés** (`--max 10` max)
- **drive-push** ne se fait QUE depuis un JSON validé math au préalable
- **Pas d'écriture libre** dans `/opt/lisa/secrets/`, `/etc/lisa/`, `~/.openclaw/`
