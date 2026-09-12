import logging
import threading
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

_scheduler: Optional[BackgroundScheduler] = None
_lock = threading.Lock()
logger = logging.getLogger("pve2.scheduler")


def _ensure() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        logger.info("Scheduler started")
    return _scheduler


def add_job(name: str, func: Callable, trigger: str = "interval", seconds: int = 300) -> bool:
    with _lock:
        sched = _ensure()
        if sched.get_job(name):
            return False

        def _leader_wrapped():
            # Cluster safety: with multiple replicas, only the instance that
            # holds the DB lease runs the job. The lease TTL equals the job
            # interval, so if the leader dies another replica takes over on
            # the next tick.
            from PVE2Services.libs.distributed import try_lock

            if not try_lock(f"job:{name}", ttl_seconds=seconds):
                logger.debug("Job %s skipped — lease held by another instance", name)
                return
            func()

        sched.add_job(_leader_wrapped, trigger, seconds=seconds, id=name)
        if _audit:
            try:
                _audit.log("scheduler", "INFO", "Job added",
                           detail=f"name: {name}\n"
                                  f"interval_seconds: {seconds}")
            except Exception:
                pass
        logger.info("Job %s added (every %ds)", name, seconds)
        return True


def remove_job(name: str) -> bool:
    with _lock:
        global _scheduler
        if _scheduler and _scheduler.get_job(name):
            _scheduler.remove_job(name)
            from PVE2Services.libs.distributed import release_lock

            release_lock(f"job:{name}")
            if _audit:
                try:
                    _audit.log("scheduler", "INFO", "Job removed",
                               detail=f"name: {name}")
                except Exception:
                    pass
            logger.info("Job %s removed", name)
            return True
        return False


def start_scheduler(func: Callable, trigger: str = "interval", seconds: int = 300) -> bool:
    return add_job("pve2_sync", func, trigger, seconds)


def reschedule_job(name: str, func: Callable, trigger: str = "interval", seconds: int = 300) -> bool:
    """Replace a running job in place (the lease TTL follows the new interval)."""
    remove_job(name)
    return add_job(name, func, trigger, seconds)


def stop_scheduler() -> bool:
    return remove_job("pve2_sync")


def scheduler_status() -> dict:
    if _scheduler and _scheduler.running:
        jobs = [
            {
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            }
            for job in _scheduler.get_jobs()
        ]
        return {"running": True, "jobs": jobs}
    return {"running": False, "jobs": []}
