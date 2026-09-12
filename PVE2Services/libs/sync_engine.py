from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jinja2 import BaseLoader
from jinja2.sandbox import SandboxedEnvironment

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

if TYPE_CHECKING:
    from PVE2Services.core.wiki_service.app.proxmox_adapter import ProxmoxClient

from .db_adapter import DBAdapter
from .wiki_adapter import PageNotFoundError, WikiAdapter


def format_bytes(b: int) -> str:
    if not b:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit = 0
    size = float(b)
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{size:.0f} {units[unit]}"
    return f"{size:.2f} {units[unit]}"


def _format_uptime(sec: int) -> str:
    if not sec:
        return "0s"
    days, sec = divmod(sec, 86400)
    hours, sec = divmod(sec, 3600)
    minutes, sec = divmod(sec, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def hash_entity(e: Dict[str, Any]) -> str:
    s = f"{e.get('ID') or e.get('id')}|{e.get('Name') or e.get('name')}|{e.get('Type') or e.get('type')}|{e.get('Status') or e.get('status')}|{e.get('Node') or e.get('node')}|{e.get('Cores') or e.get('cores') or 0}|{e.get('MemoryMB') or e.get('memory_mb') or 0}|{e.get('IPAddress') or e.get('ip_address')}"
    return hashlib.sha256(s.encode()).hexdigest()


class Engine:
    def __init__(self, db: DBAdapter, pve_client: Optional[ProxmoxClient] = None, wiki=None, logger=None):
        self.db = db
        self.pve = pve_client
        self.wiki = wiki or WikiAdapter()
        self.logger = logger or logging.getLogger("sync_engine")

    async def run(self) -> Dict[str, int]:
        processed = 0
        created = 0
        updated = 0

        # fetch current PVE state via client when available
        entities: List[Dict[str, Any]] = []
        if self.pve:
            try:
                nodes = await self.pve.get_nodes()
                entities.extend(nodes)
            except Exception as e:
                self.logger.exception("Error fetching nodes: %s", e)
            try:
                vms = await self.pve.get_vms()
                entities.extend(vms)
            except Exception as e:
                self.logger.exception("Error fetching vms: %s", e)
            try:
                lxcs = await self.pve.get_lxcs()
                entities.extend(lxcs)
            except Exception as e:
                self.logger.exception("Error fetching lxcs: %s", e)
            try:
                storage = await self.pve.get_storage()
                entities.extend(storage)
            except Exception as e:
                self.logger.exception("Error fetching storage: %s", e)
            try:
                disks = await self.pve.get_disks()
                entities.extend(disks)
            except Exception as e:
                self.logger.exception("Error fetching disks: %s", e)

        # load sync state from DB
        db_states = {s['id']: s for s in self.db.get_all_sync_states()}

        try:
            templates = self.db.get_templates()
        except Exception as e:
            self.logger.exception("Error loading templates: %s", e)
            templates = {}

        for ent in entities:
            processed += 1
            ent_id = ent.get('ID') or ent.get('id')
            ent_type = ent.get('Type') or ent.get('type')
            h = hash_entity(ent)

            state = db_states.get(ent_id)
            if not state or state.get('archived'):
                # create
                tpl = templates.get(ent_type, "# {{ Name }}\n")
                rendered = self.render_template(tpl, ent)
                title = ent.get('Name') or ent.get('name') or ent_id
                try:
                    self.wiki.create_page(f"pve2wiki/{ent_type}/{ent_id}", rendered, title)
                    self.db.update_sync_state({
                        'id': ent_id,
                        'type': ent_type,
                        'hash': h,
                        'wiki_path': f"pve2wiki/{ent_type}/{ent_id}",
                        'archived': False,
                        'entity_data': ent,
                    })
                    self.db.save_changelog('created', ent_type, ent_id, title, 'Created wiki page')
                    created += 1
                except Exception as e:
                    self.logger.exception("Error creating page for %s: %s", ent_id, e)
                    continue
            else:
                if state.get('hash') != h:
                    tpl = templates.get(ent_type, "# {{ Name }}\n")
                    rendered = self.render_template(tpl, ent)
                    try:
                        self.wiki.update_page(state.get('wiki_path'), rendered)
                        state['hash'] = h
                        state['entity_data'] = ent
                        self.db.update_sync_state(state)
                        self.db.save_changelog('updated', ent_type, ent_id, ent.get('Name') or ent.get('name') or ent_id, 'Updated page')
                        updated += 1
                    except PageNotFoundError:
                        orphan = (self.db.get_setting("wiki_orphan_handling") or "recreate").lower()
                        if orphan == "recreate":
                            title = ent.get('Name') or ent.get('name') or ent_id
                            try:
                                self.wiki.create_page(state.get('wiki_path', f"pve2wiki/{ent_type}/{ent_id}"), rendered, title)
                                state['hash'] = h
                                state['entity_data'] = ent
                                self.db.update_sync_state(state)
                                self.db.save_changelog('updated', ent_type, ent_id, title, 'Recreated orphaned wiki page')
                                updated += 1
                            except Exception as e2:
                                self.logger.exception("Error recreating page for %s: %s", ent_id, e2)
                        else:
                            self.logger.debug("Ignoring orphaned page for %s", ent_id)
                    except Exception as e:
                        self.logger.exception("Error updating page for %s: %s", ent_id, e)
                        continue

        if _audit:
            try:
                _audit.log("sync_engine", "INFO", "Sync engine run completed",
                           detail=f"processed: {processed}\n"
                                  f"created: {created}\n"
                                  f"updated: {updated}")
            except Exception:
                pass
        return {'processed': processed, 'created': created, 'updated': updated}

    def render_template(self, tpl: str, ent: Dict[str, Any]) -> str:
        env = SandboxedEnvironment(loader=BaseLoader())
        env.filters['FormatBytes'] = format_bytes
        env.filters['FormatMB'] = lambda mb: format_bytes((mb or 0) * 1024 * 1024)
        env.filters['format_mb'] = env.filters['FormatMB']
        env.filters['format_size'] = format_bytes
        env.filters['format_uptime'] = _format_uptime

        def _entity_history(entity_id: str) -> str:
            try:
                from sqlalchemy import text as sql_text

                with self.db.engine.connect() as conn:
                    rows = conn.execute(
                        sql_text(
                            "SELECT timestamp, details FROM changelog "
                            "WHERE entity_id = :eid ORDER BY id DESC LIMIT 10"
                        ),
                        {"eid": entity_id},
                    ).fetchall()
                if not rows:
                    return ""
                lines = []
                for r in rows:
                    ts = str(r[0])[:10]
                    lines.append(f"- **{ts}**: {r[1]}")
                return "\n".join(lines)
            except Exception:
                return ""

        env.filters['entity_history'] = _entity_history
        template = env.from_string(tpl)
        # Jinja uses dict keys; convert keys to simple names
        context = {k: v for k, v in ent.items()}
        try:
            return template.render(**context)
        except Exception as e:
            self.logger.exception("Template render failed: %s", e)
            return str(ent)
