import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bcrypt
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import reset_admin_password as tool  # noqa: E402


class ResetAdminPasswordToolTest(unittest.TestCase):
    def setUp(self):
        self.old_url = os.environ.get("PVE2_DB_URL")
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["PVE2_DB_URL"] = f"sqlite:///{self.db_path}"

    def tearDown(self):
        if self.old_url is None:
            os.environ.pop("PVE2_DB_URL", None)
        else:
            os.environ["PVE2_DB_URL"] = self.old_url
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _db(self):
        from PVE2Services.libs.db_adapter import DBAdapter

        return DBAdapter()

    def test_set_writes_bcrypt_hash(self):
        rc = tool.main(["set", "--username", "admin2", "--password", "supersecret1"])
        self.assertEqual(rc, 0)
        db = self._db()
        stored = db.get_setting("admin_password_hash")
        user = db.get_setting("admin_user")
        self.assertEqual(user, "admin2")
        self.assertTrue(stored.startswith("$2b$"))
        self.assertTrue(bcrypt.checkpw(b"supersecret1", stored.encode()))

    def test_set_then_login_works_end_to_end(self):
        rc = tool.main(["set", "--password", "supersecret1"])
        self.assertEqual(rc, 0)
        from PVE2Services.core.wiki_service.app.main import app

        c = TestClient(app)
        r = c.post("/admin/login", json={"username": "admin", "password": "supersecret1"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_reset_clears_credentials_and_reenables_setup(self):
        tool.main(["set", "--username", "olduser", "--password", "supersecret1"])
        rc = tool.main(["reset", "--yes"])
        self.assertEqual(rc, 0)
        db = self._db()
        self.assertIsNone(db.get_setting("admin_password_hash"))
        self.assertIsNone(db.get_setting("admin_user"))

        from PVE2Services.core.wiki_service.app.main import app

        c = TestClient(app)
        r = c.post("/admin/setup", json={"username": "admin", "new_password": "brandnewpass1"})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post("/admin/login", json={"username": "admin", "password": "brandnewpass1"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_reset_aborts_without_confirmation(self):
        tool.main(["set", "--password", "supersecret1"])
        with patch("builtins.input", return_value="n"):
            rc = tool.main(["reset"])
        self.assertEqual(rc, 1)
        db = self._db()
        self.assertIsNotNone(db.get_setting("admin_password_hash"))

    def test_short_password_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            tool.main(["set", "--password", "short"])
        self.assertEqual(ctx.exception.code, 2)
        db = self._db()
        self.assertIsNone(db.get_setting("admin_password_hash"))

    def test_prompt_mismatch_rejected(self):
        with patch.object(tool.getpass, "getpass", side_effect=["pw12345678", "different1"]):
            with self.assertRaises(SystemExit) as ctx:
                tool.main(["set"])
        self.assertEqual(ctx.exception.code, 2)
        db = self._db()
        self.assertIsNone(db.get_setting("admin_password_hash"))

    def test_status_reports_states(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            tool.main(["status"])
        self.assertIn("NOT SET", buf.getvalue())

        tool.main(["set", "--password", "supersecret1"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            tool.main(["status"])
        self.assertIn("SET", buf.getvalue())
        self.assertIn("bcrypt", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
