# Format de sortie LISA (scope simplifié)

Document de référence. Date : 14 mai 2026.

## Périmètre de LISA (phase 1)

LISA ne fait QUE l'extraction PDF → JSON. Pas de FTP, pas de transformation EDI SIFA.
La transformation vers le format SQL proc (`proc_AchatEDIFactureImport_Creation`) sera faite par un autre système qui consommera le JSON produit.

## Flux

```
Drive Inbox/         → LISA pipeline   → Drive Outbox/<facture>.json
  facture.pdf                            (PDF source archivé dans Drive Archive/YYYYMM/)

                                          OU si extraction échoue :
                                          Drive Quarantine/<facture>.pdf
```

## Format JSON de sortie

Voir [JSON_exemple_complet.json](../uploads/JSON_exemple_complet.json) pour la référence complète.

Structure :

```json
{
  "meta": {
    "product": "level_1_native | level_2_scan | gemini",
    "source": "nom du PDF source",
    "id_fex": "524875",
    "region": "NC | PF",
    "processed_at": "2026-05-14T16:30:00+11:00"
  },
  "invoices": [
    {
      "header": {
        "date": "2026-04-14",
        "num": "F26LG02709",
        "supplier": "MC3 LOGISTIQUE SAS",
        "recipient": "MC3 NOUVELLE CALEDONIE",
        "total_ht": "1856,72",
        "currency": "EUR",
        "gross_weight": "48,500",
        "net_weight": "42,100",
        "volume": "0,185",
        "dossier": "207708",
        "dof": "L'exportateur déclare...",
        "rex": "FRREX20207457"
      },
      "lines": [
        {
          "ref": "BRO_LC3211BK",
          "label": "CRT NOIR 200 PAGES...",
          "hs_code": "8443991000",
          "origin": "PH",
          "qty": 2,
          "unit_price": "9,47",
          "amount": "18,94",
          "packages": 1
        },
        {
          "ref": "3560070048786",
          "label": "HEINEKEN 24X330ML",
          "ean": "3560070048786",
          "hs_code": "2203000100",
          "origin": "NL",
          "qty": 10,
          "unit_price": "22,50",
          "amount": "225,00",
          "alcohol": "5,00",
          "units": 24,
          "weight": "0,330",
          "packages": 2
        },
        {
          "type": "xfee",
          "ref": "XFEE",
          "label": "FRAIS EXCLUS - REMISE",
          "qty": 1,
          "amount": "19,00"
        }
      ]
    }
  ]
}
```

## Champs du `header`

| Champ | Obligatoire | Format | Notes |
|---|---|---|---|
| `date` | Oui | `YYYY-MM-DD` | Date facture |
| `num` | Oui | string | N° facture fournisseur |
| `supplier` | Oui | string | Nom exact du fournisseur |
| `recipient` | Oui | string | Destinataire (filiale SIFA) |
| `total_ht` | Oui | string décimal virgule | Ex `"1856,72"` |
| `currency` | Oui | code ISO 3 | EUR, USD, etc. |
| `gross_weight` | Si dispo | string décimal | kg |
| `net_weight` | Si dispo | string décimal | kg |
| `volume` | Si dispo | string décimal | m³ |
| `dossier` | Si dispo | string | N° dossier SIFA |
| `dof` | Si présent | string | Déclaration d'origine |
| `rex` | Si présent | string | Numéro REX |

## Champs des `lines`

| Champ | Obligatoire | Format | Notes |
|---|---|---|---|
| `ref` | Oui | string | Référence article |
| `label` | Oui | string | Désignation |
| `hs_code` | Si dispo | string numérique | Code SH |
| `origin` | Si dispo | ISO 2 | Pays origine |
| `qty` | Oui | number | Quantité |
| `unit_price` | Oui | string décimal virgule | Prix unitaire HT |
| `amount` | Oui | string décimal virgule | Montant ligne HT |
| `packages` | Si dispo | number | Nombre de colis |
| `ean` | Si dispo | string numérique | Code-barres EAN |
| `alcohol` | Si applicable | string décimal | Degré d'alcool |
| `sugar` | Si applicable | string décimal | Taux de sucre |
| `weight` | Si dispo | string décimal | Poids unitaire |
| `units` | Si dispo | number | Unités par colis |

## Type spécial `xfee`

Ligne représentant frais exclus / remises / escomptes :

```json
{ "type": "xfee", "ref": "XFEE", "label": "...", "qty": 1, "amount": "..." }
```

## Méta-données

Ajoutées par le pipeline LISA :

| Champ | Format | Description |
|---|---|---|
| `meta.product` | enum | `level_1_native`, `level_2_scan`, `gemini` |
| `meta.source` | string | Nom du PDF source (ex `facture_mc3_20260414.pdf`) |
| `meta.id_fex` | string | ID Facture Expéditeur, à extraire si présent |
| `meta.region` | enum `NC` / `PF` | Société destinataire (à déduire du recipient) |
| `meta.processed_at` | ISO 8601 | Timestamp UTC ou Pacific/Noumea du traitement |

## Conventions

- Encodage fichier : **UTF-8 sans BOM**
- Nom de fichier : `<num_facture>_<source_basename>.json` (ex `F26LG02709_facture_mc3.json`)
- Décimaux : **virgule** dans les strings (cohérent avec FR/SIFA)
- Pas de pretty-print en prod (économie d'espace), pretty-print en debug

## Étapes du pipeline

1. Pull Drive Inbox → /opt/lisa/processing/<facture>.pdf
2. Sanitize (exiftool + qpdf)
3. Classify (level 1/2/3 + détection fournisseur)
4. Extract (selon niveau)
5. Validate (math, complétude)
6. Build JSON conforme à ce document
7. Push Drive Outbox/<num>_<source>.json
8. Move PDF original → Drive Archive/YYYYMM/
9. Si échec → Drive Quarantine/ + notif Telegram
