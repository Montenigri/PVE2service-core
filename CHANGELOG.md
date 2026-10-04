# Changelog

All notable changes to PVE2Services will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Fixed

- Test isolation: the `PVE2_DB_PATH` resolution test now clears `PVE2_DB_MODE`
  (the CI Postgres leg sets it and it takes precedence over `PVE2_DB_PATH`).

## [0.1.0] - 2026-10-04

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
- PVE2LLM: Interact with PVE2 with a chatbot
- Authentication system (HMAC-SHA256)
- CSRF protection
- Rate limiting
- Security headers
- Docker support (SQLite and PostgreSQL)
- CI/CD with GitHub Actions
- Comprehensive technical documentation

[Unreleased]: https://github.com/Montenigri/pve2service-core/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Montenigri/pve2service-core/releases/tag/v0.1.0
