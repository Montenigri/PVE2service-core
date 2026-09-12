import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("inspect_db")
# tools/ -> repo root -> legacy PVE2Wiki DB location.
DB = Path(__file__).resolve().parents[1] / "PVE2Wiki" / "pve2wiki.db"


def main():
    logger.info("Inspecting DB at: %s", DB)
    if not DB.exists():
        logger.error("DB file not found, aborting.")
        return
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name;")
    items = cur.fetchall()
    for name, typ, sql in items:
        logger.info("\n== %s %s", typ.upper(), name)
        logger.info(sql)
        try:
            cur.execute(f"PRAGMA table_info('{name}')")
            cols = cur.fetchall()
            for c in cols:
                logger.info("  col: %s", c)
        except Exception:
            logger.exception("  (no table info) for %s", name)
        try:
            cur.execute(f"SELECT * FROM '{name}' LIMIT 5")
            rows = cur.fetchall()
            logger.info("  rows:")
            for r in rows:
                logger.info("   %s", r)
        except Exception:
            logger.exception("  (no rows) for %s", name)

    con.close()


if __name__ == '__main__':
    logging.basicConfig()
    main()
