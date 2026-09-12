"""DB-backed page store (survives restarts; was in-memory before)."""

from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy import text

from PVE2Services.libs.db_adapter import get_db

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None


class Page(BaseModel):
    id: int
    title: str
    content: str


class PageCreate(BaseModel):
    title: str
    content: str


def _schema_for(dialect: str) -> str:
    if dialect == "postgresql":
        return """
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT ''
)
"""
    return """
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT ''
)
"""


class PageStore:
    """Page store persisted in the `pages` DB table."""

    def __init__(self):
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
            with self._db.engine.begin() as conn:
                conn.execute(text(_schema_for(self._db.engine.dialect.name)))
        return self._db

    def create(self, title: str, content: str) -> Page:
        with self.db.engine.begin() as conn:
            if self.db.engine.dialect.name == "postgresql":
                pid = conn.execute(
                    text("INSERT INTO pages (title, content) VALUES (:t, :c) RETURNING id"),
                    {"t": title, "c": content},
                ).scalar_one()
            else:
                r = conn.execute(
                    text("INSERT INTO pages (title, content) VALUES (:t, :c)"),
                    {"t": title, "c": content},
                )
                pid = r.lastrowid
        if _audit:
            try:
                _audit.log("pages", "INFO", "Page created",
                           detail=f"page_id: {pid}\n"
                                  f"title: {title}")
            except Exception:
                pass
        return Page(id=pid, title=title, content=content)

    def get(self, page_id: int) -> Optional[Page]:
        with self.db.engine.connect() as conn:
            r = conn.execute(
                text("SELECT id, title, content FROM pages WHERE id = :i"),
                {"i": page_id},
            ).fetchone()
        return Page(id=r[0], title=r[1], content=r[2]) if r else None

    def list(self) -> List[Page]:
        with self.db.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, title, content FROM pages ORDER BY id")
            ).fetchall()
        return [Page(id=r[0], title=r[1], content=r[2]) for r in rows]

    def find_by_title(self, title: str) -> Optional[Page]:
        with self.db.engine.connect() as conn:
            r = conn.execute(
                text("SELECT id, title, content FROM pages WHERE title = :t OR title LIKE :p ORDER BY id LIMIT 1"),
                {"t": title, "p": f"%{title}%"},
            ).fetchone()
        return Page(id=r[0], title=r[1], content=r[2]) if r else None

    def update(self, page_id: int, title: Optional[str] = None, content: Optional[str] = None) -> Optional[Page]:
        current = self.get(page_id)
        if not current:
            return None
        new_title = title if title is not None else current.title
        new_content = content if content is not None else current.content
        with self.db.engine.begin() as conn:
            conn.execute(
                text("UPDATE pages SET title = :t, content = :c WHERE id = :i"),
                {"t": new_title, "c": new_content, "i": page_id},
            )
        if _audit:
            try:
                _audit.log("pages", "INFO", "Page updated",
                           detail=f"page_id: {page_id}")
            except Exception:
                pass
        return Page(id=page_id, title=new_title, content=new_content)

    def delete(self, page_id: int) -> bool:
        with self.db.engine.begin() as conn:
            r = conn.execute(text("DELETE FROM pages WHERE id = :i"), {"i": page_id})
        deleted = (r.rowcount or 0) > 0
        if deleted and _audit:
            try:
                _audit.log("pages", "INFO", "Page deleted",
                           detail=f"page_id: {page_id}")
            except Exception:
                pass
        return deleted


# Module-level singleton for backward compatibility
_store = PageStore()

create_page = _store.create
get_page = _store.get
list_pages = _store.list
