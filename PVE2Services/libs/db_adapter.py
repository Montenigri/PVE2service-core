import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("pve2.db_adapter")


def resolve_db_url() -> str:
    """Resolve the database URL from environment variables with a unified fallback."""
    env_url = os.getenv("PVE2_DB_URL")
    if env_url:
        return env_url
    pve2_mode = os.getenv("PVE2_DB_MODE")
    if pve2_mode in ("postgres", "postgres_docker"):
        user = os.getenv("PVE2_DB_USER", "pve2")
        pwd = os.getenv("PVE2_DB_PASS", "pve2pass")
        host = os.getenv("PVE2_DB_HOST", "db")
        name = os.getenv("PVE2_DB_NAME", "pve2")
        if pwd == "pve2pass":
            logger.warning(
                "PVE2_DB_PASS is using the default value 'pve2pass' — change it for production!"
            )
        return f"postgresql://{user}:{pwd}@{host}:5432/{name}"
    env_path = os.getenv("PVE2_DB_PATH")
    if env_path:
        return f"sqlite:///{env_path}"
    workspace = Path(__file__).resolve().parents[2]
    legacy = workspace / "PVE2Wiki" / "pve2wiki.db"
    if legacy.exists():
        return f"sqlite:///{legacy}"
    default = workspace / "pve2.db"
    return f"sqlite:///{default}"


_db_lock = threading.Lock()
_db_instance: Optional["DBAdapter"] = None


def get_db() -> "DBAdapter":
    """Return a thread-safe singleton DBAdapter instance."""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = DBAdapter()
    return _db_instance


class DBAdapter:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or resolve_db_url()

        self.engine: Engine = create_engine(self.db_url, future=True)
        self._ensure_schema()

    def _ensure_schema(self):
        schema_sql = [
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE TABLE IF NOT EXISTS sync_state (id TEXT PRIMARY KEY, type TEXT, hash TEXT, wiki_path TEXT, archived BOOLEAN DEFAULT FALSE, entity_data TEXT)",
            "CREATE TABLE IF NOT EXISTS storage_history (id INTEGER PRIMARY KEY, node TEXT, storage_name TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_bytes INTEGER, used_bytes INTEGER, content_types TEXT)",
            "CREATE TABLE IF NOT EXISTS health_history (id INTEGER PRIMARY KEY, component TEXT, item_id TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT, details TEXT)",
            "CREATE TABLE IF NOT EXISTS changelog (id INTEGER PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, action TEXT, entity_type TEXT, entity_id TEXT, entity_name TEXT, details TEXT)",
            "CREATE TABLE IF NOT EXISTS templates (type TEXT PRIMARY KEY, content TEXT)",
            "CREATE TABLE IF NOT EXISTS resource_history (id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, node TEXT NOT NULL, entity_type TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, cpu_avg REAL, cpu_max REAL, mem_used INTEGER, mem_max INTEGER, disk_used INTEGER, disk_max INTEGER, netin INTEGER, netout INTEGER)",
            "CREATE TABLE IF NOT EXISTS audit_recommendations (id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, entity_name TEXT, entity_type TEXT, node TEXT, category TEXT NOT NULL, severity TEXT NOT NULL, current_value TEXT, suggested_value TEXT, rationale TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, applied BOOLEAN DEFAULT FALSE, dismissed BOOLEAN DEFAULT FALSE)",
            "CREATE TABLE IF NOT EXISTS dns_records (id INTEGER PRIMARY KEY, machine_id TEXT UNIQUE NOT NULL, machine_name TEXT NOT NULL DEFAULT '', hostname TEXT NOT NULL DEFAULT '', ip_address TEXT NOT NULL DEFAULT '', enabled BOOLEAN DEFAULT TRUE, last_synced TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS power_schedules (id INTEGER PRIMARY KEY, machine_id TEXT UNIQUE NOT NULL, machine_name TEXT NOT NULL DEFAULT '', enabled BOOLEAN DEFAULT TRUE, weekday_start_time TEXT NOT NULL DEFAULT '08:00', weekday_stop_time TEXT NOT NULL DEFAULT '18:00', weekend_start_time TEXT NOT NULL DEFAULT '10:00', weekend_stop_time TEXT NOT NULL DEFAULT '20:00', monday BOOLEAN DEFAULT TRUE, tuesday BOOLEAN DEFAULT TRUE, wednesday BOOLEAN DEFAULT TRUE, thursday BOOLEAN DEFAULT TRUE, friday BOOLEAN DEFAULT TRUE, saturday BOOLEAN DEFAULT FALSE, sunday BOOLEAN DEFAULT FALSE, force_stop_timeout INTEGER DEFAULT 5, last_action TEXT, last_action_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS proxy_mappings (id INTEGER PRIMARY KEY, machine_id TEXT UNIQUE NOT NULL, machine_name TEXT NOT NULL DEFAULT '', hostname TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '', ip_address TEXT NOT NULL DEFAULT '', port INTEGER DEFAULT 80, websocket BOOLEAN DEFAULT FALSE, cache BOOLEAN DEFAULT FALSE, rate_limit INTEGER DEFAULT 0, ssl_enabled BOOLEAN DEFAULT FALSE, extra_directives TEXT DEFAULT '', enabled BOOLEAN DEFAULT TRUE, last_deployed TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS wiki_pages (id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE NOT NULL, entity_type TEXT NOT NULL, wiki_path TEXT NOT NULL, wiki_page_id INTEGER, title TEXT NOT NULL, content_hash TEXT NOT NULL, last_synced TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS managed_resource (id INTEGER PRIMARY KEY, resource_address TEXT UNIQUE NOT NULL, resource_type TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'bpg', matching_tag TEXT UNIQUE NOT NULL, last_vmid INTEGER, cluster_id TEXT DEFAULT '', node TEXT, declared_state TEXT, last_ingested_at TIMESTAMP, last_checked_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS drift_event (id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL, field TEXT NOT NULL, declared_value TEXT, live_value TEXT, status TEXT NOT NULL DEFAULT 'open', category TEXT NOT NULL DEFAULT 'attribute_drift', first_detected_at TIMESTAMP, last_detected_at TIMESTAMP, resolved_at TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS drift_api_keys (id INTEGER PRIMARY KEY, environment TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_used_at TIMESTAMP, revoked BOOLEAN DEFAULT FALSE)",
            "CREATE TABLE IF NOT EXISTS nut_history (id INTEGER PRIMARY KEY, ups_name TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT '', battery_charge REAL, battery_runtime REAL, ups_load REAL, realpower REAL, apparent_power REAL, input_voltage REAL, output_voltage REAL, battery_voltage REAL, vars_json TEXT)",
            "CREATE TABLE IF NOT EXISTS nut_events (id INTEGER PRIMARY KEY, ups_name TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, event_type TEXT NOT NULL, from_status TEXT, to_status TEXT, detail TEXT)",
        ]
        with self.engine.begin() as conn:
            for s in schema_sql:
                conn.execute(text(s))

    def get_setting(self, key: str) -> Optional[str]:
        with self.engine.connect() as conn:
            r = conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).fetchone()
            return r[0] if r else None

    def set_setting(self, key: str, value: str):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v) ON CONFLICT(key) DO UPDATE SET value = excluded.value"), {"k": key, "v": value})

    def get_all_settings(self) -> Dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT key, value FROM settings")).fetchall()
            return {r[0]: r[1] for r in rows}

    def get_all_sync_states(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id, type, hash, wiki_path, archived, entity_data FROM sync_state")).fetchall()
            return [r._asdict() for r in rows]

    def get_sync_state(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            # rowid only exists on SQLite; other dialects fall back to the PK
            if self.engine.dialect.name == "sqlite":
                order = "ORDER BY rowid DESC"
            else:
                order = "ORDER BY id DESC"
            rows = conn.execute(text(f"SELECT id, type, hash, wiki_path, archived, entity_data FROM sync_state {order} LIMIT :lim"), {"lim": limit}).fetchall()
            out = []
            for r in rows:
                d = r._asdict()
                try:
                    d["entity_data"] = json.loads(d.get("entity_data") or "null")
                except (json.JSONDecodeError, TypeError):
                    pass
                out.append(d)
            return out

    def update_sync_state(self, state: Dict[str, Any]):
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM sync_state WHERE id = :id"), {"id": state.get("id")})
            conn.execute(text("INSERT INTO sync_state (id, type, hash, wiki_path, archived, entity_data) VALUES (:id, :type, :hash, :wiki_path, :archived, :entity_data)"),
                         {"id": state.get("id"), "type": state.get("type"), "hash": state.get("hash"), "wiki_path": state.get("wiki_path"), "archived": int(bool(state.get("archived"))), "entity_data": json.dumps(state.get("entity_data") or {})})

    def save_changelog(self, action: str, entity_type: str, entity_id: str, entity_name: str, details: str):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO changelog (timestamp, action, entity_type, entity_id, entity_name, details) VALUES (:ts, :a, :t, :id, :n, :d)"), {"ts": local_now(), "a": action, "t": entity_type, "id": entity_id, "n": entity_name, "d": details})

    def get_entity_by_id(self, id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str], Optional[str]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id, type, hash, wiki_path, archived, entity_data FROM sync_state WHERE id = :id"), {"id": id}).fetchone()
            if not row:
                return None, None, None, None
            d = row._asdict()
            return d, d.get("id"), d.get("entity_data"), d.get("wiki_path")

    def get_entity_changes(self, entity_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT timestamp, action, entity_type, entity_id, entity_name, details FROM changelog WHERE entity_id = :id ORDER BY timestamp DESC LIMIT :lim"), {"id": entity_id, "lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def get_changelog(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT timestamp, action, entity_type, entity_id, entity_name, details FROM changelog ORDER BY id DESC LIMIT :lim"), {"lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def get_template(self, ttype: str) -> Optional[str]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT content FROM templates WHERE type = :t"), {"t": ttype}).fetchone()
            return row[0] if row else None

    def get_templates(self) -> Dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT type, content FROM templates")).fetchall()
            return {r[0]: r[1] for r in rows}

    def get_storage_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT node, storage_name, timestamp, total_bytes, used_bytes, content_types FROM storage_history ORDER BY id DESC LIMIT :lim"), {"lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def get_health_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT component, item_id, timestamp, status, details FROM health_history ORDER BY id DESC LIMIT :lim"), {"lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def save_storage_history(self, node: str, storage_name: str,
                             total_bytes: int = 0, used_bytes: int = 0,
                             content_types: str = ""):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO storage_history (node, storage_name, timestamp, total_bytes, used_bytes, content_types)
                        VALUES (:n, :s, :ts, :t, :u, :c)"""),
                {"n": node, "s": storage_name, "ts": local_now(),
                 "t": total_bytes, "u": used_bytes, "c": content_types},
            )

    def save_health_history(self, component: str, item_id: str, status: str, details: str = ""):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO health_history (component, item_id, timestamp, status, details) VALUES (:c, :i, :ts, :s, :d)"),
                {"c": component, "i": item_id, "ts": local_now(), "s": status, "d": details},
            )

    # --- Audit / rightsizing tables ---

    def insert_resource_history(self, entity_id: str, node: str, entity_type: str,
                                 cpu_avg: float = 0, cpu_max: float = 0,
                                 mem_used: int = 0, mem_max: int = 0,
                                 disk_used: int = 0, disk_max: int = 0,
                                 netin: int = 0, netout: int = 0):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO resource_history
                (entity_id, node, entity_type, timestamp, cpu_avg, cpu_max, mem_used, mem_max, disk_used, disk_max, netin, netout)
                VALUES (:eid, :node, :etype, :ts, :ca, :cm, :mu, :mm, :du, :dm, :ni, :no)"""),
                {"eid": entity_id, "node": node, "etype": entity_type, "ts": local_now(),
                 "ca": cpu_avg, "cm": cpu_max, "mu": mem_used, "mm": mem_max,
                 "du": disk_used, "dm": disk_max, "ni": netin, "no": netout})

    def get_resource_history(self, entity_id: str, since: Optional[datetime] = None,
                             limit: int = 500) -> List[Dict[str, Any]]:
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=7)
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT entity_id, node, entity_type, timestamp,
                cpu_avg, cpu_max, mem_used, mem_max, disk_used, disk_max, netin, netout
                FROM resource_history
                WHERE entity_id = :eid AND timestamp >= :since
                ORDER BY timestamp ASC LIMIT :lim"""),
                {"eid": entity_id, "since": since, "lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def get_resource_history_aggregate(self, entity_id: str,
                                       since: Optional[datetime] = None) -> Dict[str, Any]:
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=7)
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT
                AVG(cpu_avg) as cpu_avg, MAX(cpu_max) as cpu_peak,
                AVG(mem_used) as mem_avg, MAX(mem_used) as mem_peak,
                MAX(mem_max) as mem_max,
                AVG(CAST(mem_used AS REAL) / NULLIF(mem_max, 0)) as mem_pct_avg,
                AVG(CAST(disk_used AS REAL) / NULLIF(disk_max, 0)) as disk_pct_avg,
                AVG(disk_used) as disk_avg, MAX(disk_used) as disk_peak
                FROM resource_history
                WHERE entity_id = :eid AND timestamp >= :since"""),
                {"eid": entity_id, "since": since}).fetchone()
            return row._asdict() if row else {}

    def get_resource_history_coverage(self) -> Dict[str, Dict[str, Any]]:
        """Per-entity data coverage: first sample timestamp and sample count.

        Used to warn when an entity has less than the recommended history
        (e.g. 7 days) before trusting rightsizing suggestions.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT entity_id, MIN(timestamp) AS first_seen, COUNT(*) AS samples
                FROM resource_history GROUP BY entity_id""")).fetchall()
        return {
            r[0]: {"first_seen": r[1], "samples": r[2]}
            for r in rows
        }

    def prune_resource_history(self, days: int = 30) -> int:
        """Delete samples older than `days` days. Returns rows removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM resource_history WHERE timestamp < :c"),
                             {"c": cutoff})
            return r.rowcount or 0

    def save_recommendation(self, entity_id: str, entity_name: str, entity_type: str,
                            node: str, category: str, severity: str,
                            current_value: str, suggested_value: str,
                            rationale: str) -> int:
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                return conn.execute(text("""INSERT INTO audit_recommendations
                    (entity_id, entity_name, entity_type, node, category, severity,
                     current_value, suggested_value, rationale)
                    VALUES (:eid, :en, :et, :node, :cat, :sev, :cur, :sug, :rat)
                    RETURNING id"""),
                    {"eid": entity_id, "en": entity_name, "et": entity_type,
                     "node": node, "cat": category, "sev": severity,
                     "cur": current_value, "sug": suggested_value, "rat": rationale}).scalar_one()
            r = conn.execute(text("""INSERT INTO audit_recommendations
                (entity_id, entity_name, entity_type, node, category, severity,
                 current_value, suggested_value, rationale)
                VALUES (:eid, :en, :et, :node, :cat, :sev, :cur, :sug, :rat)"""),
                {"eid": entity_id, "en": entity_name, "et": entity_type,
                 "node": node, "cat": category, "sev": severity,
                 "cur": current_value, "sug": suggested_value, "rat": rationale})
            return r.lastrowid or 0

    def get_recommendations(self, entity_id: Optional[str] = None,
                            category: Optional[str] = None,
                            severity: Optional[str] = None,
                            active_only: bool = True) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        conditions = []
        if entity_id:
            conditions.append("entity_id = :eid")
            params["eid"] = entity_id
        if category:
            conditions.append("category = :cat")
            params["cat"] = category
        if severity:
            conditions.append("severity = :sev")
            params["sev"] = severity
        if active_only:
            conditions.append("applied = 0 AND dismissed = 0")
        where = " AND ".join(conditions) if conditions else "1=1"
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""SELECT id, entity_id, entity_name, entity_type, node,
                category, severity, current_value, suggested_value, rationale,
                created_at, applied, dismissed
                FROM audit_recommendations WHERE {where}
                ORDER BY created_at DESC"""), params).fetchall()
            return [r._asdict() for r in rows]

    def dismiss_recommendation(self, rec_id: int) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("UPDATE audit_recommendations SET dismissed = 1 WHERE id = :id"),
                             {"id": rec_id})
            return r.rowcount > 0

    def mark_recommendation_applied(self, rec_id: int) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("UPDATE audit_recommendations SET applied = 1 WHERE id = :id"),
                             {"id": rec_id})
            return r.rowcount > 0

    def get_recommendation_summary(self) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM audit_recommendations WHERE applied = 0 AND dismissed = 0")).scalar() or 0
            by_severity = dict(conn.execute(text("SELECT severity, COUNT(*) FROM audit_recommendations WHERE applied = 0 AND dismissed = 0 GROUP BY severity")).fetchall())
            by_category = dict(conn.execute(text("SELECT category, COUNT(*) FROM audit_recommendations WHERE applied = 0 AND dismissed = 0 GROUP BY category")).fetchall())
            return {"total": total, "by_severity": by_severity, "by_category": by_category}

    # --- DNS records table ---

    def _ensure_dns_records_table(self):
        with self.engine.begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS dns_records (
                id INTEGER PRIMARY KEY,
                machine_id TEXT UNIQUE NOT NULL,
                machine_name TEXT NOT NULL DEFAULT '',
                hostname TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                enabled BOOLEAN DEFAULT TRUE,
                last_synced TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))

    def get_dns_records(self) -> List[Dict[str, Any]]:
        self._ensure_dns_records_table()
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT id, machine_id, machine_name, hostname,
                ip_address, enabled, last_synced, created_at, updated_at
                FROM dns_records ORDER BY machine_name ASC""")).fetchall()
            return [r._asdict() for r in rows]

    def get_dns_record(self, machine_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_dns_records_table()
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT id, machine_id, machine_name, hostname,
                ip_address, enabled, last_synced, created_at, updated_at
                FROM dns_records WHERE machine_id = :mid"""),
                {"mid": machine_id}).fetchone()
            return row._asdict() if row else None

    def save_dns_record(self, machine_id: str, machine_name: str,
                        hostname: str, ip_address: str,
                        enabled: bool = True) -> bool:
        self._ensure_dns_records_table()
        with self.engine.begin() as conn:
            try:
                conn.execute(text("""INSERT INTO dns_records
                    (machine_id, machine_name, hostname, ip_address, enabled)
                    VALUES (:mid, :mn, :hn, :ip, :en)"""),
                    {"mid": machine_id, "mn": machine_name, "hn": hostname,
                     "ip": ip_address, "en": int(enabled)})
            except IntegrityError:
                conn.execute(text("""UPDATE dns_records SET
                    machine_name = :mn, hostname = :hn, ip_address = :ip,
                    enabled = :en, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""),
                    {"mn": machine_name, "hn": hostname, "ip": ip_address,
                     "en": int(enabled), "mid": machine_id})
            return True

    def bulk_save_dns_records(self, records: List[Dict[str, Any]]) -> int:
        saved = 0
        for r in records:
            ok = self.save_dns_record(
                machine_id=r["machine_id"],
                machine_name=r.get("machine_name", ""),
                hostname=r.get("hostname", ""),
                ip_address=r.get("ip_address", ""),
                enabled=r.get("enabled", True),
            )
            if ok:
                saved += 1
        return saved

    def delete_dns_record(self, machine_id: str) -> bool:
        self._ensure_dns_records_table()
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM dns_records WHERE machine_id = :mid"),
                             {"mid": machine_id})
            return r.rowcount > 0

    def mark_dns_record_synced(self, machine_ids: List[str]):
        self._ensure_dns_records_table()
        with self.engine.begin() as conn:
            for mid in machine_ids:
                conn.execute(text("""UPDATE dns_records
                    SET last_synced = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""), {"mid": mid})

    def clear_dns_record_synced(self, machine_ids: List[str]):
        """Reset last_synced (e.g. after the record was removed from DNS)."""
        self._ensure_dns_records_table()
        with self.engine.begin() as conn:
            for mid in machine_ids:
                conn.execute(text("""UPDATE dns_records
                    SET last_synced = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""), {"mid": mid})

    # --- Power schedules ---

    def get_power_schedules(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT * FROM power_schedules ORDER BY machine_name ASC""")).fetchall()
            return [r._asdict() for r in rows]

    def get_power_schedule(self, machine_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM power_schedules WHERE machine_id = :mid"),
                               {"mid": machine_id}).fetchone()
            return row._asdict() if row else None

    def save_power_schedule(self, machine_id: str, machine_name: str,
                            enabled: bool = True,
                            weekday_start_time: str = "08:00",
                            weekday_stop_time: str = "18:00",
                            weekend_start_time: str = "10:00",
                            weekend_stop_time: str = "20:00",
                            monday: bool = True, tuesday: bool = True,
                            wednesday: bool = True, thursday: bool = True,
                            friday: bool = True,
                            saturday: bool = False, sunday: bool = False,
                            force_stop_timeout: int = 5) -> bool:
        with self.engine.begin() as conn:
            try:
                conn.execute(text("""INSERT INTO power_schedules
                    (machine_id, machine_name, enabled, weekday_start_time, weekday_stop_time,
                     weekend_start_time, weekend_stop_time,
                     monday, tuesday, wednesday, thursday, friday, saturday, sunday,
                     force_stop_timeout)
                    VALUES (:mid, :mn, :en, :wst, :wsp, :west, :wesp,
                            :mo, :tu, :we, :th, :fr, :sa, :su, :fst)"""),
                    {"mid": machine_id, "mn": machine_name, "en": int(enabled),
                     "wst": weekday_start_time, "wsp": weekday_stop_time,
                     "west": weekend_start_time, "wesp": weekend_stop_time,
                     "mo": int(monday), "tu": int(tuesday),
                     "we": int(wednesday), "th": int(thursday),
                     "fr": int(friday), "sa": int(saturday), "su": int(sunday),
                     "fst": force_stop_timeout})
            except IntegrityError:
                conn.execute(text("""UPDATE power_schedules SET
                    machine_name = :mn, enabled = :en,
                    weekday_start_time = :wst, weekday_stop_time = :wsp,
                    weekend_start_time = :west, weekend_stop_time = :wesp,
                    monday = :mo, tuesday = :tu, wednesday = :we,
                    thursday = :th, friday = :fr, saturday = :sa, sunday = :su,
                    force_stop_timeout = :fst, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""),
                    {"mid": machine_id, "mn": machine_name, "en": int(enabled),
                     "wst": weekday_start_time, "wsp": weekday_stop_time,
                     "west": weekend_start_time, "wesp": weekend_stop_time,
                     "mo": int(monday), "tu": int(tuesday),
                     "we": int(wednesday), "th": int(thursday),
                     "fr": int(friday), "sa": int(saturday), "su": int(sunday),
                     "fst": force_stop_timeout})
            return True

    def update_power_schedule_action(self, machine_id: str, action: str):
        with self.engine.begin() as conn:
            conn.execute(text("""UPDATE power_schedules
                SET last_action = :act, last_action_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE machine_id = :mid"""),
                {"act": action, "mid": machine_id})

    def delete_power_schedule(self, machine_id: str) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM power_schedules WHERE machine_id = :mid"),
                             {"mid": machine_id})
            return r.rowcount > 0

    # --- Proxy mappings ---

    def get_proxy_mappings(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT * FROM proxy_mappings ORDER BY machine_name ASC""")).fetchall()
            return [r._asdict() for r in rows]

    def get_proxy_mapping(self, machine_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM proxy_mappings WHERE machine_id = :mid"),
                               {"mid": machine_id}).fetchone()
            return row._asdict() if row else None

    def save_proxy_mapping(self, machine_id: str, machine_name: str = "",
                           hostname: str = "", domain: str = "",
                           ip_address: str = "", port: int = 80,
                           websocket: bool = False, cache: bool = False,
                           rate_limit: int = 0, ssl_enabled: bool = False,
                           extra_directives: str = "",
                           enabled: bool = True) -> bool:
        with self.engine.begin() as conn:
            try:
                conn.execute(text("""INSERT INTO proxy_mappings
                    (machine_id, machine_name, hostname, domain, ip_address, port,
                     websocket, cache, rate_limit, ssl_enabled, extra_directives, enabled)
                    VALUES (:mid, :mn, :hn, :dom, :ip, :port,
                            :ws, :cache, :rl, :ssl, :extra, :en)"""),
                    {"mid": machine_id, "mn": machine_name, "hn": hostname,
                     "dom": domain, "ip": ip_address, "port": port,
                     "ws": int(websocket), "cache": int(cache), "rl": rate_limit,
                     "ssl": int(ssl_enabled), "extra": extra_directives,
                     "en": int(enabled)})
            except IntegrityError:
                conn.execute(text("""UPDATE proxy_mappings SET
                    machine_name = :mn, hostname = :hn, domain = :dom,
                    ip_address = :ip, port = :port,
                    websocket = :ws, cache = :cache, rate_limit = :rl,
                    ssl_enabled = :ssl, extra_directives = :extra,
                    enabled = :en, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""),
                    {"mid": machine_id, "mn": machine_name, "hn": hostname,
                     "dom": domain, "ip": ip_address, "port": port,
                     "ws": int(websocket), "cache": int(cache), "rl": rate_limit,
                     "ssl": int(ssl_enabled), "extra": extra_directives,
                     "en": int(enabled)})
            return True

    def delete_proxy_mapping(self, machine_id: str) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM proxy_mappings WHERE machine_id = :mid"),
                             {"mid": machine_id})
            return r.rowcount > 0

    # --- Wiki sync pages ---

    def get_wiki_pages(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM wiki_pages ORDER BY entity_type, title")).fetchall()
            return [r._asdict() for r in rows]

    def get_wiki_page(self, entity_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM wiki_pages WHERE entity_id = :eid"),
                               {"eid": entity_id}).fetchone()
            return row._asdict() if row else None

    def upsert_wiki_page(self, entity_id: str, entity_type: str,
                         wiki_path: str, wiki_page_id: int,
                         title: str, content_hash: str) -> bool:
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            try:
                conn.execute(text("""INSERT INTO wiki_pages
                    (entity_id, entity_type, wiki_path, wiki_page_id, title, content_hash, last_synced)
                    VALUES (:eid, :et, :wp, :wpid, :t, :ch, :ts)"""),
                    {"eid": entity_id, "et": entity_type, "wp": wiki_path,
                     "wpid": wiki_page_id, "t": title, "ch": content_hash, "ts": local_now()})
            except IntegrityError:
                conn.execute(text("""UPDATE wiki_pages SET
                    entity_type = :et, wiki_path = :wp, wiki_page_id = :wpid,
                    title = :t, content_hash = :ch, last_synced = :ts,
                    updated_at = :ts
                    WHERE entity_id = :eid"""),
                    {"eid": entity_id, "et": entity_type, "wp": wiki_path,
                     "wpid": wiki_page_id, "t": title, "ch": content_hash, "ts": local_now()})
            return True

    def delete_wiki_page(self, entity_id: str) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM wiki_pages WHERE entity_id = :eid"),
                             {"eid": entity_id})
            return r.rowcount > 0

    def save_template(self, type_key: str, content: str) -> bool:
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO templates (type, content) VALUES (:k, :c) ON CONFLICT(type) DO UPDATE SET content = excluded.content"),
                         {"k": type_key, "c": content})
            return True

    def delete_template(self, type_key: str) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM templates WHERE type = :k"), {"k": type_key})
            return r.rowcount > 0

    def mark_proxy_deployed(self, machine_ids: List[str]):
        with self.engine.begin() as conn:
            for mid in machine_ids:
                conn.execute(text("""UPDATE proxy_mappings
                    SET last_deployed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE machine_id = :mid"""), {"mid": mid})

    # --- Drift detection (PVE2Drift) ---

    def upsert_managed_resource(self, resource_address: str, resource_type: str,
                                provider: str, matching_tag: str,
                                last_vmid: Optional[int], cluster_id: str = "",
                                node: Optional[str] = None,
                                declared_state: Optional[str] = None) -> int:
        """Insert or refresh a declared IaC resource. Returns its internal id."""
        from PVE2Services.libs.timeutil import local_now

        now = local_now()
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT id FROM managed_resource WHERE resource_address = :a"),
                               {"a": resource_address}).fetchone()
            if row:
                conn.execute(text("""UPDATE managed_resource SET
                    resource_type = :rt, provider = :prov, matching_tag = :tag,
                    last_vmid = :vmid, cluster_id = :cid, node = :node,
                    declared_state = :ds, last_ingested_at = :ts
                    WHERE id = :id"""),
                    {"rt": resource_type, "prov": provider, "tag": matching_tag,
                     "vmid": last_vmid, "cid": cluster_id, "node": node,
                     "ds": declared_state, "ts": now, "id": row[0]})
                return row[0]
            r = conn.execute(text("""INSERT INTO managed_resource
                (resource_address, resource_type, provider, matching_tag, last_vmid,
                 cluster_id, node, declared_state, last_ingested_at)
                VALUES (:a, :rt, :prov, :tag, :vmid, :cid, :node, :ds, :ts)"""),
                {"a": resource_address, "rt": resource_type, "prov": provider,
                 "tag": matching_tag, "vmid": last_vmid, "cid": cluster_id,
                 "node": node, "ds": declared_state, "ts": now})
            return r.lastrowid or 0

    def get_managed_resources(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM managed_resource ORDER BY resource_address ASC")).fetchall()
            return [r._asdict() for r in rows]

    def get_managed_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM managed_resource WHERE id = :id"),
                               {"id": resource_id}).fetchone()
            return row._asdict() if row else None

    def get_managed_resource_by_tag(self, matching_tag: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM managed_resource WHERE matching_tag = :tag"),
                               {"tag": matching_tag}).fetchone()
            return row._asdict() if row else None

    def delete_managed_resource(self, resource_id: int) -> bool:
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM drift_event WHERE resource_id = :rid"),
                         {"rid": resource_id})
            r = conn.execute(text("DELETE FROM managed_resource WHERE id = :id"),
                             {"id": resource_id})
            return r.rowcount > 0

    def touch_managed_resource_checked(self, resource_id: int):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(text("UPDATE managed_resource SET last_checked_at = :ts WHERE id = :id"),
                         {"ts": local_now(), "id": resource_id})

    def open_or_update_drift(self, resource_id: int, field: str,
                             declared_value: str, live_value: str,
                             category: str = "attribute_drift") -> bool:
        """Open a drift event, or refresh last_detected_at if already open.

        Returns True when the drift was newly opened (a state transition,
        the only moment a notification is worth emitting).
        """
        from PVE2Services.libs.timeutil import local_now

        now = local_now()
        with self.engine.begin() as conn:
            row = conn.execute(text("""SELECT id FROM drift_event
                WHERE resource_id = :rid AND field = :f AND status = 'open'"""),
                {"rid": resource_id, "f": field}).fetchone()
            if row:
                conn.execute(text("""UPDATE drift_event SET
                    declared_value = :dv, live_value = :lv,
                    last_detected_at = :ts, category = :cat WHERE id = :id"""),
                    {"dv": declared_value, "lv": live_value, "ts": now,
                     "cat": category, "id": row[0]})
                return False
            conn.execute(text("""INSERT INTO drift_event
                (resource_id, field, declared_value, live_value, status,
                 category, first_detected_at, last_detected_at)
                VALUES (:rid, :f, :dv, :lv, 'open', :cat, :ts, :ts)"""),
                {"rid": resource_id, "f": field, "dv": declared_value,
                 "lv": live_value, "cat": category, "ts": now})
            return True

    def resolve_drift(self, resource_id: int, field: str) -> bool:
        """Close an open drift event. True when a transition actually happened."""
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            r = conn.execute(text("""UPDATE drift_event SET status = 'resolved',
                resolved_at = :ts
                WHERE resource_id = :rid AND field = :f AND status = 'open'"""),
                {"ts": local_now(), "rid": resource_id, "f": field})
            return (r.rowcount or 0) > 0

    def get_open_drifts(self, resource_id: Optional[int] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM drift_event WHERE status = 'open'"
        params: Dict[str, Any] = {}
        if resource_id is not None:
            query += " AND resource_id = :rid"
            params["rid"] = resource_id
        with self.engine.connect() as conn:
            rows = conn.execute(text(query + " ORDER BY first_detected_at DESC"), params).fetchall()
            return [r._asdict() for r in rows]

    def get_drift_events(self, status: Optional[str] = None,
                         limit: int = 200) -> List[Dict[str, Any]]:
        query = """SELECT d.id, d.resource_id, d.field, d.declared_value,
            d.live_value, d.status, d.category, d.first_detected_at,
            d.last_detected_at, d.resolved_at,
            r.resource_address, r.matching_tag, r.last_vmid
            FROM drift_event d
            LEFT JOIN managed_resource r ON r.id = d.resource_id"""
        params: Dict[str, Any] = {}
        if status:
            query += " WHERE d.status = :st"
            params["st"] = status
        with self.engine.connect() as conn:
            rows = conn.execute(text(query + " ORDER BY d.id DESC LIMIT :lim"),
                                {**params, "lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def create_api_key(self, environment: str) -> str:
        """Generate a new drift ingestion API key. The plaintext is returned
        exactly once; only its sha256 hash is persisted."""
        import hashlib
        import secrets

        from PVE2Services.libs.timeutil import local_now

        plaintext = "pve2drift_" + secrets.token_hex(24)
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO drift_api_keys
                (environment, key_hash, created_at) VALUES (:env, :kh, :ts)"""),
                {"env": environment, "kh": key_hash, "ts": local_now()})
        return plaintext

    def verify_api_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Return the key row when the presented key is valid and not revoked."""
        import hashlib

        key_hash = hashlib.sha256(key.encode()).hexdigest()
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT id, environment FROM drift_api_keys
                WHERE key_hash = :kh AND revoked = 0"""), {"kh": key_hash}).fetchone()
            if not row:
                return None
        with self.engine.begin() as conn:
            from PVE2Services.libs.timeutil import local_now

            conn.execute(text("UPDATE drift_api_keys SET last_used_at = :ts WHERE id = :id"),
                         {"ts": local_now(), "id": row[0]})
        return {"id": row[0], "environment": row[1]}

    def list_api_keys(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT id, environment, created_at,
                last_used_at, revoked FROM drift_api_keys ORDER BY id DESC""")).fetchall()
            return [r._asdict() for r in rows]

    def revoke_api_key(self, key_id: int) -> bool:
        with self.engine.begin() as conn:
            r = conn.execute(text("UPDATE drift_api_keys SET revoked = 1 WHERE id = :id"),
                             {"id": key_id})
            return r.rowcount > 0

    # --- NUT (UPS) tables — PVE2NUT ---

    def save_nut_reading(self, ups_name: str, status: str = "",
                         battery_charge: Optional[float] = None,
                         battery_runtime: Optional[float] = None,
                         ups_load: Optional[float] = None,
                         realpower: Optional[float] = None,
                         apparent_power: Optional[float] = None,
                         input_voltage: Optional[float] = None,
                         output_voltage: Optional[float] = None,
                         battery_voltage: Optional[float] = None,
                         vars: Optional[Dict[str, Any]] = None):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO nut_history
                (ups_name, timestamp, status, battery_charge, battery_runtime, ups_load,
                 realpower, apparent_power, input_voltage, output_voltage, battery_voltage, vars_json)
                VALUES (:u, :ts, :st, :bc, :br, :ul, :rp, :ap, :iv, :ov, :bv, :vj)"""),
                {"u": ups_name, "ts": local_now(), "st": status,
                 "bc": battery_charge, "br": battery_runtime, "ul": ups_load,
                 "rp": realpower, "ap": apparent_power, "iv": input_voltage,
                 "ov": output_voltage, "bv": battery_voltage,
                 "vj": json.dumps(vars or {}, default=str)})

    def get_last_nut_reading(self, ups_name: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            # rowid only exists on SQLite; other dialects fall back to the PK
            if self.engine.dialect.name == "sqlite":
                order = "ORDER BY rowid DESC"
            else:
                order = "ORDER BY id DESC"
            row = conn.execute(text(f"""SELECT ups_name, timestamp, status, battery_charge,
                battery_runtime, ups_load, realpower, apparent_power, input_voltage,
                output_voltage, battery_voltage, vars_json
                FROM nut_history WHERE ups_name = :u {order} LIMIT 1"""),
                {"u": ups_name}).fetchone()
        if not row:
            return None
        d = row._asdict()
        try:
            d["vars"] = json.loads(d.pop("vars_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["vars"] = {}
        return d

    def get_nut_history(self, ups_name: str, hours: int = 24,
                        limit: int = 1000) -> List[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT ups_name, timestamp, status, battery_charge,
                battery_runtime, ups_load, realpower, apparent_power, input_voltage,
                output_voltage, battery_voltage
                FROM nut_history
                WHERE ups_name = :u AND timestamp >= :since
                ORDER BY timestamp ASC LIMIT :lim"""),
                {"u": ups_name, "since": since, "lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def get_nut_devices(self) -> List[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT ups_name FROM nut_history ORDER BY ups_name")).fetchall()
            return [r[0] for r in rows]

    def prune_nut_history(self, days: int = 30) -> int:
        """Delete UPS readings older than `days` days. Returns rows removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM nut_history WHERE timestamp < :c"),
                             {"c": cutoff})
            return r.rowcount or 0

    def save_nut_event(self, ups_name: str, event_type: str,
                       from_status: str = "", to_status: str = "",
                       detail: str = ""):
        from PVE2Services.libs.timeutil import local_now

        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO nut_events
                (ups_name, timestamp, event_type, from_status, to_status, detail)
                VALUES (:u, :ts, :et, :fs, :ts2, :d)"""),
                {"u": ups_name, "ts": local_now(), "et": event_type,
                 "fs": from_status, "ts2": to_status, "d": detail})

    def get_nut_events(self, ups_name: Optional[str] = None,
                       limit: int = 100) -> List[Dict[str, Any]]:
        query = """SELECT id, ups_name, timestamp, event_type, from_status, to_status, detail
                   FROM nut_events"""
        params: Dict[str, Any] = {}
        if ups_name:
            query += " WHERE ups_name = :u"
            params["u"] = ups_name
        with self.engine.connect() as conn:
            # rowid only exists on SQLite; other dialects fall back to the PK
            if self.engine.dialect.name == "sqlite":
                order = "ORDER BY rowid DESC"
            else:
                order = "ORDER BY id DESC"
            rows = conn.execute(text(query + f" {order} LIMIT :lim"),
                                {**params, "lim": limit}).fetchall()
            return [r._asdict() for r in rows]

    def prune_nut_events(self, days: int = 90) -> int:
        """Delete UPS events older than `days` days. Returns rows removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM nut_events WHERE timestamp < :c"),
                             {"c": cutoff})
            return r.rowcount or 0
