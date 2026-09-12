# Changelog

All notable changes to PVE2Services will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Repository split**: the core (`PVE2Services-Core`) and the first-party
  plugins (`PVE2Services-Plugin`) are now separate repos. The core release
  pipeline checks out the plugin repo at a pinned ref and bundles the open
  first-party plugins into the image; new/extra plugins (first-party closed or
  third-party) are distributed through the plugin store.
- The import package is nested at `PVE2Services/` inside the repo root; the
  repo root is on `PYTHONPATH` (Docker: `/app` + `/app/PVE2Services`).
- Build/sign tooling moved into the installable package
  (`PVE2Services/libs/plugin_build.py`, `plugin_keys.py`) and is exposed as the
  `pve2-build-plugin` / `pve2-generate-keys` console scripts; `tools/*` are
  thin shims. `tools/sync_plugins.py` fetches plugin sources for local dev.
- Plugin-owned admin templates now live with each plugin
  (`plugins/<id>/templates/`), so plugin packages are self-contained.

### Added
- **Plugin Store (MVP, trusted channel)**: per-plugin `plugin.yaml` manifests
  validated by the loader before import (schema + `core_compat` range +
  checksum); ed25519 package signing with verification against the committed
  `keys/plugin_signing.pub`; S3/MinIO distribution bucket with `index.json`
  catalog; automatic installer at startup (`PVE2_PLUGIN_STORE_URL` +
  `PVE2_PLUGINS_INSTALL`), fail-closed and atomic (staging/backup/swap).
  Trust tier is derived from verified provenance (signed package or bundled
  release list), never from the author-declared manifest field.
- Plugin hub now shows version, effective trust tier and declared
  capabilities per plugin (`/api/hub/plugins` enriched, hub UI badges).
- Release workflow now builds and signs all plugin packages and publishes
  them to the plugin store bucket alongside the Docker image.
- Local MinIO dev environment (`docker-compose.plugin-store-dev.yml`).
- Tooling: `tools/generate_keys.py`, `tools/build_plugin.py`
  (build/sign/publish), documented in `docs/PLUGIN_STORE.md`.
- Dynamic plugin discovery (no more hardcoded plugin list)

### Fixed
- **Postgres portability of connector configs** (September 2026 review):
  `ConnectorConfig.save_config` used a failed-INSERT→UPDATE fallback inside
  one transaction — on Postgres the failed INSERT aborts the transaction and
  the UPDATE is rejected; the run survived only on SQLite. Now uses a portable
  `INSERT ... ON CONFLICT ... DO UPDATE` upsert, passes `enabled` as boolean
  (the column is BOOLEAN on Postgres), and the table `id` becomes an identity
  column on Postgres (SQLite keeps the rowid-alias `INTEGER PRIMARY KEY`).
  Note for existing Postgres deployments: re-run the app once against a
  migrated DB (or drop/recreate the connector tables) — `CREATE TABLE IF NOT
  EXISTS` only fixes the schema on fresh databases.
- Security hardening of the plugin pipeline (September 2026 review):
  zip directory entries (`../evil/`) could escape the extraction folder —
  all members (dirs included) are now validated; manifest signature parsing
  tolerates trailing whitespace; index `version` values are parsed as
  semver before any path/key construction (poisoned-catalog defense).
- XSS hardening: hub API output (names/descriptions/icons/load errors in
  manifests and plugin load errors) is HTML-escaped server-side before the
  client renders it into innerHTML.
- Store installer now downloads `index.json` once per sync run instead of
  once per plugin; install results are structured (installed/updated/skipped).
- Install request syntax now accepts manifests id with dashes
  (e.g. `pve2-drift==1.2.0`).

### Changed
- `PVE2_PLUGINS_DIR` (env): the core can load plugins from a separate
  checkout (core repo + plugins repo split). External-checkout plugins are
  imported by file path under a private namespace (`pve2_external_<dir>`);
  the monolithic default behaviour is unchanged. Trust tiers are still
  derived from verified provenance.
- DB path unification across all components
- Test infrastructure with shared fixtures
- CONTRIBUTING.md, CHANGELOG.md, SECURITY.md documentation
- MIT LICENSE file

### Fixed
- PVE2Wiki plugin directory split (was broken on Linux)
- DBAdapter singleton pattern (reduced resource usage)
- AdGuard authentication caching (no more re-login per API call)
- PVE2Power timezone precision (now supports half-hour offsets)
- PVE2Power `last_action` field now populated correctly
- PVE2Power `poll_interval_seconds` now actually used by scheduler
- PVE2Core clone action now uses proper VMID allocation
- WikiAdapter exceptions (no more HTTPException outside request context)
- PVE2DNS typo "enabled/enabled" fixed
- PVE2Proxy `/generate` now uses POST instead of GET
- PVE2Proxy bulk save now uses database transactions
- PVE2Proxy SSH keys now encrypted at rest
- PVE2Audit `entity_history` now respects `days` parameter
- PVE2Wiki content hash now includes all relevant fields
- PVE2Wiki now archives removed entities instead of orphaning

### Changed
- Plugin discovery is now automatic (scan `plugins/` directory)
- Hub plugin list is now dynamic (no more hardcoded JSON)
- README placeholder URLs updated
- pyproject.toml metadata completed

## [0.1.0] - 2026-01-01

### Added
- Initial release
- Core plugin system with 8 plugins
- PVE2Core: Central Proxmox data connector
- PVE2Dash: Read-only cluster dashboard
- PVE2Audit: Rightsizing recommendations
- PVE2DNS: DNS sync (Pi-hole/AdGuard)
- PVE2Notify: State-change notifications
- PVE2Proxy: Reverse proxy generator
- PVE2Power: Auto start/stop scheduler
- PVE2Wiki: Wiki.js sync via GraphQL
- Authentication system (HMAC-SHA256)
- CSRF protection
- Rate limiting
- Security headers
- Docker support (SQLite and PostgreSQL)
- CI/CD with GitHub Actions
- Comprehensive technical documentation

[Unreleased]: https://github.com/Montenigri/PVE2Services/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Montenigri/PVE2Services/releases/tag/v0.1.0
