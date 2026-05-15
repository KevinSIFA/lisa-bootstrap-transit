# Tests LISA V3

## test_e2e_manual.sh

Procédure de validation manuelle E2E sur une facture test.

### Pré-requis

1. VPS LISA déployé (bootstrap modules 01-10 OK)
2. Module 04 V3 (google-genai + sentence-transformers) installé
3. Module 05 V3 (openclaw.json V3 + 3 SKILL.md + prompts) déployé
4. `/etc/lisa/openclaw.env` rempli avec vraies clés API
5. `/etc/lisa/telegram.token` rempli
6. Repo LISA V2 cloné dans `/opt/lisa/lisa-v2-repo`
7. Factures de test copiées dans `/opt/lisa/test-factures/`

### Lancement

```bash
sudo -iu openclaw
cd /opt/lisa/lisa-v2-repo
bash tests/test_e2e_manual.sh /opt/lisa/test-factures/FEX-DOC-000000009997.pdf
```

Le script s'arrête entre chaque étape avec un `read` — appuyer Entrée pour continuer.
Cela permet d'inspecter le JSON retourné par chaque commande avant de passer à la suivante.

### Étapes

1. **sanitize** : qpdf + exiftool → PDF safe dans `/opt/lisa/processing/`
2. **vision-split** : Gemini Flash, split + détection complétude
3. **identify-supplier** : slug + embedding + décision matched/new/review
4. **classify-type** : natif / scan_propre / scan_difficile
5. **apply-script** : tente le script catalogue (peut échouer)
6. **repair-script** : si apply échoue, Opus génère/répare un script
7. **gemini-fallback** : dernier recours si repair échoue
8. **catalogue + health** : vérifier que la facture est intégrée correctement

### Que vérifier

- Pas d'erreur Python dans les logs
- Le JSON V6.1 produit passe `validate-math` (Σ + xfee == total_ht)
- Le catalogue contient bien le fournisseur après identify
- Le sample est bien dans `samples/<doc_type>/` après catalogue-add-sample
- Si Opus a tourné : le script généré est dans `catalogue/<slug>/method_<type>.py`
- Pas de coût LLM excessif (vérifier `tokens_in`/`tokens_out` dans les sorties JSON)

### Cas particuliers

**1ère facture jamais vue** : doit déclencher SEED mode (Opus génère un script à partir de 1 sample)

**Facture native** : apply-script doit marcher direct si le script existe

**Scan difficile** : peut directement basculer en gemini-fallback (Tesseract conf < 60%)

**Multi-fournisseurs** : vision-split doit retourner N invoices, le test continue sur 1 facture splittée à la fois
