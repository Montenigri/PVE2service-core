# Plugin Store — guida operativa

Implementazione del documento [`pve2services-plugin-store.md`](../pve2services-plugin-store.md) (MVP, via **trusted**):
manifest per plugin, validazione del loader, firma ed25519, bucket S3/MinIO con
`index.json` e installer automatico all'avvio. La via **third-party** (repo
esterne, pin a commit, isolamento subprocess) resta prevista in roadmap: il
loader già classifica ogni pacchetto senza firma verificabile come
`third_party`, quindi l'apertura futura non richiede riscritture.

## Architettura

```
release CI                       bucket S3/MinIO                  installazione locale
┌────────────────────┐  firma   ┌────────────────────────────┐  ┌────────────────────┐
│ tools/build_plugin │ ────────▶│ <plugin_id>/<version>/     │  │ plugins/<id>/      │
│  (zip + sha256 +   │          │   package.zip              │─▶│  __init__.py       │
│   firma ed25519)   │─upload──▶│   plugin.yaml              │  │  plugin.yaml       │
└────────────────────┘  upload  │   plugin.yaml.sig          │  │  plugin.yaml.sig   │
                           +    │ index.json                 │  │  .pve2_provenance  │
                        energet │ └──────────────────────────┘  └────────────────────┘
                         (index) │
                                                                       │
                                                                       ▼
                                                       autodiscovery (INVARIATA)
                                                       valida manifest → import
```

Principi chiave:

1. **Discovery e distribuzione sono separati.** L'autodiscovery legge la
   stessa cartella `plugins/` di sempre; lo store la popola automaticamente.
2. **Il tier effettivo è derivato dal loader, mai dal manifest.** Il campo
   `trust_tier` in `plugin.yaml` è solo informativo per l'utente.
3. **Fail-closed a ogni verifica**: firma, checksum, compatibilità core.
   Qualunque fallimento blocca l'installazione e non tocca lo stato esistente.
4. **Mai fatale al boot**: problema di store o di un plugin influenza solo quel
   plugin; il core parte comunque.

## Come viene derivato il trust tier

| Provenienza | `.sig` valida | bundled (release) | tier |
|---|---|---|---|
| Installato dallo store | sì | — | **trusted** |
| Directory in `plugins/bundled_plugins.txt` | — | sì | **trusted** |
| Plugin first-party legacy, manifest assente | — | sì | trusted + warning |
| Cartella calata a mano, senza firma | no | no | **third_party** |
| Firma alterata o chiave esterna | no | — | **third_party** |

- `bundled_plugins.txt` elenca i plugin di prima parte che fanno parte dello
  stesso artefatto di release del core (git tag / immagine Docker): condividono
  il dominio di fiducia di chi costruisce l'immagine.
- I pacchetti installati dallo store portano `plugin.yaml.sig`: firma ed25519
  sui byte di `plugin.yaml`, verificata contro `keys/plugin_signing.pub`.
- Un pacchetto firmato "trusted" nel bucket ma copiato a mano in `plugins/`
  senza `.sig` rimane third_party — conta il canale verificato, non
  l'etichetta.

## Schema package (`s3://<bucket>/<plugin_id>/<version>/`)

| File | Contenuto |
|---|---|
| `package.zip` | solo il **codice** del plugin (niente manifest, niente cache) |
| `plugin.yaml` | manifest con `checksum: sha256:<hex di package.zip>` |
| `plugin.yaml.sig` | firma base64 ed25519 sui byte del manifest |
| `index.json` | catalogo: `{schema_version, generated_at, plugins: [{id, name, versions: [{version, checksum, core_compat, channel, published_at}]}]}` |

Perché il manifest non è dentro lo zip: il checksum dichiarato nel manifest è
l'hash dello zip stesso, quindi includere il manifest nello zip è circolare
(l'hash dipenderebbe dal contenitore del campo stesso). L'installer ricompone
l'unità completa su disco (codice più manifest firmato più `.sig`), esattamente
l'unità che il loader si aspetta.

## Configurazione (variabili d'ambiente)

Installer (lato core, eseguito all'avvio prima della discovery):

```bash
PVE2_PLUGIN_STORE_URL="http://minio:9000"       # richiesto per abilitare
PVE2_PLUGIN_STORE_BUCKET=pve2services-plugins   # default
PVE2_PLUGIN_STORE_ACCESS_KEY=...                # credenziali S3
PVE2_PLUGIN_STORE_SECRET_KEY=...
PVE2_PLUGIN_STORE_REGION=us-east-1
PVE2_PLUGIN_STORE_VERIFY_TLS=true               # false solo per MinIO self-signed
PVE2_PLUGINS_INSTALL="pve2dns,pve2power==1.0.0" # id (latest) oppure id==version
PVE2_PLUGINS_FORCE=false                        # reinstalla anche a stessa version
PVE2_PLUGIN_PACKAGE_MAX_BYTES=104857600         # cap download (100 MiB)
PVE2_PLUGIN_EXTRACT_MAX_BYTES=209715200         # cap estrazione (200 MiB)
```

Firma (loader tools):

```bash
PVE2_PLUGIN_SIGNING_PUBKEY=/path/publica.pem   # override; default keys/plugin_signing.pub
PVE2_PLUGIN_SIGNING_KEY=/path/privata.pem      # SOLO tooling di build/release, mai repo
PVE2_PLUGINS_DIR=/percorso/plugins             # caricare plugin da checkout esterno
                                               # (es. repo PVE2Services-Plugin in dev)
```

Senza `PVE2_PLUGIN_STORE_URL` l'installer è un no-op: il comportamento resta
identico a quello pre-migrazione.

## Flusso di rilascio (CI)

1. Generare una volta la keypair:

```bash
python tools/generate_keys.py keys/plugin_signing.pub pve2_signing.pem
```

Chiave **pubblica** committata nel repo; **privata** solo nei GitHub Secrets
(`PVE2_PLUGIN_SIGNING_KEY`) o in un deposito locale sicuro.

2. Secret bucket: `PVE2_STORE_ENDPOINT`, `PVE2_STORE_BUCKET`,
   `PVE2_STORE_ACCESS_KEY`, `PVE2_STORE_SECRET_KEY`.
3. I workflow sono divisi tra le due repo:

   **Repo core (`PVE2Services`)** — al tag `v*`, `.github/workflows/release.yml`:
   - fa checkout di `PVE2Services-Plugin` al ref pinnato (`vars.PVE2_PLUGINS_REF`),
   - copia in `PVE2Services/plugins/` i plugin elencati in `bundled_plugins.txt`
     (i plugin first-party open restano **bundled**, non passano dallo store),
   - build e push dell'immagine Docker e (opzionale) pubblica il wheel `pve2`.

   **Repo plugin (`PVE2Services-Plugin`)** — al tag `v*`,
   `.github/workflows/release.yml`:
   - scrive la chiave privata dal secret `PVE2_PLUGIN_SIGNING_KEY`,
   - lancia `pve2-build-plugin plugins/<id> --out build --publish` per ogni
     plugin elencato in `plugins/store_plugins.txt`: build, firma, upload dei
     pacchetti e merge di `index.json` nel bucket.

   I plugin first-party **closed** e quelli **terze parti** seguono lo stesso
   percorso store: sorgenti in una repo separata, firmati (i closed con la
   chiave del progetto) e installati via `PVE2_PLUGINS_INSTALL`.

Uso locale:

```bash
python tools/build_plugin.py plugins/PVE2DNS --signing-key "$HOME/secrets/pve2_signing.pem"
# artefatti in build/pve2dns/<version>/ + build/index.json
```

## Ambiente di sviluppo: MinIO locale

`docker-compose.plugin-store-dev.yml` avvia un MinIO locale:

```bash
docker compose -f docker-compose.plugin-store-dev.yml up -d
export PVE2_STORE_ENDPOINT=http://localhost:19000
export PVE2_PLUGIN_SIGNING_KEY="$HOME/secrets/pve2_signing.pem"
python tools/build_plugin.py plugins/PVE2DNS --out build --publish
```

## Supervisione: API e UI

- `GET /api/hub/plugins` (autenticata) — elenco dei plugin dal registry:
  `{id, name, version, trust_tier, declared_tier, capabilities, loaded,
  signed, icon, description}`. L'hub mostra il tier effettivo (badge verde
  "trusted" o ambra "third party") e le capabilities dichiarate.
- Un plugin con errore di caricamento compare in `/api/hub/plugins` con
  `loaded: false` e `load_error` — utile come primo strumento di
  troubleshooting, senza downtime del core.

## Sicurezza: cosa proteggono i controlli

- **Firma/chiave pubblico** solo il possessore della chiave privata del
  progetto può produrre un pacchetto trusted — il `trust_tier` scritto nel
  manifest è ignorato a questo scopo. La chiave pubblica è dentro
  l'immagine/repo per scelta.
- **Checksum**: il pacchetto scaricato deve corrispondere ai byte censiti nel
  manifest firmato — difesa da corruzione in transito sino all'estrazione.
- **Estrazione sicura**: niente `..`, percorsi assoluti, NUL byte, o archivi
  che superano il cap di dimensione (zip-bomb). I fallimenti cancellano lo
  staging senza lasciare residui.
- **Atomic swap**: esistenza di un vecchio `plugins/<id>` non lascia mai
  estensioni a metà — stage, backup, rename, cleanup con ripristino in caso
  di errore.
- **Repliche HA**: il sync è idempotente; se due repliche lo eseguono in
  parallelo ottengono lo stesso contenuto byte-per-byte, quindi l'esito è
  innocuo anche in last-writer-wins.

## Casi limite coperti

| Caso | Comportamento |
|---|---|
| Manifest YAML roto, id/version non conformi | Plugin saltato con log ERROR/WARNING, core parte |
| Plugin bundled senza (buon) manifest | Caricato con fallback, warning (contratto legacy) |
| `core_compat` fuori range | Plugin saltato, motivo nel registry |
| Cartella `PVE2DNS` installata con name-case diverso | Already-satisfied compara case-insensitive |
| Id nel manifest diverso dalla chiave cartella | Store: fallisce (id verificato); locale: warning |
| Due cartelle che mappano la stessa chiave | Risolti deterministicamente: firmato > bundled > altro |
| `PVE2_PLUGINS_INSTALL` malformato | Sync disabilitato per questa esecuzione, core parte |
| Endpoint irraggiungibile / 404 | Errori nel report, core parte uguale |
| Zip con `../evil.txt` | Rifiutato, nulla scritto fuori |
| Firmato con chiave esterna | Sempre `third_party` |
| Versione pinata assente in index | Errore solo per quel plugin, gli altri continuano |

## Rimane in roadmap (via third-party)

- Repository esterne via URL con pin obbligatorio a commit.
- Isolamento sottoprocesso/gRPC per il canale third-party.
- Enforcement delle capabilities (oggi solo dichiarazione).
- Licensing server per i plugin trusted a pagamento (`license_required` è
  già nello schema del manifest, non attivo).
