import logging
import re
from typing import Any, Dict

from .db import get_sync_state, get_templates
from .store import create_page

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

logger = logging.getLogger("app_sync")


_FIELD_RE = re.compile(r"{{\s*\.([A-Za-z0-9_]+)\s*(?:\|[^}]*)?}}")


def render_template(template: str, data: Dict[str, Any]) -> str:
    if not template:
        return ""

    def _repl(m):
        key = m.group(1)
        return str(data.get(key) or "")

    return _FIELD_RE.sub(_repl, template)


def run_sync(limit: int = 50) -> Dict[str, int]:
    """Read `sync_state` from DB and create pages in the in-memory store.

    Returns summary with counts.
    """
    entries = get_sync_state(limit)
    templates = get_templates()
    created = 0

    for e in entries:
        entity = e.get("entity_data") or {}
        # entity may be a JSON string; if so, it's already parsed in db.get_sync_state
        title = entity.get("Name") or entity.get("ID") or e.get("id")
        ttype = e.get("type")
        tpl = templates.get(ttype) or "# {Name}\n\nNo template"
        # try simple render from entity dict
        content = render_template(tpl, entity if isinstance(entity, dict) else {})

        # create page in main app store
        try:
            create_page(title, content)
            created += 1
        except Exception as e:
            logger.exception("Failed creating page for %s: %s", title, e)
            continue

    if _audit:
        try:
            _audit.log("sync", "INFO", "Sync completed",
                       detail=f"processed: {len(entries)}\n"
                              f"created: {created}")
        except Exception:
            pass
    return {"processed": len(entries), "created": created}
