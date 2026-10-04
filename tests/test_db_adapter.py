"""Tests for DBAdapter — database operations and schema."""

import os

import pytest

from PVE2Services.libs.db_adapter import DBAdapter, resolve_db_url


class TestResolveDbUrl:
    @pytest.fixture(autouse=True)
    def restore_env(self):
        """Save/restore PVE2_DB_* env vars — resolve tests mutate them."""
        keys = ("PVE2_DB_URL", "PVE2_DB_MODE", "PVE2_DB_USER", "PVE2_DB_PASS", "PVE2_DB_HOST", "PVE2_DB_NAME", "PVE2_DB_PATH")
        saved = {k: os.environ.get(k) for k in keys}
        yield
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_sqlite(self):
        os.environ.pop("PVE2_DB_URL", None)
        os.environ.pop("PVE2_DB_MODE", None)
        os.environ.pop("PVE2_DB_PATH", None)
        url = resolve_db_url()
        assert url.startswith("sqlite:///")

    def test_explicit_url(self):
        os.environ["PVE2_DB_URL"] = "sqlite:///custom.db"
        url = resolve_db_url()
        assert url == "sqlite:///custom.db"

    def test_explicit_postgres_url_pins_psycopg2(self):
        """Bare postgres URLs must use the declared psycopg2 driver.

        SQLAlchemy 2.1 defaults ``postgresql://`` to psycopg v3; the project
        depends on psycopg2-binary, so the driver is pinned explicitly.
        """
        os.environ["PVE2_DB_URL"] = "postgresql://u:p@localhost:5432/db"
        assert resolve_db_url() == "postgresql+psycopg2://u:p@localhost:5432/db"

    def test_explicit_postgres_psycopg3_url_untouched(self):
        os.environ["PVE2_DB_URL"] = "postgresql+psycopg://u:p@localhost:5432/db"
        assert resolve_db_url() == "postgresql+psycopg://u:p@localhost:5432/db"

    def test_postgres_mode(self):
        os.environ.pop("PVE2_DB_URL", None)
        os.environ["PVE2_DB_MODE"] = "postgres"
        os.environ["PVE2_DB_USER"] = "testuser"
        os.environ["PVE2_DB_PASS"] = "testpass"
        os.environ["PVE2_DB_HOST"] = "localhost"
        os.environ["PVE2_DB_NAME"] = "testdb"
        url = resolve_db_url()
        assert "postgresql+psycopg2://testuser:testpass@localhost:5432/testdb" == url

    def test_explicit_path(self):
        os.environ.pop("PVE2_DB_URL", None)
        os.environ.pop("PVE2_DB_MODE", None)
        os.environ["PVE2_DB_PATH"] = "/tmp/test.db"
        url = resolve_db_url()
        assert url == "sqlite:////tmp/test.db"


class TestDBAdapter:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        old_url = os.environ.get("PVE2_DB_URL")
        self.db_path = str(tmp_path / "test.db")
        os.environ["PVE2_DB_URL"] = f"sqlite:///{self.db_path}"
        self.db = DBAdapter(db_url=f"sqlite:///{self.db_path}")
        yield
        if old_url:
            os.environ["PVE2_DB_URL"] = old_url
        else:
            os.environ.pop("PVE2_DB_URL", None)
        # Nothing should have bound the shared singleton to this tmp DB, but
        # reset it so a leak cannot poison later tests.
        import PVE2Services.libs.db_adapter as _dba

        _dba._db_instance = None

    def test_settings_crud(self):
        self.db.set_setting("test_key", "test_value")
        assert self.db.get_setting("test_key") == "test_value"
        self.db.set_setting("test_key", "updated")
        assert self.db.get_setting("test_key") == "updated"
        assert self.db.get_setting("nonexistent") is None

    def test_get_all_settings(self):
        self.db.set_setting("a", "1")
        self.db.set_setting("b", "2")
        settings = self.db.get_all_settings()
        assert settings["a"] == "1"
        assert settings["b"] == "2"

    def test_sync_state_crud(self):
        state = {"id": "vm/100", "type": "vm", "hash": "abc", "wiki_path": "/test", "archived": False, "entity_data": {"name": "test"}}
        self.db.update_sync_state(state)
        states = self.db.get_all_sync_states()
        assert len(states) == 1
        assert states[0]["id"] == "vm/100"

    def test_changelog(self):
        self.db.save_changelog("created", "vm", "vm/100", "Test VM", "Created test VM")
        changelog = self.db.get_changelog()
        assert len(changelog) == 1
        assert changelog[0]["action"] == "created"

    def test_templates(self):
        self.db.save_template("vm", "# {{ name }}")
        assert self.db.get_template("vm") == "# {{ name }}"
        templates = self.db.get_templates()
        assert "vm" in templates

    def test_delete_template(self):
        self.db.save_template("temp", "content")
        assert self.db.delete_template("temp") is True
        assert self.db.get_template("temp") is None
        assert self.db.delete_template("nonexistent") is False

    def test_storage_history(self):
        self.db.engine.begin()
        with self.db.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("""INSERT INTO storage_history (node, storage_name, total_bytes, used_bytes, content_types)
                VALUES ('node1', 'local', 1000000, 500000, 'images')"""))
        history = self.db.get_storage_history()
        assert len(history) == 1
        assert history[0]["storage_name"] == "local"

    def test_health_history(self):
        with self.db.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("""INSERT INTO health_history (component, item_id, status, details)
                VALUES ('cpu', 'cpu0', 'ok', 'normal')"""))
        history = self.db.get_health_history()
        assert len(history) == 1
        assert history[0]["component"] == "cpu"

    def test_dns_records_crud(self):
        assert self.db.save_dns_record("vm/100", "Test VM", "test.example.com", "10.0.0.1") is True
        record = self.db.get_dns_record("vm/100")
        assert record is not None
        assert record["hostname"] == "test.example.com"
        
        records = self.db.get_dns_records()
        assert len(records) == 1
        
        assert self.db.delete_dns_record("vm/100") is True
        assert self.db.get_dns_record("vm/100") is None

    def test_dns_record_update(self):
        self.db.save_dns_record("vm/100", "Test VM", "old.example.com", "10.0.0.1")
        self.db.save_dns_record("vm/100", "Test VM Updated", "new.example.com", "10.0.0.2")
        record = self.db.get_dns_record("vm/100")
        assert record["hostname"] == "new.example.com"
        assert record["machine_name"] == "Test VM Updated"

    def test_power_schedules_crud(self):
        assert self.db.save_power_schedule("vm/100", "Test VM") is True
        schedule = self.db.get_power_schedule("vm/100")
        assert schedule is not None
        assert schedule["weekday_start_time"] == "08:00"
        
        schedules = self.db.get_power_schedules()
        assert len(schedules) == 1
        
        assert self.db.delete_power_schedule("vm/100") is True
        assert self.db.get_power_schedule("vm/100") is None

    def test_proxy_mappings_crud(self):
        assert self.db.save_proxy_mapping("vm/100", "Test VM", "test", "example.com", "10.0.0.1") is True
        mapping = self.db.get_proxy_mapping("vm/100")
        assert mapping is not None
        assert mapping["domain"] == "example.com"
        
        mappings = self.db.get_proxy_mappings()
        assert len(mappings) == 1
        
        assert self.db.delete_proxy_mapping("vm/100") is True
        assert self.db.get_proxy_mapping("vm/100") is None

    def test_wiki_pages_crud(self):
        assert self.db.upsert_wiki_page("vm/100", "vm", "/wiki/test", 123, "Test VM", "hash123") is True
        page = self.db.get_wiki_page("vm/100")
        assert page is not None
        assert page["title"] == "Test VM"
        
        pages = self.db.get_wiki_pages()
        assert len(pages) == 1
        
        assert self.db.delete_wiki_page("vm/100") is True
        assert self.db.get_wiki_page("vm/100") is None

    def test_resource_history(self):
        self.db.insert_resource_history("vm/100", "node1", "vm", 0.5, 0.8, 1024, 2048, 500, 1000, 100, 200)
        history = self.db.get_resource_history("vm/100")
        assert len(history) == 1
        assert history[0]["cpu_avg"] == 0.5

    def test_recommendations(self):
        rec_id = self.db.save_recommendation("vm/100", "Test VM", "vm", "node1", "rightsizing", "warning", "4 CPU", "2 CPU", "Low usage")
        assert rec_id > 0
        
        recs = self.db.get_recommendations()
        assert len(recs) == 1
        
        assert self.db.dismiss_recommendation(rec_id) is True
        recs = self.db.get_recommendations(active_only=True)
        assert len(recs) == 0

    def test_mark_proxy_deployed(self):
        self.db.save_proxy_mapping("vm/100", "Test VM")
        self.db.save_proxy_mapping("vm/200", "Test VM 2")
        self.db.mark_proxy_deployed(["vm/100"])
        mapping = self.db.get_proxy_mapping("vm/100")
        assert mapping["last_deployed"] is not None

    def test_bulk_save_dns_records(self):
        records = [
            {"machine_id": "vm/100", "machine_name": "VM1", "hostname": "vm1.example.com", "ip_address": "10.0.0.1"},
            {"machine_id": "vm/200", "machine_name": "VM2", "hostname": "vm2.example.com", "ip_address": "10.0.0.2"},
        ]
        saved = self.db.bulk_save_dns_records(records)
        assert saved == 2
        assert len(self.db.get_dns_records()) == 2
