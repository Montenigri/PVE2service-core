"""Timezone-aware timestamp helpers.

PVE2_TIMEZONE (IANA name, e.g. "Europe/Rome") controls the local time used for
user-visible timestamps (changelog, histories, wiki page sync times).
Defaults to UTC. The SQL CURRENT_TIMESTAMP defaults stay UTC; the DB writes
below pass explicit timestamps instead.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("PVE2_TIMEZONE", "UTC") or "UTC")


def local_now() -> str:
    """Current time in the configured timezone as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(local_tz()).strftime("%Y-%m-%d %H:%M:%S")
