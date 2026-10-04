#!/usr/bin/env bash
# End-to-end check for the pve2drift plugin against a running PVE2 core.
#
#   tools/e2e_pve2drift.sh            # seed a demo managed resource + open drift
#   tools/e2e_pve2drift.sh --cleanup  # remove the demo data
#
# Non-invasive: it never touches Proxmox. It ingests a synthetic `tofu show -json`
# declaring a resource that is NOT live, so the drift check reports a
# `resource_missing` event you can see in the admin UI at /admin/pve2drift.
#
# Env overrides: PVE2_URL (default http://localhost:8000), PVE2_USER, PVE2_PASS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE="${PVE2_URL:-http://localhost:8000}"
USER="${PVE2_USER:-admin}"
PASS="${PVE2_PASS:-pve2admin}"
ENVNAME="e2e-test"
ADDRESS="proxmox_virtual_environment_vm.e2e-demo"
COOKIE="$(mktemp)"
trap 'rm -f "$COOKIE"' EXIT

login() {
  curl -fsS -c "$COOKIE" -X POST "$BASE/admin/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" >/dev/null
}

set_config() {
  curl -fsS -b "$COOKIE" -X POST "$BASE/api/plugins/pve2drift/config" \
    -H 'Content-Type: application/json' \
    -d "{\"enabled\":$1,\"check_interval_seconds\":3600,\"tracked_fields\":[\"cores\",\"memory_mb\",\"disk_gb\",\"node\",\"vmid\"],\"unmanaged_to_audit\":true}" >/dev/null
}

cleanup() {
  login
  set_config false
  docker compose exec -T core python -c "
from sqlalchemy import text
from PVE2Services.libs.db_adapter import DBAdapter
db = DBAdapter()
with db.engine.begin() as c:
    de = c.execute(text(\"DELETE FROM drift_event WHERE resource_id IN (SELECT id FROM managed_resource WHERE cluster_id='$ENVNAME')\")).rowcount
    mr = c.execute(text(\"DELETE FROM managed_resource WHERE cluster_id='$ENVNAME'\")).rowcount
    ak = c.execute(text(\"DELETE FROM drift_api_keys WHERE environment='$ENVNAME'\")).rowcount
    ar = c.execute(text(\"DELETE FROM audit_recommendations WHERE category='drift' AND entity_id LIKE 'drift:$ADDRESS%'\")).rowcount
print(f'removed drift_event={de} managed_resource={mr} api_keys={ak} audit_recs={ar}')
"
  echo "cleanup done"
}

if [[ "${1:-}" == "--cleanup" ]]; then
  cleanup
  exit 0
fi

login
echo "== enable pve2drift ==" ; set_config true
echo "== create ingestion key ($ENVNAME) =="
KEY="$(curl -fsS -b "$COOKIE" -X POST "$BASE/api/plugins/pve2drift/apikeys" \
  -H 'Content-Type: application/json' -d "{\"environment\":\"$ENVNAME\"}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])')"
echo "key (shown once): $KEY"

echo "== ingest synthetic tofu state (resource NOT live) =="
curl -fsS -X POST "$BASE/api/v1/state/ingest" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{
  "format_version":"1.2",
  "values":{"root_module":{"resources":[
    {"address":"proxmox_virtual_environment_vm.e2e-demo","mode":"managed",
     "type":"proxmox_virtual_environment_vm",
     "values":{"node_name":"pve","vm_id":9999,"cpu":{"cores":2},
               "memory":{"dedicated":1024},"disk":[{"size":16}],"tags":["demo"]}}
  ]}}}'
echo

echo "== run drift check =="
curl -fsS -b "$COOKIE" -X POST "$BASE/api/plugins/pve2drift/sync" ; echo
echo "== open drifts =="
curl -fsS -b "$COOKIE" "$BASE/api/plugins/pve2drift/drifts?status=open" | python3 -m json.tool

cat <<EOF

Demo ready. Verify in the browser:
  ${BASE}/admin/pve2drift     (login: ${USER} / ${PASS})
  -> Resources: $ADDRESS
  -> Drifts:    field=resource, category=resource_missing

Clean up:  tools/e2e_pve2drift.sh --cleanup
EOF
