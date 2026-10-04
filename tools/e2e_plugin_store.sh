#!/usr/bin/env bash
# End-to-end verification of the plugin signing -> store -> install channel.
#
# Unlike tests/test_plugin_store.py (moto-mocked S3), this exercises a REAL
# S3-compatible server (MinIO from docker-compose.plugin-store-dev.yml), the
# real build/sign tooling and the real core startup installer + loader. The
# verification logic lives in tools/e2e_store_verify.py, run inside a
# python:3.12 container (the project targets 3.12; keeps host Python out of it).
#
#   generate keypair -> build+sign plugin -> publish to MinIO
#     -> core store sync (signature + checksum) -> plugins/<id> on disk
#     -> provenance file -> loader derives trust tier = trusted (signed)
#
# Requirements: docker + docker compose.
#
# Usage:
#   tools/e2e_plugin_store.sh [plugin_dir]
#
# Default plugin_dir is ../PVE2Services-Plugin/plugins/example_plugin.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.plugin-store-dev.yml"
PLUGIN_SRC="${1:-$ROOT/../PVE2Services-Plugin/plugins/example_plugin}"

if [[ ! -d "$PLUGIN_SRC" ]]; then
  echo "plugin dir not found: $PLUGIN_SRC" >&2
  exit 2
fi
PLUGIN_DIR_ABS="$(cd "$PLUGIN_SRC" && pwd)"

VOL="pve2-store-e2e-$$"
docker volume create "$VOL" >/dev/null
cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
  docker volume rm -f "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> [1/3] starting MinIO store"
docker compose -f "$COMPOSE_FILE" up -d
# store-init is a one-shot that creates the bucket + empty index.json. Include
# stopped containers (-a) so we wait for a container that already finished.
init_cid="$(docker compose -f "$COMPOSE_FILE" ps -aq store-init)"
if [[ -n "$init_cid" ]]; then
  # `docker wait` prints the container's exit code on stdout.
  rc="$(docker wait "$init_cid")"
  if [[ "$rc" != "0" ]]; then
    echo "    store-init failed (exit $rc):" >&2
    docker logs "$init_cid" >&2 || true
    exit 1
  fi
fi

echo "==> [2/3] build -> sign -> publish -> install -> verify (python:3.12-slim)"
docker run --rm --network host \
  -v "$ROOT":/app:ro \
  -v "$VOL":/work \
  -v "$PLUGIN_DIR_ABS":/plugin-src:ro \
  -w /app \
  -e PYTHONPATH=/app \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PIP_CACHE_DIR=/work/.pip-cache \
  -e PVE2_STORE_ENDPOINT=http://localhost:19000 \
  -e PVE2_STORE_BUCKET=pve2services-plugins \
  -e PVE2_STORE_ACCESS_KEY=pve2store \
  -e PVE2_STORE_SECRET_KEY=pve2store-secret \
  -e PVE2_STORE_REGION=us-east-1 \
  -e PVE2_PLUGIN_SRC=/plugin-src \
  -e PVE2_E2E_WORK_DIR=/work \
  -e PVE2_DB_URL=sqlite:////work/e2e.db \
  -e PVE2_SECRET_FILE=/work/.pve2_secret \
  -e PVE2_ENCRYPTION_KEY_FILE=/work/.pve2_encryption_key \
  python:3.12-slim bash -lc '
    set -euo pipefail
    pip install -q -r requirements.txt
    python tools/e2e_store_verify.py
  '

echo "==> [3/3] PASS: signing -> store -> install -> trusted load verified"
