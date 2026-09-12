from PVE2Services.core.wiki_service.app.store import _store


class PageNotFoundError(Exception):
    """Raised when a wiki page is not found."""


class WikiAdapter:
    """Wiki provider backed by the in-memory page store.

    This is a lightweight replacement for the Wiki.js integration used in the
    original Go code. It supports CreatePage, UpdatePage and MovePage.
    """

    def create_page(self, path: str, content: str, title: str) -> None:
        _store.create(title or path, content)

    def update_page(self, path: str, content: str) -> None:
        page = _store.find_by_title(path)
        if page:
            _store.update(page.id, content=content)
            return
        raise PageNotFoundError(f"page not found: {path}")

    def move_page(self, from_path: str, to_path: str) -> None:
        page = _store.find_by_title(from_path)
        if page:
            _store.update(page.id, title=to_path)
            return
        raise PageNotFoundError(f"page not found: {from_path}")
