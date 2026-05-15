# OpenClaw config pour LISA V3

Ce dossier contient la configuration OpenClaw à déployer sur le VPS LISA.

## Fichiers

| Fichier | Destination VPS |
|---|---|
| `openclaw.json` | `~/.openclaw/openclaw.json` |
| `AGENTS.md` | `~/.openclaw/agents/lisa/AGENTS.md` |
| `SOUL.md` | `~/.openclaw/agents/lisa/SOUL.md` |

Les SKILL.md des 3 skills sont dans `openclaw_skills/` et doivent être déployés dans `/opt/lisa/skills/lisa-extraction/SKILL.md`, etc.

## Variables d'environnement à substituer

`openclaw.json` utilise `${VAR_NAME}` substitué depuis `/etc/lisa/openclaw.env` :

- `OPENCLAW_GATEWAY_TOKEN` — token gateway (générer via `openclaw doctor --generate-gateway-token`)
- `ANTHROPIC_API_KEY` — clé Anthropic primaire (workspace LISA)
- `ANTHROPIC_API_KEY_BACKUP` — clé secondaire pour rotation rate-limit
- `TELEGRAM_BOT_TOKEN` — token bot BotFather
- `KEVIN_TELEGRAM_USER_ID` — user_id numérique whitelisté

## Déploiement

```bash
# Sur le VPS (user openclaw)
sudo systemctl stop openclaw-gateway
cp openclaw.json ~/.openclaw/openclaw.json
mkdir -p ~/.openclaw/agents/lisa
cp AGENTS.md SOUL.md ~/.openclaw/agents/lisa/
chmod 600 ~/.openclaw/openclaw.json

# Skills
sudo mkdir -p /opt/lisa/skills
sudo cp -r openclaw_skills/lisa-* /opt/lisa/skills/
sudo chown -R openclaw:openclaw /opt/lisa/skills

# Token telegram en mode fichier (chmod 600)
echo "${TELEGRAM_BOT_TOKEN}" | sudo tee /etc/lisa/telegram.token
sudo chmod 600 /etc/lisa/telegram.token
sudo chown openclaw:openclaw /etc/lisa/telegram.token

# Validation
openclaw doctor --deep
openclaw security audit --deep --fix
openclaw models status --probe
openclaw skills list

# Restart
sudo systemctl start openclaw-gateway
```
