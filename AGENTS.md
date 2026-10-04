# PVE2Services — Agent instructions

## Commands

```bash
source .venv/bin/activate          # venv in repo root
pip install -r requirements-dev.txt  # runtime + ruff + pytest + moto
python tools/sync_plugins.py       # fetch first-party plugins into PVE2Services/plugins/
ruff check .                       # lint (only linter; no type checker configured)
pytest -q tests                    # core test-suite (SQLite)
pytest -q tests/test_auth.py       # single file; add -x to stop on first failure
uvicorn PVE2Services.core.wiki_service.app.main:app --reload --port 8000
```

The plugin *sources* live in the separate `PVE2Services-Plugin` repo; the core
repo only tracks `PVE2Services/plugins/bundled_plugins.txt`. Run
`tools/sync_plugins.py` (idempotent, reads that list) before booting locally.
Release images get the same copy step in `.github/workflows/release.yml`.

CI = `ruff check .` + pytest matrix over `PVE2_DB_MODE=sqlite|postgres_docker`
(see `.github/workflows/ci.yml`).

## Import-path gotcha

Code imports as `PVE2Services.*` but the repo root is `PVE2Services-Core`, so
the import package is **nested** at `PVE2Services-Core/PVE2Services/`. Imports
resolve when the repo root is on `sys.path`; `tests/conftest.py` inserts
`parents[1]` (the repo root) for exactly this reason. The Docker image copies
the repo root to `/app` and sets `PYTHONPATH=/app`, so the package lands at
`/app/PVE2Services`. Keep absolute `PVE2Services.*` imports.

The repo also ships the installable `pve2` wheel (`PVE2Services.libs` +
`PVE2Services.core`); the plugin repo installs it in CI.

## Testing quirks

- `tests/conftest.py` defaults to `sqlite:///<repo>/pve2_test.db` but
  **respects a pre-set `PVE2_DB_URL`** — CI's postgres leg sets it explicitly
  and really runs Postgres. Verify locally with:
  `PVE2_DB_URL=postgresql://pve2:pve2pass@127.0.0.1:5432/pve2 pytest -q tests`
- Autouse fixture resets the in-memory login/credentials rate limiters
  (5 attempts / 60 s → 5-min block) between tests.
- Use the `authenticated_client` fixture (runs `/admin/setup` then
  `/admin/login`) for authed requests; plain `client` gets 401/redirects.
  The fixture in `test_auth.py` force-resets the admin password hash — tests
  that change credentials won't break it.
- Mock-data factories live in `tests/helpers.py` (`make_machine`, `make_node`,
  `make_storage`, ...); plugin-store test factories live in
  `tests/test_plugin_loader_helpers.py`. Store tests use `moto` (S3 mock);
  the loader's effective trust tier is exercised with an ephemeral ed25519
  keypair injected via `PVE2_PLUGIN_SIGNING_PUBKEY` (env is monkeypatched,
  so no leak across tests).
- `PVE2_SESSION_SECURE=false` is required for TestClient over http (conftest
  sets it).

## Security model

- **CSRF**: the auth middleware rejects POST/PUT/PATCH/DELETE whose `Origin`
  or `Referer` host differs from the request host (403). Non-browser clients
  sending neither header pass — don't "fix" this by requiring the header.
- **Secrets at rest**: `ConnectorConfig` encrypts every field in
  `SECRET_FIELDS` (Fernet). Key comes from `PVE2_ENCRYPTION_KEY` env or is
  auto-generated into `.pve2_encryption_key` (0600, gitignored); deleting the
  file makes old secrets undecryptable. `PVE2_STRICT_SECRETS=true` refuses to
  store without an explicit key.
- Session secret: `.pve2_secret` (override `PVE2_SECRET_FILE`); deleting it
  invalidates all sessions. Legacy SHA-256 password hashes migrate to bcrypt
  on login. There is **no default admin password** — first boot needs
  `POST /admin/setup`.

## Database

Resolution order in `libs/db_adapter.resolve_db_url()`:
1. `PVE2_DB_URL`
2. `PVE2_DB_MODE=postgres|postgres_docker` → builds URL from
   `PVE2_DB_USER/PASS/HOST/NAME` (defaults `pve2/pve2pass/db/pve2`;
   host `db` matches the compose service)
3. `PVE2_DB_PATH`
4. Fallback: legacy `<repo>/PVE2Wiki/pve2wiki.db`, else `<repo>/pve2.db`

Schema is **portable SQLite + Postgres** (ON CONFLICT upserts, TIMESTAMP,
dialect checks for `rowid`/`lastrowid`/RETURNING). Both engines are covered
by the test suite. New SQL must avoid SQLite-only syntax (`INSERT OR REPLACE`,
`datetime()`, `AUTOINCREMENT`).

## Runtime behavior

- The core machine cache is in-memory but **auto-warms at startup** (with
  jitter) and heals on first empty `/machines` request (single-flight) —
  dependent plugins work right after a restart when pve2core is configured.
- `/pages` CRUD is persisted in the DB `pages` table (survives restarts).
- `/health` pings the DB and returns `ok`/`degraded`.

## Cluster / multi-replica

- Scheduled jobs (main sync, pve2power, pve2notify) are wrapped by a DB
  lease (`libs/distributed.py`, `instance_locks` table, epoch-ms TTLs):
  only the lease holder runs each job; TTL = job interval, so a dead
  leader's jobs are taken over on the next tick. Don't bypass the wrapper
  by calling job functions directly.
- The login/credentials rate limiters are DB-backed (`rate_limit_events`,
  `rate_limit_blocks`) — shared across replicas and restarts. Tests reset
  them via `reset_all_rate_limiters()`, not by poking internals.
- Sessions/secrets are HMAC/Fernet over files — replicas must share
  `.pve2_secret` and `.pve2_encryption_key` (see `docker-compose.ha.yml`).
- Test-isolation rule: tests that mutate `PVE2_DB_URL`/`PVE2_DB_*` env must
  restore them (see `TestResolveDbUrl.restore_env`) — a leaked mutation makes
  later session fixtures bind to the wrong SQLite file.

## Architecture

- **Repo split:** this repo is the core only. First-party plugin sources live
  in the separate **PVE2Services-Plugin** repo; the release workflow checks it
  out at a pinned ref and copies the dirs in `bundled_plugins.txt` into
  `PVE2Services/plugins/` before building the image (local equivalent:
  `tools/sync_plugins.py`). Future/extra plugins arrive at runtime through the
  plugin store (`PVE2_PLUGINS_INSTALL`).
- **Single FastAPI app:** `PVE2Services/core/wiki_service/app/main.py`
  (plugins dir configurable via `PVE2_PLUGINS_DIR` for loading a checkout
  outside the image). Plugin discovery
  lives in `libs/plugin_loader.py`: at import time it scans `plugins/*` dirs
  containing `__init__.py`, validates each `plugin.yaml` manifest (schema +
  `core_compat` vs `CORE_VERSION`), derives the effective **trust tier from
  verified provenance** (`plugin.yaml.sig` ed25519 vs `keys/plugin_signing.pub`,
  or membership in `plugins/bundled_plugins.txt`), then imports and calls
  `load_plugin(app)`. Failures are logged, never fatal.
- **Plugin order** comes from `PLUGIN_ORDER` in `main.py` (passed to
  `load_all_plugins(plugin_order=...)`; unknown plugins load sorted after).
  Display data (name/icon/description/version/tier) comes from the manifests
  via the `PluginRegistry` — do not handroll PLUGIN_META anymore.
- **Plugin contract:** ship a validated `plugin.yaml` (see
  `libs/plugin_manifest.py` + `docs/PLUGIN_STORE.md`) and export
  `load_plugin(app)`; register `GET /api/plugins/<id>/info`,
  `GET|POST /api/plugins/<id>/config`, `POST /api/plugins/<id>/test`,
  `POST /api/plugins/<id>/sync` plus an HTML page at `/admin/<id>`. Plugin
  development lives in the PVE2Services-Plugin repo (see its
  `PLUGIN_DEVELOPMENT.md` and the `example_plugin/` template). Add a new
  bundled plugin to `bundled_plugins.txt` here; future/extra plugins are
  published to the store from the plugin repo (`tools/build_plugin.py`, or the
  `pve2-build-plugin` console script).
- **Plugin store** (MVP, trusted channel): `libs/plugin_store.py` runs at
  startup BEFORE discovery (no-op unless `PVE2_PLUGIN_STORE_URL` is set):
  fetch `index.json` from the S3/MinIO bucket, verify ed25519 signature +
  sha256 checksum, extract atomically into `plugins/<id>` with provenance.
  Trust: signed store package or bundled list → `trusted`; anything else →
  `third_party`. The manifest `trust_tier` field is display-only.
- **libs/**: `DBAdapter` (SQLAlchemy; schema auto-created on init;
  `get_db()` thread-safe singleton), `ConnectorConfig`, `SyncEngine`,
  `WikiAdapter`, `scheduler` (APScheduler), `ConfigAdapter`, `errors`
  (`register_error_handlers` + `PluginError`/`ConfigError`/`ManifestError`),
  `http`, `plugin_base`, `plugin_manifest`, `plugin_signing` (ed25519),
  `plugin_loader`, `plugin_store`.
- **Page store is in-memory:** `_pages` dict in
  `core/wiki_service/app/store.py`; data lost on restart.
- Admin UI pages are inline HTML strings (`hub.py`, `auth.py`,
  `templates_renderer.py` + `app/templates/`).

## Authentication

Middleware in `core/wiki_service/app/auth.py`: everything except `/health`,
`/admin/login`, `/admin/setup` requires the `pve2_session` cookie (`/api/*` →
401 JSON, other paths → redirect to login).

- Credentials are stored in DB settings `admin_user` / `admin_password_hash`
  (bcrypt). There is **no hardcoded default password** — first boot must call
  `POST /admin/setup` `{username, new_password}` (≥8 chars) or set credentials
  via `/admin/settings/credentials`. The README's `admin/pve2admin` only
  applies to test setups.
- Session secret auto-generated into `.pve2_secret` (override with
  `PVE2_SECRET_FILE`); deleting it invalidates all sessions.

## Database

Resolution order in `libs/db_adapter.resolve_db_url()`:
1. `PVE2_DB_URL`
2. `PVE2_DB_MODE=postgres|postgres_docker` → builds URL from
   `PVE2_DB_USER/PASS/HOST/NAME` (defaults `pve2/pve2pass/db/pve2`;
   host `db` matches the compose service)
3. `PVE2_DB_PATH`
4. Fallback: legacy `<repo>/PVE2Wiki/pve2wiki.db`, else `<repo>/pve2.db`

Schema SQL is portable SQLite + Postgres (see Database section above).

## Docker / release

- Working path: `docker compose up -d` builds from **repo root** context with
  `PVE2Services/core/wiki_service/Dockerfile`; the repo root is bind-mounted at
  `/app` (package at `/app/PVE2Services`), so code edits are live without
  rebuild. Run `tools/sync_plugins.py` first so bundled plugins are present.
- Release workflow (tag `v*`): checks out `Montenigri/PVE2Services-Plugin` at
  `vars.PVE2_PLUGINS_REF` (default `main`), copies the dirs listed in
  `PVE2Services/plugins/bundled_plugins.txt` into the image, then builds/pushes
  Docker. It also builds the `pve2` wheel and attaches it to the GitHub
  release (optionally publishes to PyPI when `PYPI_API_TOKEN` is set).
- `.dockerignore` excludes secrets (`.auth_credentials`, `.pve2_*`), venv,
  build artifacts and tests.
- The **private** signing key (`PVE2_PLUGIN_SIGNING_KEY`) and the store
  credentials live only in the PVE2Services-Plugin repo (which signs/publishes
  future store plugins). This repo commits only the public half
  `keys/plugin_signing.pub`.

## Key endpoints

| Path | Description |
|------|-------------|
| `/health` | `{"status": "ok"}` (unauthenticated) |
| `/admin/hub` | Plugin hub HTML |
| `/admin/<plugin>` | Per-plugin HTML page |
| `/admin/setup` POST | First-boot admin creation (unauthenticated) |
| `/pages` GET/POST, `/pages/{id}` GET | In-memory wiki pages CRUD |
| `/sync/run` | Create pages from DB `sync_state` |
| `/sync/run/full` | Full sync: fetch from Proxmox + wiki + DB |
| `/nodes`, `/vms`, `/storage`, `/cluster/status` | Proxmox proxy; creds read from DB settings |
| `/db/settings`, `/db/storage_history`, `/db/health_history`, `/db/sync_state`, `/db/changelog`, `/db/templates` | Read-only DB views |
| `/admin/scheduler/start|stop|status` | APScheduler lifecycle |

Proxmox calls pass through SSRF validation (`_is_safe_url` blocks metadata
hosts); security headers added by middleware in `main.py`.
