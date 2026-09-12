"""Tests for the configurable core sync schedule (persistence + live reschedule)."""

import asyncio
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class SchedulerConfigTest(unittest.TestCase):
    def setUp(self):
        self._old_url = os.environ.get("PVE2_DB_URL")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["PVE2_DB_URL"] = f"sqlite:///{self.db_path}"

        from PVE2Services.core.wiki_service.app.main import app

        self.client = TestClient(app)
        self.client.post("/admin/setup", json={"username": "admin", "new_password": "pve2admin"})
        r = self.client.post("/admin/login", json={"username": "admin", "password": "pve2admin"})
        assert r.status_code == 200, r.text

    def tearDown(self):
        from PVE2Services.libs.scheduler import stop_scheduler

        stop_scheduler()
        if self._old_url:
            os.environ["PVE2_DB_URL"] = self._old_url
        else:
            os.environ.pop("PVE2_DB_URL", None)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _status(self):
        r = self.client.get("/admin/scheduler/status")
        assert r.status_code == 200
        return r.json()

    def test_status_exposes_defaults(self):
        st = self._status()
        assert st["interval_seconds"] == 300
        assert st["enabled"] is False

    def test_interval_persisted_while_stopped(self):
        r = self.client.post("/admin/scheduler/interval?interval_seconds=600")
        assert r.status_code == 200
        body = r.json()
        assert body["interval_seconds"] == 600
        assert body["running"] is False

        st = self._status()
        assert st["interval_seconds"] == 600
        assert st["running"] is False

    def test_invalid_interval_rejected(self):
        r = self.client.post("/admin/scheduler/interval?interval_seconds=5")
        assert "error" in r.json()
        st = self._status()
        assert st["interval_seconds"] == 300  # unchanged

    def test_start_stop_persist_state_and_apply_live(self):
        r = self.client.post("/admin/scheduler/start?interval_seconds=120").json()
        assert r["running"] is True
        assert r["enabled"] is True

        st = self._status()
        assert st["running"] is True
        assert st["enabled"] is True
        assert st["interval_seconds"] == 120

        # Change cadence while running: applies live, stays enabled
        r2 = self.client.post("/admin/scheduler/interval?interval_seconds=60").json()
        assert r2["running"] is True
        st2 = self._status()
        assert st2["interval_seconds"] == 60
        assert st2["running"] is True

        # Stop: job removed AND persisted as disabled
        r3 = self.client.post("/admin/scheduler/stop").json()
        assert r3["stopped"] is True
        st3 = self._status()
        assert st3["running"] is False
        assert st3["enabled"] is False

    def test_autostart_restores_schedule_after_restart(self):
        self.client.post("/admin/scheduler/start?interval_seconds=45")

        # Simulate a process restart: scheduler state is gone, DB setting remains
        from PVE2Services.libs.scheduler import scheduler_status, stop_scheduler

        stop_scheduler()
        raw = scheduler_status()
        assert "pve2_sync" not in [j["id"] for j in raw["jobs"]]

        from PVE2Services.core.wiki_service.app.main import restore_sync_schedule

        asyncio.run(restore_sync_schedule())

        st = self._status()
        assert st["running"] is True
        assert st["interval_seconds"] == 45


if __name__ == "__main__":
    unittest.main()
