<p align="center">
  <img src="https://img.shields.io/badge/PVE2-v0.1.0-667eea?style=for-the-badge&logo=proxmox&logoColor=white" alt="PVE2 v0.1.0"/>
  <img src="https://img.shields.io/badge/Python-3.11+-764ba2?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/FastAPI-0.100+-059669?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/license-MIT-333?style=for-the-badge" alt="License MIT"/>
  <a href="https://github.com/Montenigri/pve2service-core/actions"><img src="https://img.shields.io/github/actions/workflow/status/Montenigri/pve2service-core/ci.yml?style=for-the-badge&label=CI" alt="CI Status"/></a>
</p>

<h1 align="center">PVE2 Services</h1>

<p align="center">
  <strong>Proxmox Unified Management Platform</strong>
  <br/>
  Connect your Proxmox cluster to <em>dynamic DNS, notifications, power scheduling, reverse proxies, and wiki</em> with a unified panel.
</p>

---

## What is PVE2?

**PVE2 Services** is a **self-hosted** administration panel that connects to your Proxmox VE cluster and synchronizes data with external platforms.

### Who is it for?

- **Sysadmins** who want automation without writing custom scripts
- **Teams** managing Proxmox who want automatically updated documentation on Wiki.js
- **Homelabbers** looking for a single panel for all integrations

### What does it do?

| Plugin | Description | Connects to |
|--------|-------------|-------------|
| **PVE2 Core** | Central data normalization engine | Proxmox VE (API) |
| **PVE2 Dash** | Read-only cluster health dashboard | Proxmox VE (API) |
| **PVE2 Audit** | Resource analysis and rightsizing tips | Proxmox VE (API) |
| **PVE2 DNS** | Sync VM/CT hostnames to DNS | Pi-hole, AdGuard Home |
| **PVE2 Notify** | Send notifications on cluster changes | Discord, Slack, Mattermost, Telegram |
| **PVE2 NUT** | Monitor UPSes via NUT and react to power events | NUT (upsd) |
| **PVE2 Power** | Scheduled start/stop for power savings | Proxmox VE (API) |
| **PVE2 Proxy** | Generate and deploy proxy configurations | Nginx, Traefik, SSH servers |
| **PVE2 Drift** | Detect infrastructure drift from OpenTofu state | OpenTofu/Terraform |
| **PVE2 Wiki** | Sync cluster documentation to external wiki | Wiki.js (GraphQL) |
| **PVE2 LLM** | Winky — ask the cluster questions in natural language | Ollama, OpenAI, Anthropic |

The plugin sources live in a separate repo,
[**Montenigri/PVE2Services-Plugin**](https://github.com/Montenigri/PVE2Services-Plugin).
The core release bundles the first-party open plugins into the image; future
and extra plugins can be installed from the optional plugin store.

---

## Quick Start

```bash
git clone https://github.com/Montenigri/pve2service-core.git
cd pve2service-core
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# First-party plugins live in PVE2Services-Plugin: fetch them into the package.
git clone https://github.com/Montenigri/PVE2Services-Plugin.git ../PVE2Services-Plugin
python tools/sync_plugins.py

uvicorn PVE2Services.core.wiki_service.app.main:app --reload --port 8000
```

The core runs fine with zero plugins too (the hub simply lists nothing); the
sync step is what gives you the bundled set locally. Docker images already
include them.

Open [http://localhost:8000](http://localhost:8000) — on first boot you'll be
asked to **create the admin account** (there is no default password). The
session secret is auto-generated into `.pve2_secret`; connector secrets are
encrypted with a key auto-generated into `.pve2_encryption_key`.

> **Change the password immediately!** Go to `/admin/settings` after first login.

---

## Docker

```bash
docker compose up -d
# Visit http://localhost:8000/admin/login
```

For PostgreSQL: `PVE2_DB_MODE=postgres_docker docker compose up -d`

### Plugin store (optional)

Bundled first-party plugins ship in the image and work out of the box. To
auto-install **additional** signed plugins (new first-party or third-party)
from your own S3/MinIO store at startup:

```bash
PVE2_PLUGIN_STORE_URL=http://minio:9000 \
PVE2_PLUGINS_INSTALL="pve2dns,pve2power==1.0.0" \
docker compose up -d
```

See [`docs/PLUGIN_STORE.md`](docs/PLUGIN_STORE.md) for bucket setup, signing
keys and the release pipeline. A ready-to-use local MinIO dev environment is
in `docker-compose.plugin-store-dev.yml`.

### Cluster / HA deployment

Run multiple replicas behind a load balancer with `docker compose -f docker-compose.ha.yml up -d`.
Requirements:

- **Postgres** — shared state; SQLite cannot be shared between replicas
- **Shared secrets** — every replica needs the same `.pve2_secret` (sessions)
  and `.pve2_encryption_key` (stored secrets); the HA compose file shares them
  via a volume, on multi-host setups distribute the two key files yourself
- **Forward the Host header** in your proxy — the CSRF check compares
  `Origin` against it
- Scheduled jobs (sync, power, notify) coordinate through DB leases: only the
  lease holder executes each job, so replicas never duplicate Proxmox calls,
  notifications, or power actions. If the leader dies, another replica takes
  over on the next interval.
- Set `PVE2_TRUST_XFF=true` so rate limiting sees real client IPs behind the
  proxy.

---

## Password recovery

Forgot the admin password? Use the shell-only recovery tool on the host (or
inside the container) — it targets the same database as the running app.

Check the current state:

```bash
python tools/reset_admin_password.py status
```

**Option A — set a new password directly** (interactive prompt; alternatively
`--password-stdin` or `--password`):

```bash
python tools/reset_admin_password.py set --username admin
```

**Option B — full reset to first boot**: removes the credentials entirely, so
the next web access walks you through `/admin/setup` again:

```bash
python tools/reset_admin_password.py reset
```

With Docker Compose, prefix the commands, e.g.:

```bash
docker compose exec core python /app/tools/reset_admin_password.py set
```

Both modes also clear login rate-limit blocks. Existing sessions stay valid;
delete `.pve2_secret` (or `PVE2_SECRET_FILE`) to invalidate them all.

---

## Security

All admin endpoints are protected by HMAC-SHA256 authentication (httponly cookies, 24h). Sandboxed template engine, protected SSH injection, prevented SQL injection, blocked SSRF.

**Plugin supply chain:** every plugin ships a validated `plugin.yaml` manifest
that is checked *before* its code is ever imported; the effective trust tier
is derived from verified provenance (ed25519 signature against the project's
committed public key, or first-party membership in the release) — never from
a field the plugin author could declare. Store-installed packages are
signature- and checksum-verified with safe extraction before touching disk.
Details: [`docs/PLUGIN_STORE.md`](docs/PLUGIN_STORE.md).

---

## Development

```bash
pip install -r requirements-dev.txt   # runtime + ruff + pytest + moto
ruff check .                          # lint
pytest -q tests                       # test (SQLite)
pytest -q tests -x                    # stop on first failure
```

---

## Architecture

```
PVE2Services/core/wiki_service/app/  <- FastAPI server + admin hub (import package)
PVE2Services/libs/                   <- Shared libraries (DB, scheduler, plugin loader/store)
PVE2Services/plugins/                <- Bundle target: bundled_plugins.txt + plugins fetched at build/dev time
tests/                               <- Core test-suite
tools/                               <- build/sign tooling, plugin sync, admin recovery
keys/plugin_signing.pub              <- public signing key for store packages
```

Every plugin follows the `load_plugin(app: FastAPI)` convention exported from
`__init__.py` and declares itself via a validated `plugin.yaml`. First-party
open plugins are bundled at build time from the PVE2Services-Plugin repo;
additional signed plugins are distributed through an optional plugin store
(S3/MinIO) and installed/upgraded at startup. See
[docs/PLUGIN_STORE.md](docs/PLUGIN_STORE.md).

---

## Documentation

- [docs/TECHNICAL.md](docs/TECHNICAL.md) — API reference, configuration, deployment, troubleshooting, DB schema
- [docs/PLUGIN_STORE.md](docs/PLUGIN_STORE.md) — Plugin store, manifests, signing, trust tiers
- [CONTRIBUTING.md](CONTRIBUTING.md) — How to contribute
- [SECURITY.md](SECURITY.md) — Vulnerability reporting policy

---

## License

MIT — use, modify, share. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Made with Python and FastAPI | PVE2 Services v0.1.0</sub>
</p>
