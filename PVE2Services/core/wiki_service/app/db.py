
from typing import Any, Dict, List

from PVE2Services.libs.db_adapter import DBAdapter

_db = DBAdapter()


def get_settings() -> Dict[str, str]:
    return _db.get_all_settings()


def get_storage_history(limit: int = 100) -> List[Dict[str, Any]]:
    return _db.get_storage_history(limit)


def get_health_history(limit: int = 100) -> List[Dict[str, Any]]:
    return _db.get_health_history(limit)


def get_sync_state(limit: int = 100) -> List[Dict[str, Any]]:
    return _db.get_sync_state(limit)


def get_changelog(limit: int = 100) -> List[Dict[str, Any]]:
    return _db.get_changelog(limit)


def get_templates() -> Dict[str, str]:
    return _db.get_templates()
