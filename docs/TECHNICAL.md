# PVE2 Services — Technical Documentation

> **Version:** 0.1.1 · **Last updated:** September 2026

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Directory Layout](#2-directory-layout)
3. [Plugin System](#3-plugin-system)
4. [Database Schema](#4-database-schema)
5. [Authentication System](#5-authentication-system)
6. [Scheduler System](#6-scheduler-system)
7. [API Reference](#7-api-reference)
8. [Configuration Reference](#8-configuration-reference)
9. [Security Model](#9-security-model)
10. [Development Guide](#10-development-guide)
11. [Deployment Guide](#11-deployment-guide)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     FastAPI Server                        │
│                     (Port 8000)                           │
│                                                          │
│  ┌───────────┐  ┌───────────┐  ┌──────────────────────┐  │
│  │  Auth MW   │  │  Session   │  │  Admin Routes        │  │
│  │  (cookie)  │  │  Manager   │  │  /admin/hub, /login   │  │
│  └─────┬─────┘  └───────────┘  │  /admin/settings       │  │
│        │                       └──────────────────────┘  │
│        ▼                                                 │
│  ┌──────────────────────────────────────────────────────┐ │
│  │              Plugin Discovery Layer                   │ │
│  │  store sync (S3/MinIO, optional) → manifest           │ │
│  │  validation → trust-tier derivation → plugin import   │ │
│  └──┬───────┬───────┬───────┬───────┬───────┬──────────┘ │
│     │       │       │       │       │       │            │
│     ▼       ▼       ▼       ▼       ▼       ▼            │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌───────┐     │
│  │DNS  │ │Notify│ │Power│ │Proxy│ │Wiki │ │PVE2   │     │
│  │     │ │     │ │     │ │     │ │     │ │Core   │     │
│  └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬────┘     │
│     │       │       │       │       │       │            │
│     └───────┴───────┴───────┴───────┴───────┘            │
│                        │  Cross-plugin HTTP calls         │
│                        ▼  (never direct imports)          │
│                  ┌──────────────┐                         │
│                  │  PVE2Core    │  ←─ /api/plugins/...    │
│                  │  (data hub)  │                         │
│                  └──────┬───────┘                         │
│                         │                                 │
│                         ▼                                 │
│                  ┌──────────────┐                         │
│                  │  Proxmox VE  │                         │
│                  │  Cluster API │                         │
│                  └──────────────┘                         │
│                                                          │
│  ┌──────────────────────────────────────────────────────┐ │
│  │            Shared Libraries (libs/)                   │ │
│  │  DBAdapter │ SyncEngine │ Scheduler │ WikiAdapter     │ │
│  │  ConfigAdapter │ ConnectorConfig                      │ │
│  │  plugin_manifest │ plugin_signing │                   │ │
│  │  plugin_loader │ plugin_store                         │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Cross-plugin HTTP, not imports** | Prevents circular dependencies; plugins communicate via PVE2Core's HTTP API |
| **Inline JS/HTML, no frontend framework** | Zero build step; single Python package dependency-free for frontend |
| **stdlib auth (HMAC-SHA256)** | Avoids JWT/OAuth2 library dependencies; adequate for internal admin panel |
| **SQLAlchemy auto-schema** | `DBAdapter.__init__` creates tables on instantiation; no migration tool needed |
| **Sandboxed Jinja2** | User-uploaded templates in PVE2Wiki cannot execute arbitrary Python |
| **Manifest before import** | `plugin.yaml` validated (schema + core compat) before any plugin code runs |
| **Tier from provenance, not self-declaration** | Trust level derives from the verified ed25519 signature / bundled list, never from the manifest field an author could fake |

---

## 2. Directory Layout

The import package is nested at `PVE2Services/`; the repo also ships the
installable `pve2` wheel. First-party plugin sources live in the separate
[PVE2Services-Plugin](https://github.com/Montenigri/PVE2Services-Plugin) repo
and are copied in at build time (or via `tools/sync_plugins.py`).

```
PVE2Services-Core/
├── PVE2Services/                # import package (repo root is on PYTHONPATH)
│   ├── core/wiki_service/
│   │   └── app/
│   │       ├── main.py              # FastAPI application entry point
│   │       ├── auth.py              # Authentication system (token + middleware)
│   │       ├── hub.py               # Hub admin page (plugin listing)
│   │       ├── templates_renderer.py # Centralized HTML template loader
│   │       ├── templates/           # only CORE templates (generic pages)
│   │       │   ├── login.html
│   │       │   ├── settings.html
│   │       │   ├── hub.html
│   │       │   └── plugin_page.html
│   │       └── store.py             # In-memory wiki pages
│   ├── libs/
│   │   ├── db_adapter.py            # SQLAlchemy models + CRUD operations
│   │   ├── config_adapter.py        # Plugin configuration storage
│   │   ├── connector_config.py      # ConnectorConfig Pydantic model
│   │   ├── scheduler.py             # APScheduler wrapper (multi-job)
│   │   ├── sync_engine.py           # Wiki sync engine (Proxmox → Wiki.js)
│   │   ├── wiki_adapter.py          # Wiki.js GraphQL client
│   │   ├── plugin_manifest.py       # plugin.yaml schema + validation
│   │   ├── plugin_signing.py        # ed25519 sign/verify (package provenance)
│   │   ├── plugin_loader.py         # manifest-validating discovery + registry
│   │   ├── plugin_store.py          # S3/MinIO installer (startup sync)
│   │   ├── plugin_build.py          # packaging + signing + publish (pve2-build-plugin)
│   │   └── plugin_keys.py           # keypair generator (pve2-generate-keys)
│   └── plugins/
│       └── bundled_plugins.txt      # first-party plugin list → trusted tier
│                                    # (dirs fetched from the plugin repo)
│
├── tests/                       # core test-suite
├── tools/                       # build/sign shims, sync_plugins, password recovery
├── keys/plugin_signing.pub      # Trusted ed25519 public key (committed)
├── docker-compose.yml
├── pyproject.toml               # Ruff config, wheel/entry points, metadata
├── requirements.txt
├── AGENTS.md
└── README.md
```

---

## 3. Plugin System

Every plugin lives in `plugins/<Dir>/` (fetched from the PVE2Services-Plugin
repo, or installed by the store) and consists of a Python package plus a
**`plugin.yaml` manifest**. Plugin-owned admin templates travel inside the
plugin dir (`templates/`), so store packages are self-contained:

```
plugins/
  my_plugin/
    __init__.py      # exports load_plugin()
    plugin.yaml      # manifest: id, name, version, core_compat, capabilities
    templates/       # optional plugin-owned admin HTML
    ...
```

### Manifest

```yaml
id: my_plugin
name: My Custom Plugin
version: 1.0.0                      # strict X.Y.Z semver
core_compat: ">=0.1.0,<2.0.0"       # PEP 440 range checked against the core
entrypoint: "__init__:load_plugin"  # "pkg.mod:function" also supported
capabilities:
  reads: [vm_state, machine_cache]
  writes: [notifications]
trust_tier: trusted                 # display-only; see trust model below
```

The loader (`libs/plugin_loader.py`) validates the manifest **before
importing** the plugin: schema errors or an incompatible `core_compat` skip
the plugin with a logged reason instead of crashing the core at import time.

### Discovery and trust tier

At import time the core (optionally the plugin-store installer first, see
below) runs `load_all_plugins()`:

1. scan `plugins/*` for `__init__.py` directories
2. validate `plugin.yaml` (schema + `core_compat` vs `CORE_VERSION`)
3. derive the effective **trust tier from verified provenance** — never from
   the manifest's `trust_tier` field (display-only):
   - `plugin.yaml.sig` verifying against `keys/plugin_signing.pub` → `trusted`
   - dir listed in `plugins/bundled_plugins.txt` (ships in the same release
     artifact as the core) → `trusted`
   - anything else → `third_party`
4. import `PVE2Services.plugins.<Dir>` and call `load_plugin(app)`

Failures are logged, never fatal. Discovered plugins are recorded in a
`PluginRegistry` (`/api/hub/plugins` serves it: id, name, version, effective
tier, capabilities, loaded flag, load error).

### Plugin store (optional distribution channel)

The core can pull signed plugin packages from an S3/MinIO bucket at startup —
signature + sha256 verified fail-closed, atomic install into `plugins/`:

```bash
PVE2_PLUGIN_STORE_URL=http://minio:9000
PVE2_PLUGINS_INSTALL="pve2dns,pve2power==1.0.0"
```

Full details (signing, key management, release pipeline, edge cases):
[docs/PLUGIN_STORE.md](PLUGIN_STORE.md). Plugin development guide with the
complete manifest schema: [PLUGIN_DEVELOPMENT.md](../PLUGIN_DEVELOPMENT.md).

### Required Endpoints

Every plugin should define these 4 endpoints for hub compatibility:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/plugins/<id>/info` | Returns `{"id": ..., "name": ..., "description": ..., "icon": ..., "version": ...}` |
| `GET` | `/api/plugins/<id>/config` | Returns current plugin configuration |
| `POST` | `/api/plugins/<id>/config` | Saves plugin configuration |
| `POST` | `/api/plugins/<id>/test` | Tests connection to external service |
| `POST` | `/api/plugins/<id>/sync` | Runs a sync cycle |

### Plugin Admin Pages

Each plugin's `frontend.py` returns an HTML admin page read from its own
`plugins/<Dir>/templates/` directory (plugin-owned assets). The hub renders it
via the core's `plugin_page.html` shell and initializes the plugin-specific
JavaScript.

Plugin frontend HTML should include:

1. A `<div id="plugin-content">` container
2. A `<script>` block with a `loadPlugin()` function
3. A `PluginConfig` JS class following the pattern in `plugin_page.html`

### Plugin-to-Plugin Communication

Plugins fetch machine/VM/container data from **PVE2Core** via HTTP:

```
GET /api/plugins/pve2core/vms      # All VMs
GET /api/plugins/pve2core/nodes    # All nodes
GET /api/plugins/pve2core/storage  # Storage volumes
```

**Never import another plugin's module directly.** This prevents circular imports and maintains loose coupling.

---

## 4. Database Schema

Auto-created by `DBAdapter.__init__()`. Supports SQLite (default) and PostgreSQL.

### Table: `settings`

| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT PRIMARY KEY | Setting key (`admin_username`, `admin_password_hash`) |
| `value` | TEXT | Setting value |

### Table: `connector_configs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL UNIQUE | Plugin identifier |
| `config_json` | TEXT | JSON blob with plugin-specific config |
| `enabled` | BOOLEAN DEFAULT 1 | Whether the connector is active |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

### Table: `proxy_mappings`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier (e.g. `pve2proxy`) |
| `machine_id` | TEXT NOT NULL | VM/CT identifier |
| `machine_name` | TEXT | Human-readable name |
| `domain_name` | TEXT | Domain for reverse proxy |
| `port` | INTEGER | Target port (default: 80 for VMs, 8006 for nodes) |
| `default_port` | INTEGER | Original default port before override |
| `ip_address` | TEXT | Machine IP |
| `provider` | TEXT | `"nginx"` or `"traefik"` |
| `proxy_id` | TEXT | Provider-specific proxy ID |
| `ssl` | BOOLEAN DEFAULT 0 | Enable SSL |
| `websocket` | BOOLEAN DEFAULT 0 | Enable WebSocket support |
| `cache` | BOOLEAN DEFAULT 0 | Enable caching |
| `rate_limit` | BOOLEAN DEFAULT 0 | Enable rate limiting |
| `extra_directives` | TEXT | Additional Nginx directives (validated) |
| `deployed` | BOOLEAN DEFAULT 0 | Whether config has been deployed |
| `deployed_at` | TIMESTAMP | Last deployment timestamp |
| `deployed_version` | INTEGER | Incremented on each deploy |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

### Table: `machines` (PVE2DNS)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `machine_id` | TEXT NOT NULL | VM/CT identifier |
| `machine_name` | TEXT | Human-readable name |
| `domain` | TEXT | Domain suffix |
| `hostname` | TEXT | Resolved hostname |
| `ip_address` | TEXT | Machine IP |
| `type` | TEXT | `"qemu"` or `"lxc"` |
| `dns_record_id` | TEXT | Remote DNS record ID |
| `synced` | BOOLEAN DEFAULT 0 | Sync status flag |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

### Table: `sync_state` (PVE2DNS)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `last_sync` | TIMESTAMP | Last sync timestamp |
| `status` | TEXT | `"idle"`, `"syncing"`, `"error"` |

### Table: `machine_snapshots` (PVE2Notify)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `snapshot_data` | TEXT | JSON blob of machine state at snapshot time |
| `created_at` | TIMESTAMP DEFAULT NOW | |

### Table: `event_log` (PVE2Notify)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `machine_id` | TEXT | Affected machine |
| `event_type` | TEXT | `"created"`, `"deleted"`, `"changed"` |
| `details` | TEXT | Event description |
| `created_at` | TIMESTAMP DEFAULT NOW | |

### Table: `snapshot_state` (PVE2Notify)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `last_snapshot` | TIMESTAMP | Last snapshot time |
| `status` | TEXT | `"idle"`, `"snapshotting"`, `"error"` |

### Table: `power_schedules` (PVE2Power)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `machine_id` | TEXT NOT NULL | VM/CT identifier |
| `machine_name` | TEXT | Human-readable name |
| `weekday_start` | TEXT | HH:MM format |
| `weekday_stop` | TEXT | HH:MM format |
| `weekend_start` | TEXT | HH:MM format |
| `weekend_stop` | TEXT | HH:MM format |
| `monday` | BOOLEAN DEFAULT 1 | |
| `tuesday` | BOOLEAN DEFAULT 1 | |
| `wednesday` | BOOLEAN DEFAULT 1 | |
| `thursday` | BOOLEAN DEFAULT 1 | |
| `friday` | BOOLEAN DEFAULT 1 | |
| `saturday` | BOOLEAN DEFAULT 1 | |
| `sunday` | BOOLEAN DEFAULT 1 | |
| `timezone` | TEXT | IANA timezone (e.g., `"Europe/Rome"`) |
| `action` | TEXT | `"shutdown"` or `"start"` (deprecated; combined schedule) |
| `enabled` | BOOLEAN DEFAULT 1 | |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

### Table: `wiki_pages` (PVE2Wiki)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `page_id` | TEXT | Wiki.js page ID |
| `entity_type` | TEXT | `"vm"`, `"lxc"`, `"node"`, `"storage"` |
| `entity_id` | TEXT | Source entity identifier |
| `title` | TEXT | Page title |
| `path` | TEXT | Wiki.js path |
| `content_hash` | TEXT | MD5 hash of last synced content |
| `published` | BOOLEAN DEFAULT 0 | Whether page exists on wiki |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

### Table: `wiki_templates` (PVE2Wiki)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `service_name` | TEXT NOT NULL | Plugin identifier |
| `entity_type` | TEXT | `"vm"`, `"lxc"`, `"node"`, `"storage"` |
| `template_name` | TEXT | Human-readable name |
| `template_content` | TEXT | Jinja2 template string |
| `is_default` | BOOLEAN DEFAULT 0 | Whether this is the default template |
| `created_at` | TIMESTAMP DEFAULT NOW | |
| `updated_at` | TIMESTAMP DEFAULT NOW | |

---

## 5. Authentication System

**File:** `core/wiki_service/app/auth.py`

### How It Works

```
User → POST /admin/login ─→ Validate credentials ─→ Generate HMAC token
                                                         │
                                                         ▼
                                              Set cookie: pve2_session=<token>
                                              httponly, path=/, max_age=86400
                                                         │
                                                         ▼
User → GET /admin/* ────────────→ AuthMiddleware checks cookie
                                          │
                                    ┌─────┴─────┐
                                    │ Valid?      │
                                    ├─────────────┤
                                    │ Yes → route │
                                    │ No  → 302 /admin/login (HTML) or 401 (API)
                                    └─────────────┘
```

### Token Format

```
header = base64({"alg":"HS256","typ":"JWT"})
payload = base64({"user":"admin","exp":<unix_ts>,"iat":<unix_ts>,"jti":<uuid>})
signature = HMAC-SHA256(header + "." + payload, secret_key)
token = header + "." + payload + "." + signature
```

- **Secret key:** Read from `settings` table → `auth_secret`; generated on first login if missing
- **Expiry:** 24 hours from issuance

### Endpoints

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| `GET` | `/admin/login` | No | Login page (HTML form) |
| `POST` | `/admin/login` | No | Login endpoint (accepts form or JSON) |
| `POST` | `/admin/logout` | Yes | Clears session cookie |
| `GET` | `/admin/settings` | Yes | Password change page |
| `POST` | `/admin/settings` | Yes | Update username/password |

### Env Overrides

Admin credentials are **not** configurable via env vars. On first boot, create
the admin account via `POST /admin/setup {username, new_password}` (or the web
UI); credentials are stored bcrypt-hashed in the DB `settings` table.

```bash
PVE2_ENCRYPTION_KEY=<fernet-key>   # Optional: encrypt connector secrets
                                   # (auto-generated into .pve2_encryption_key if unset)
PVE2_STRICT_SECRETS=true           # Refuse to store secrets without an explicit key
```

### Plugin Store Configuration

Store installer env (all optional — store disabled when
`PVE2_PLUGIN_STORE_URL` is unset, see `docs/PLUGIN_STORE.md`):

```bash
PVE2_PLUGIN_STORE_URL=http://minio:9000        # S3 endpoint (MinIO ok)
PVE2_PLUGIN_STORE_BUCKET=pve2services-plugins  # default
PVE2_PLUGIN_STORE_ACCESS_KEY / _SECRET_KEY     # S3 credentials
PVE2_PLUGIN_STORE_VERIFY_TLS=true              # false only for self-signed MinIO
PVE2_PLUGINS_INSTALL="pve2dns,pve2power==1.0.0" # id (latest) or id==version
PVE2_PLUGINS_FORCE=false                       # reinstall same version
PVE2_PLUGIN_SIGNING_PUBKEY=...                 # override trusted key path
```

### Middleware Protection

- **Protected:** All routes under `/admin/*` and `/api/*`
- **Exempt:** `/health`, `/admin/login`
- API routes return `401 {"detail": "Not authenticated"}`
- Admin routes redirect to `/admin/login?next=<original_path>`

---

## 6. Scheduler System

**File:** `libs/scheduler.py`

### Architecture

```
                 ┌──────────────────────────────────────┐
                 │         BackgroundScheduler           │
                 │         (APScheduler singleton)       │
                 └──────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   threading.Lock   │
                    └─────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
         "pve2power"     "pve2notify"    "pve2_sync"
         (300s)           (60s)           (300s)
              │               │               │
              ▼               ▼               ▼
     check_and_act()   poll_and_notify()  sync_engine()
```

### API

```python
from libs.scheduler import add_job, remove_job, scheduler_status

# Add a named recurring job
add_job("my_plugin", func=my_function, seconds=300)
# Returns True on success, False if job name already exists

# Remove a named job
remove_job("my_plugin")

# Get status of all jobs
scheduler_status()
# Returns: [{"id": "my_plugin", "next_run_time": "2026-06-05T12:00:00"}, ...]

# Legacy aliases (backward-compatible):
# start_scheduler(), stop_scheduler() → operate on "pve2_sync" job
```

### Plugin Scheduler Endpoints

Each plugin with background scheduling exposes 3 endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/<id>/scheduler/status` | Returns job status or `{"active": false}` |
| `POST` | `/api/plugins/<id>/scheduler/start` | Start background job |
| `POST` | `/api/plugins/<id>/scheduler/stop` | Stop background job |

### Background Job Pattern

```python
def _run_background_job():
    """Sync wrapper for APScheduler (sync callback)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(async_function())
    finally:
        loop.close()

# Each job checks config.enabled early and exits if disabled
```

### Important Notes

- APScheduler calls sync functions; async plugins use `asyncio.new_event_loop()` wrappers
- Jobs are **not auto-started** on app boot — always start manually via API
- The threading lock prevents race conditions when adding/removing jobs concurrently

---

## 7. API Reference

### 7.1 Global Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Root welcome message |
| `GET` | `/health` | No | `{"status": "ok"}` |
| `GET` | `/admin/hub` | Yes | Plugin hub HTML page |
| `GET` | `/api/hub/plugins` | Yes | Plugin registry: id, name, version, effective trust tier, capabilities, loaded flag |
| `GET` | `/admin/scheduler/status` | Yes | Legacy scheduler status |
| `POST` | `/admin/scheduler/start` | Yes | Start legacy sync |
| `POST` | `/admin/scheduler/stop` | Yes | Stop legacy sync |

### 7.2 PVE2Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2core/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2core/config` | Get config |
| `POST` | `/api/plugins/pve2core/config` | Save config |
| `POST` | `/api/plugins/pve2core/test` | Test Proxmox connection |
| `GET` | `/api/plugins/pve2core/vms` | Get all VMs (normalized) |
| `GET` | `/api/plugins/pve2core/nodes` | Get all nodes |
| `GET` | `/api/plugins/pve2core/storage` | Get storage volumes |
| `GET` | `/api/plugins/pve2core/vms/{vmid}` | Get single VM by ID |
| `POST` | `/api/plugins/pve2core/vms/{vmid}/status` | Change VM status (`start`/`shutdown`/`stop`) |

**Config schema:**
```json
{
  "host": "https://proxmox-server:8006",
  "token_name": "pve2-token",
  "token_secret": "your-api-token",
  "verify_ssl": true
}
```

### 7.3 PVE2DNS Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2dns/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2dns/config` | Get config |
| `POST` | `/api/plugins/pve2dns/config` | Save config |
| `POST` | `/api/plugins/pve2dns/test` | Test DNS provider connection |
| `POST` | `/api/plugins/pve2dns/sync` | Run DNS sync |
| `GET` | `/api/plugins/pve2dns/machines` | List DNS machine records |
| `POST` | `/api/plugins/pve2dns/machines` | Save all machine records |
| `PUT` | `/api/plugins/pve2dns/machines` | Update single machine record |
| `GET` | `/api/plugins/pve2dns/sync-state` | Get last sync state |

**Config schema:**
```json
{
  "provider": "pihole" | "adguard",
  "pihole_url": "http://pi.hole:80",
  "pihole_token": "abc123...",
  "adguard_url": "http://adguard:80",
  "adguard_user": "admin",
  "adguard_password": "pass",
  "default_domain": "home.lab",
  "sync_interval": 300
}
```

### 7.4 PVE2Notify Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2notify/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2notify/config` | Get config |
| `POST` | `/api/plugins/pve2notify/config` | Save config |
| `POST` | `/api/plugins/pve2notify/test` | Test notification provider |
| `POST` | `/api/plugins/pve2notify/sync` | Run manual snapshot & notify |
| `GET` | `/api/plugins/pve2notify/events` | Get event log |
| `GET` | `/api/plugins/pve2notify/snapshot` | Get current snapshot data |
| `GET` | `/api/plugins/pve2notify/scheduler/status` | BG job status |
| `POST` | `/api/plugins/pve2notify/scheduler/start` | Start BG poller |
| `POST` | `/api/plugins/pve2notify/scheduler/stop` | Stop BG poller |

**Config schema:**
```json
{
  "provider": "discord" | "slack" | "mattermost" | "telegram",
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
  "slack_webhook_url": "https://hooks.slack.com/...",
  "mattermost_webhook_url": "https://mattermost.example.com/hooks/...",
  "telegram_bot_token": "123456:ABC-DEF...",
  "telegram_chat_id": "-1001234567890",
  "poll_interval": 60
}
```

### 7.5 PVE2Power Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2power/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2power/config` | Get config |
| `POST` | `/api/plugins/pve2power/config` | Save config |
| `POST` | `/api/plugins/pve2power/test` | Test Proxmox connection |
| `GET` | `/api/plugins/pve2power/schedules` | Get all schedules |
| `POST` | `/api/plugins/pve2power/schedules` | Save all schedules |
| `GET` | `/api/plugins/pve2power/check` | Check current state vs schedules |
| `POST` | `/api/plugins/pve2power/act` | Act on pending schedule changes |
| `GET` | `/api/plugins/pve2power/scheduler/status` | BG job status |
| `POST` | `/api/plugins/pve2power/scheduler/start` | Start BG checker |
| `POST` | `/api/plugins/pve2power/scheduler/stop` | Stop BG checker |

**Config schema:**
```json
{
  "timezone": "Europe/Rome",
  "check_interval": 300
}
```

**Schedule schema (per machine):**
```json
{
  "machine_id": "100",
  "machine_name": "web-server",
  "weekday_start": "08:00",
  "weekday_stop": "20:00",
  "weekend_start": "10:00",
  "weekend_stop": "18:00",
  "monday": true,
  "tuesday": true,
  "wednesday": true,
  "thursday": true,
  "friday": true,
  "saturday": false,
  "sunday": false,
  "timezone": "Europe/Rome",
  "enabled": true
}
```

**Overnight logic:** A machine is considered "should be on" when `now >= start OR now < stop`. This correctly handles schedules like `start=22:00, stop=06:00`.

**Action sequence:** When a machine should be off:
1. Send graceful shutdown via PVE2Core API (`POST /vms/{vmid}/status` with `shutdown`)
2. If still running after configured timeout, send `stop` (forced)

### 7.6 PVE2Proxy Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2proxy/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2proxy/config` | Get config |
| `POST` | `/api/plugins/pve2proxy/config` | Save config |
| `POST` | `/api/plugins/pve2proxy/test` | Test SSH connection |
| `GET` | `/api/plugins/pve2proxy/mappings` | Get all proxy mappings |
| `POST` | `/api/plugins/pve2proxy/mappings` | Save all mappings |
| `PUT` | `/api/plugins/pve2proxy/mappings` | Update single mapping |
| `DELETE` | `/api/plugins/pve2proxy/mappings/{id}` | Delete a mapping |
| `POST` | `/api/plugins/pve2proxy/generate` | Generate config without deploying |
| `POST` | `/api/plugins/pve2proxy/preview` | Preview generated config |
| `POST` | `/api/plugins/pve2proxy/deploy` | Deploy generated config to server |
| `POST` | `/api/plugins/pve2proxy/sync` | Full sync (generate + deploy) |

**Config schema:**
```json
{
  "provider": "nginx" | "traefik",
  "ssh_host": "proxy.example.com",
  "ssh_port": 22,
  "ssh_user": "root",
  "ssh_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
  "nginx_config_path": "/etc/nginx/sites-enabled/",
  "nginx_reload_cmd": "systemctl reload nginx",
  "traefik_config_path": "/etc/traefik/dynamic/",
  "default_domain": "example.com",
  "default_port": 80
}
```

**Mapping schema (per machine):**
```json
{
  "machine_id": "100",
  "machine_name": "web-server",
  "domain_name": "web.example.com",
  "port": 8080,
  "ip_address": "192.168.1.50",
  "provider": "nginx",
  "ssl": true,
  "websocket": true,
  "cache": false,
  "rate_limit": true,
  "extra_directives": "proxy_read_timeout 90s;",
  "deployed": false
}
```

**Security validations for Proxy config:**
- `extra_directives`: blocked keywords include `server{`, `location{`, `return`, `rewrite`, `deny`, `allow`, `access_log`, `error_log`, `root`, `index`, `proxy_pass`, `fastcgi_pass`, `uwsgi_pass`, `if (`, `set $`, `auth_basic`, `valid_referers`, `add_header` on the first 50 chars
- `nginx_reload_cmd`: must be one of `systemctl reload nginx`, `service nginx reload`, `nginx -s reload`, `/etc/init.d/nginx reload`, `systemctl reload nginx;`, `service nginx reload;`, `nginx -s reload;`
- SSH remote path: escaped with `shlex.quote()`

### 7.7 PVE2Wiki Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2wiki/info` | Plugin metadata |
| `GET` | `/api/plugins/pve2wiki/config` | Get config |
| `POST` | `/api/plugins/pve2wiki/config` | Save config |
| `POST` | `/api/plugins/pve2wiki/test` | Test Wiki.js connection |
| `POST` | `/api/plugins/pve2wiki/sync` | Run wiki sync |
| `GET` | `/api/plugins/pve2wiki/entities` | List entities (VMs/CTs/Nodes/Storage) |
| `GET` | `/api/plugins/pve2wiki/templates` | Get templates for an entity type |
| `POST` | `/api/plugins/pve2wiki/templates` | Save a template |
| `DELETE` | `/api/plugins/pve2wiki/templates/{id}` | Delete a template |
| `GET` | `/api/plugins/pve2wiki/pages` | Get published wiki pages |
| `DELETE` | `/api/plugins/pve2wiki/pages/{id}` | Unpublish a wiki page |

**Config schema:**
```json
{
  "wiki_url": "https://wiki.example.com",
  "wiki_token": "graphql-api-token",
  "parent_page_id": "root-page-id",
  "page_prefix": "PVE2",
  "default_template": null
}
```

### 7.8 Admin Data Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/pve2core/vms` | Raw VM list from Proxmox |
| `GET` | `/api/plugins/pve2core/nodes` | Raw node list from Proxmox |
| `GET` | `/api/plugins/pve2core/storage` | Raw storage list from Proxmox |
| `GET` | `/api/plugins/pve2core/cluster/status` | Cluster status from Proxmox |

These proxy directly to Proxmox. Authentication query params (`base_url`, `token`, `secret`) were removed in the security audit (HIGH-01).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/db/settings` | Read settings from DB |
| `POST` | `/db/settings` | Update settings in DB |
| `GET` | `/db/connector_configs` | List connector configs |
| `GET` | `/db/storage_history` | Read storage history from DB |
| `GET` | `/db/dns_records` | Read DNS records from DB |
| `GET` | `/db/vm_history` | Read VM history from DB |

---

## 8. Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PVE2_DB_MODE` | `sqlite` | DB backend: `sqlite`, `postgres`, `postgres_docker` |
| `PVE2_DB_URL` | *(auto)* | Full database URL override |
| `PVE2_SESSION_SECURE` | `true` | Secure cookie flag (disable for plain-http local testing) |
| `PVE2_TRUST_XFF` | `false` | Trust X-Forwarded-For for rate limiting behind a proxy |
| `PVE2_SECRET_FILE` | `.pve2_secret` | Session signing secret file |
| `PVE2_ENCRYPTION_KEY` | *(auto)* | Fernet key for connector secrets |
| `PVE2_STRICT_SECRETS` | `false` | Fail instead of falling back to plaintext secrets |
| `PVE2_PLUGIN_STORE_URL` | *(unset)* | S3/MinIO endpoint; unset = plugin store disabled |
| `PVE2_PLUGIN_STORE_BUCKET` | `pve2services-plugins` | Plugin store bucket |
| `PVE2_PLUGIN_STORE_ACCESS_KEY` / `_SECRET_KEY` | *(unset)* | S3 credentials for the store |
| `PVE2_PLUGIN_STORE_VERIFY_TLS` | `true` | TLS verification for the store endpoint |
| `PVE2_PLUGINS_INSTALL` | *(unset)* | Plugins to install at boot: `id` (latest) or `id==version`, comma separated |
| `PVE2_PLUGINS_FORCE` | `false` | Reinstall even at same version |
| `PVE2_PLUGINS_DIR` | *(repo-relative)* | Load plugins from this directory instead of the checkout's `plugins/` (see `ready_to_publish.md`, core/plugin repo split) |
| `PVE2_PLUGIN_SIGNING_PUBKEY` | `keys/plugin_signing.pub` | Trusted ed25519 public key for package verification |

### DB URL Resolution

```python
if PVE2_DB_URL:
    url = PVE2_DB_URL
elif mode == "sqlite":
    url = "sqlite:///pve2wiki.db"  # then tries pve2.db
elif mode == "postgres":
    url = "postgresql://user:pass@localhost:5432/pve2wiki"
elif mode == "postgres_docker":
    url = "postgresql://pve2:pve2@postgres:5432/pve2wiki"
```

### Proxmox API Token

Each PVE2Core plugin config must specify a Proxmox API token:

1. Create a token in Proxmox: `Datacenter → Permissions → API Tokens`
2. Assign privileges: `VM.Audit`, `VM.PowerMgmt`, `Datastore.Audit`, `Sys.Audit`
3. Configure in PVE2Core UI under plugin config

---

## 9. Security Model

### Threat Surface

| Vector | Mitigation |
|--------|------------|
| **SSTI** (CRIT-02) | `SandboxedEnvironment` in Jinja2 template rendering |
| **SSH injection** (CRIT-03) | `shlex.quote()` on remote paths |
| **SQL injection** (CRIT-04) | Parameterized queries (removed f-string in `get_recommendations`) |
| **SSRF** (HIGH-01) | Removed query param `base_url`/`token`/`secret` from proxy endpoints |
| **Race condition** (HIGH-02) | `threading.Lock` on scheduler `add_job`/`remove_job` |
| **Nginx injection** (HIGH-03) | Pydantic validator blocks dangerous directives |
| **`__import__`** (HIGH-06) | Replaced with `importlib.import_module` |
| **TLS verify=False** (CRIT-06) | Removed from all external HTTP calls |
| **SSH host key** (CRIT-07) | `StrictHostKeyChecking=accept-new` instead of `no` |
| **Credentials at rest** (CRIT-05) | Not encrypted (out of scope — DB is app-accessible only) |
| **Weak password hash** | bcrypt (legacy SHA-256 hashes migrate on login); not exposed to Internet |
| **Session hijack** | HTTP-only cookie, 24h expiry, HMAC-signed |
| **Malicious plugin load** | `plugin.yaml` validated before import; trust tier derived from verified provenance (ed25519 signature vs `keys/plugin_signing.pub`, or bundled release list); store packages integrity-checked (checksum + safe extraction caps) before touching disk

### Audit Trail

The pre-release security audit identified 14 findings; the significant ones
(SSTI, SSH/SQL/Nginx injection, SSRF, race conditions, TLS, host key
verification) are fixed and mirrored in the table above. Drop privileges and
credential-at-rest encryption were evaluated as out of scope for a
LAN-facing, self-hosted panel. Report vulnerabilities per
[`SECURITY.md`](../SECURITY.md), not in the issue tracker.

---

## 10. Development Guide

### Prerequisites

- Python 3.12+
- A Proxmox VE cluster (for full testing)
- Optional: PostgreSQL (for multi-DB testing)

### Setup

```bash
git clone https://github.com/your-org/PVE2Services.git
cd PVE2Services
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Running

```bash
# Development (auto-reload)
uvicorn PVE2Services.core.wiki_service.app.main:app --reload --port 8000

# Production
uvicorn PVE2Services.core.wiki_service.app.main:app --host 0.0.0.0 --port 8000
```

### Testing

```bash
# Run all tests (SQLite default)
pytest -q tests

# Stop on first failure
pytest -q tests -x

# With PostgreSQL
PVE2_DB_MODE=postgres_docker pytest -q tests

# Verbose
pytest -v tests
```

### Linting

```bash
ruff check .
```

**Rules:** E, F, I, B with line length 100. The `E402` rule is suppressed in `main.py` (sys.path hack).

### Adding a New Plugin

1. Create `plugins/<NewPlugin>/` (directory name = plugin id, lowercased)
2. Add a valid `plugin.yaml` manifest (schema in `libs/plugin_manifest.py`;
   full guide + template: [`PLUGIN_DEVELOPMENT.md`](../PLUGIN_DEVELOPMENT.md))
3. Implement `__init__.py` with `load_plugin(app)` function
4. Define the 4 convention endpoints: `info`, `config` (GET/POST), `test`, `sync`
5. Add admin HTML in `frontend.py` (or the central templates directory)
6. Add the directory name to `plugins/bundled_plugins.txt` so the bundled
   release ships it as `trusted`
7. Register any new DB tables in `libs/db_adapter.py`
8. If background scheduling is needed, use `libs/scheduler.add_job()` and expose scheduler endpoints
9. Release it to the store: `python tools/build_plugin.py plugins/<NewPlugin> \
   --signing-key <private.pem> --publish` (run by the release workflow on tags)

**Template for `__init__.py`:**

```python
import logging
from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)

PLUGIN_ID = "myplugin"
router = APIRouter(prefix=f"/api/plugins/{PLUGIN_ID}")

@router.get("/info")
async def plugin_info():
    return {
        "id": PLUGIN_ID,
        "name": "My Plugin",
        "description": "Description",
        "icon": "🔌",
        "version": "0.1.0"
    }

def load_plugin(app: FastAPI) -> None:
    app.include_router(router)
    # Register any startup logic
```

### Cross-plugin Data Flow

```
Plugin (e.g., PVE2Power)                 PVE2Core
       │                                     │
       │  GET /api/plugins/pve2core/vms      │
       │────────────────────────────────────▶│
       │                                     │
       │  [Normalized VM list]               │
       │◀────────────────────────────────────│
       │                                     │
       │  POST /api/plugins/pve2core/        │
       │  vms/{vmid}/status                  │
       │────────────────────────────────────▶│
       │                                     │
       │  [Action result]                    │
       │◀────────────────────────────────────│
```

---

## 11. Deployment Guide

### Docker (Recommended)

```bash
# Build
docker build -t pve2-services -f core/wiki_service/Dockerfile .

# Run
docker run -d \
  --name pve2-services \
  -p 8000:8000 \
  -v "$(pwd)/pve2wiki.db:/app/pve2wiki.db" \
  pve2-services
# Then open http://localhost:8000 and create the admin account on first boot.
```

### Docker Compose

```yaml
# docker-compose.yml (provided in repo root)
services:
  app:
    build:
      context: .
      dockerfile: core/wiki_service/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - PVE2_DB_MODE=sqlite
    volumes:
      - ./pve2wiki.db:/app/pve2wiki.db
```

For PostgreSQL:

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      - PVE2_DB_MODE=postgres_docker
    depends_on:
      postgres:
        condition: service_healthy
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: pve2
      POSTGRES_PASSWORD: pve2
      POSTGRES_DB: pve2wiki
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pve2"]
      interval: 5s
```

### Manual (No Docker)

```bash
pip install -r requirements.txt
uvicorn PVE2Services.core.wiki_service.app.main:app --host 0.0.0.0 --port 8000
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name pve2.example.com;

    ssl_certificate /etc/ssl/certs/pve2.crt;
    ssl_certificate_key /etc/ssl/private/pve2.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Data Persistence

- **SQLite:** Mount `/app/pve2wiki.db` (or the DB file in working directory)
- **PostgreSQL:** Use Docker volume for Postgres data

---

## 12. Troubleshooting

### Common Issues

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| `401` on API calls | Not logged in | `POST /admin/login` first |
| `ImportError` on startup | Missing dependencies | `pip install -r requirements.txt` |
| DB file not found | Wrong working directory | Start from PVE2Services root |
| Plugin not appearing in hub | Discovery error | Check server logs for `Failed to load plugin` |
| Pi‑hole sync fails | API v5 token permissions | Ensure token has `query` + `manage` perms |
| AdGuard sync fails | Basic auth | Double-check URL/user/password |
| Discord webhook fails | Webhook URL | Test with `curl` independently |
| SSH deploy fails | Host key | First SSH manually to accept host key |
| Nginx config syntax error | `extra_directives` validation | Check Nginx error logs |
| Wiki sync creates empty pages | Connection/Wiki.js token | Check Wiki.js API token permissions |
| Scheduler job won't start | Job name already exists | Stop the existing job first |
| `StrictHostKeyChecking` error | Host not in `known_hosts` | SSH manually once to accept fingerprint |
| Auth session expires | 24h token limit | Re-login at `/admin/login` |

### Logs

The application uses Python's `logging` module. Set the log level via environment:

```bash
# More verbose
LOG_LEVEL=DEBUG uvicorn ... --log-level debug

# Default
# INFO level
```

### Diagnosis Commands

```bash
# Check if the server is running
curl http://localhost:8000/health

# Create the admin account (first boot only; 403 afterwards)
curl -v -X POST http://localhost:8000/admin/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "new_password": "choose-a-strong-password"}'

# Check plugin info
curl http://localhost:8000/api/plugins/pve2dns/info \
  -b "pve2_session=<token>"

# Test DB directly (SQLite)
sqlite3 pve2wiki.db ".tables"
sqlite3 pve2wiki.db "SELECT * FROM connector_configs;"
```

### Resetting Auth

If locked out, delete or reset the settings table:

```bash
sqlite3 pve2wiki.db "DELETE FROM settings WHERE key='admin_password_hash';"
sqlite3 pve2wiki.db "DELETE FROM settings WHERE key='auth_secret';"
# Restart the server — defaults will be restored on first login
```

---

## Appendix: sys.path Hack

In `main.py` and `conftest.py`, the import path is fixed via:

```python
import sys
from pathlib import Path
_file_path = Path(__file__).resolve()
sys.path.insert(0, str(_file_path.parents[4]))  # main.py: 4 levels up
sys.path.insert(0, str(_file_path.parents[2]))  # conftest.py: 2 levels up
```

This is required because the project is structured as `PVE2Services.core.wiki_service.app.main` but the root package is under the repository root. The `.parents[N]` value depends on the relative depth of the file from the repository root.

---

*Documentation generated from project analysis — June 2026.*
