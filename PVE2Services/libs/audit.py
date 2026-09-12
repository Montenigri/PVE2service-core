"""Human-readable audit trail written to daily-rotated flat text files.

Format (verbatim, verbose)::

    [plugin] SEVERITY Title
      timestamp: YYYY-MM-DD HH:MM:SS
      detail line 1
      detail line 2
      [plugin] SEVERITY Nested operation   <- 2 extra spaces per nesting level

Rotation: one file per local day: ``audit-YYYY-MM-DD.txt``
Retention: files older than ``audit_retention_days`` (DB setting, default 30)
are purged lazily on first write of a new day and via ``/api/logs/trim``.
"""

import contextlib
import contextvars
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .timeutil import local_tz

current_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "audit_depth", default=0,
)

_VALID_SEVERITIES = frozenset({"INFO", "OK", "WARNING", "ERROR", "CRITICAL"})
_FILE_RE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.txt$")

# Secrets that must never appear in log content.
_SECRET_KEYS = frozenset({
    "password", "password_hash", "admin_password_hash", "token", "secret",
    "api_key", "ssh_key", "proxmox_token", "proxmox_secret", "wiki_token",
    "pihole_token", "adguard_password", "discord_webhook_url",
    "slack_webhook_url", "mattermost_webhook_url", "telegram_bot_token",
})


def _redact(value: object) -> str:
    """Mask a value that may be a secret."""
    s = str(value)
    if len(s) <= 4:
        return "****"
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def redact(value: object) -> str:
    """Public helper — mask a secret value for audit logging."""
    return _redact(value)


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _is_secret_key(key: str) -> bool:
    return key.lower().strip() in _SECRET_KEYS


class AuditLogger:
    """Thread-safe, daily-rotating audit logger.

    Use the module-level :data:`audit` singleton, or create an instance for
    testing with a custom *log_dir*.
    """

    def __init__(self, log_dir: str | Path | None = None):
        self._log_dir = Path(log_dir or os.getenv(
            "PVE2_LOG_DIR",
            Path(__file__).resolve().parents[2] / "logs",
        ))
        self._lock = threading.Lock()
        self._current_day: str | None = None
        self._handle = None

    # -- internal --------------------------------------------------------

    def _today(self) -> str:
        return datetime.now(local_tz()).strftime("%Y-%m-%d")

    def _open_handle(self, day: str):
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / f"audit-{day}.txt"
        self._handle = open(path, "a", encoding="utf-8")
        self._current_day = day

    def _ensure_handle(self):
        today = self._today()
        if self._current_day != today or self._handle is None:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
            self._open_handle(today)

    def _write_line(self, depth: int, text: str):
        indent = "  " * depth
        self._handle.write(f"{indent}{text}\n")
        self._handle.flush()

    def _purge_old(self, retention_days: int | None = None):
        """Delete audit files older than *retention_days* from today."""
        try:
            if retention_days is None:
                from .db_adapter import get_db
                raw = get_db().get_setting("audit_retention_days")
                retention_days = int(raw) if raw else 30
            else:
                retention_days = max(1, int(retention_days))
        except Exception:
            retention_days = 30
        today = datetime.now(local_tz()).date()
        cutoff_str = (today - timedelta(days=retention_days)).isoformat()
        for f in self._log_dir.iterdir():
            m = _FILE_RE.match(f.name)
            if m and m.group(1) < cutoff_str:
                try:
                    f.unlink()
                except OSError:
                    pass

    # -- public API ------------------------------------------------------

    def configure(self, log_dir: str | Path | None = None, retention_days: int | None = None):
        """Reconfigure the logger (for tests or runtime)."""
        if log_dir is not None:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None
                self._current_day = None
            self._log_dir = Path(log_dir)
        if retention_days is not None:
            try:
                from .db_adapter import get_db
                get_db().set_setting("audit_retention_days", str(retention_days))
            except Exception:
                pass

    def reset(self):
        """Close the current file handle (e.g. between tests)."""
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
            self._current_day = None

    def list_files(self) -> list[dict]:
        """Return metadata for every audit log file."""
        result = []
        if not self._log_dir.exists():
            return result
        for f in sorted(self._log_dir.iterdir(), reverse=True):
            m = _FILE_RE.match(f.name)
            if not m:
                continue
            size = f.stat().st_size
            lines = 0
            try:
                with open(f, encoding="utf-8") as fh:
                    lines = sum(1 for _ in fh)
            except OSError:
                pass
            result.append({
                "name": f.name,
                "date": m.group(1),
                "size_bytes": size,
                "lines": lines,
            })
        return result

    def latest_content(self) -> dict:
        """Return the content of today's log, or the most recent non-empty file."""
        today_file = self._log_dir / f"audit-{self._today()}.txt"
        target = today_file if today_file.exists() else None
        if target is None:
            for f in sorted(self._log_dir.iterdir(), reverse=True):
                if _FILE_RE.match(f.name) and f.stat().st_size > 0:
                    target = f
                    break
        if target is None:
            return {"filename": None, "date": self._today(), "content": ""}
        m = _FILE_RE.match(target.name)
        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            content = ""
        return {"filename": target.name, "date": m.group(1) if m else "", "content": content}

    def get_file_content(self, filename: str) -> str | None:
        """Return the content of a specific audit file, or None if invalid."""
        if not _FILE_RE.match(filename):
            return None
        path = self._log_dir / filename
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def log(
        self,
        plugin: str,
        severity: str,
        title: str,
        *,
        detail: str | None = None,
    ):
        """Write a single audit entry at the current nesting depth.

        If *detail* is provided it is written as an indented line below the
        header. Secret keys in the detail string are masked automatically.
        """
        sev = severity.upper()
        if sev not in _VALID_SEVERITIES:
            sev = "INFO"
        depth = current_depth.get()
        ts = datetime.now(local_tz()).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._ensure_handle()
            self._write_line(depth, f"[{plugin}] {sev} {title}")
            self._write_line(depth, f"  timestamp: {ts}")
            if detail:
                for line in str(detail).split("\n"):
                    safe = _mask_secrets_in_line(line)
                    self._write_line(depth, f"  {safe}")
            self._write_line(depth, "")  # blank separator

    @contextlib.contextmanager
    def operation(
        self,
        plugin: str,
        severity: str,
        title: str,
        *,
        detail: str | None = None,
    ):
        """Context manager that emits an audit entry and increases the nesting
        depth for entries written inside the block.

        Sub-operations written with :meth:`log` inside the block appear
        indented by two extra spaces per nesting level. Multiple nested
        operations at the same level are sequential.
        """
        sev = severity.upper()
        if sev not in _VALID_SEVERITIES:
            sev = "INFO"
        depth = current_depth.get()
        ts = datetime.now(local_tz()).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._ensure_handle()
            self._write_line(depth, f"[{plugin}] {sev} {title}")
            self._write_line(depth, f"  timestamp: {ts}")
            if detail:
                for line in str(detail).split("\n"):
                    safe = _mask_secrets_in_line(line)
                    self._write_line(depth, f"  {safe}")

        token = current_depth.set(depth + 1)
        try:
            yield
        finally:
            current_depth.reset(token)
            # Write the closing blank separator only if no other entry
            # already did (i.e. we are the innermost context that ran last).
            with self._lock:
                if current_depth.get() == depth:
                    self._ensure_handle()
                    self._write_line(depth, "")

    def purge(self, retention_days: int | None = None):
        """Purge old files now (also runs lazily on first write of a new day)."""
        with self._lock:
            self._purge_old(retention_days)


def _mask_secrets_in_line(line: str) -> str:
    """Mask any value that looks like it belongs to a secret key."""
    for key in _SECRET_KEYS:
        pattern = re.compile(
            rf'({re.escape(key)}\s*[=:]\s*)["\']?(\S+)["\']?',
            re.IGNORECASE,
        )

        def _replacer(m: re.Match, _key: str = key) -> str:
            prefix = m.group(1)
            return f"{prefix}{_redact(m.group(2))}"

        line = pattern.sub(_replacer, line)
    return line


audit = AuditLogger()
