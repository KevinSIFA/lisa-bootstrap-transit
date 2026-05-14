# LISA Bootstrap

Prepare un VPS Ubuntu 24.04 vierge pour heberger l'agent LISA.

## Structure

```
bootstrap/
├── bootstrap-lisa.sh        # Orchestrateur principal
├── .env.bootstrap           # Configuration reelle (SECRET, ne pas committer)
├── .env.bootstrap.example   # Template documente
├── lib/
│   └── common.sh            # Fonctions partagees (log, marqueurs, etc.)
├── modules/                 # Sera rempli dans les phases suivantes
└── README.md                # Ce fichier
```

## Prerequis

- VPS Hostinger KVM 4 (ou equivalent) : 8 vCPU / 16 Go RAM / 200 Go NVMe
- Ubuntu 24.04 LTS frais
- Acces root SSH
- Fichier `.env.bootstrap` rempli (copier depuis `.env.bootstrap.example`)
- Cle SSH du compte de service Google deposee a `/opt/lisa/secrets/lisa-service-account.json`
- Cle SSH deploy GitHub deposee a `/opt/lisa/secrets/lisa_deploy_key`

## Procedure d'execution

### 1. Uploader le dossier bootstrap sur le VPS

Depuis ton PC :

```powershell
scp -r bootstrap root@187.127.107.127:/root/
```

### 2. Uploader les fichiers secrets

```powershell
ssh root@187.127.107.127 "mkdir -p /opt/lisa/secrets"
scp "C:\Users\kbramoulle\OneDrive - SIFA\Documents\Claude\Projects\LISA V2\secrets\lisa-service-account.json" root@187.127.107.127:/opt/lisa/secrets/
scp "C:\Users\kbramoulle\.ssh\lisa_deploy_key" root@187.127.107.127:/opt/lisa/secrets/
ssh root@187.127.107.127 "chmod 600 /opt/lisa/secrets/*"
```

### 3. Se connecter au VPS et lancer le bootstrap

```bash
ssh root@187.127.107.127
cd /root/bootstrap

# Lister les modules et leur etat
./bootstrap-lisa.sh --list

# Voir ce qui sera fait sans rien executer
./bootstrap-lisa.sh --dry-run

# Lancer pour de vrai
./bootstrap-lisa.sh
```

## Commandes utiles

| Commande | Effet |
|---|---|
| `./bootstrap-lisa.sh --list` | Etat des modules |
| `./bootstrap-lisa.sh --dry-run` | Simulation sans modification |
| `./bootstrap-lisa.sh` | Execute tous les modules manquants |
| `./bootstrap-lisa.sh --step 05-openclaw` | Re-execute uniquement le module 05 |
| `./bootstrap-lisa.sh --force` | Re-execute TOUS les modules (efface les marqueurs) |

## Idempotence

Chaque module termine pose un **marqueur** dans `/var/lib/lisa/state/.installed-XX-name`.
Au prochain run, les modules avec marqueur sont automatiquement saute, sauf avec `--force`.

Si un module echoue : corrige le probleme, relance le script, il reprend ou il s'est arrete.

## Logs

Tous les logs (avec timestamps) sont dans :

```
/var/log/lisa/bootstrap.log
```

Console = couleurs et resume. Fichier = trace integrale.

## Modules (a livrer)

| # | Module | Role |
|---|---|---|
| 01 | prerequisites | Verifs OS, RAM, disque, internet |
| 02 | system-security | User openclaw, SSH keys, UFW, fail2ban, unattended-upgrades |
| 03 | os-tools | Tesseract, OpenCV, exiftool, qpdf, sqlite-vec, git |
| 04 | python-stack | venv + pip (PyMuPDF, pandas, anthropic, google-cloud, etc.) |
| 05 | openclaw | Node.js + OpenClaw >= 2026.4.23, gateway loopback strict |
| 06 | network | Tailscale (cle d'auth) + pass (coffre secrets) |
| 07 | monitoring | Netdata bind loopback, alertes Telegram natives |
| 08 | workspace | Arborescence /opt/lisa/, grimoire sqlite-vec, queue persistante |
| 09 | systemd | Services + crons |
| 10 | validation | Healthcheck final |

## Securite

- **Jamais de secret en clair dans `bootstrap-lisa.sh`** : tout vient de `.env.bootstrap`
- **Marqueurs et logs en mode 750** : lisibles par root uniquement
- **Gateway OpenClaw loopback strict** : jamais bind 0.0.0.0 (CVE-2026-25253)
- **OpenClaw >= 2026.4.23** : versions inferieures refusees (CVE-2026-45006)
- **UFW** : tout ferme sauf SSH (et seulement via Tailscale apres bootstrap)

## En cas de probleme

1. Consulte `/var/log/lisa/bootstrap.log` (trace complete)
2. Relance le module ou tu as bloque : `./bootstrap-lisa.sh --step XX-nom`
3. Si situation cassee : restaure le snapshot Hostinger et recommence
