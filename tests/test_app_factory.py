"""
Phase 2C (DB-11): TestingConfig must use a real file-backed database.

Regression: ':memory:' gives EVERY sqlite3.connect() a separate, empty
database, so the migration connection never shares schema with request
connections - create_app('testing') produced an app whose routes failed
with "no such table". A temp file makes app factory, migrations, and all
connections operate on one database.
"""
import pytest

from app.config import Config, TestingConfig


@pytest.fixture
def shared_testing_db(tmp_path, monkeypatch):
    path = tmp_path / "camp_factory_test.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(TestingConfig, "DATABASE_PATH", str(path))
    return path


def test_testing_config_is_file_backed():
    assert TestingConfig.DATABASE_PATH != ":memory:"
    assert not TestingConfig.DATABASE_PATH.startswith("file:")
    assert TestingConfig.TESTING is True


def test_create_app_testing_shares_one_database(shared_testing_db):
    from app import create_app
    from app.camp_extensions import kill_switch, schedule_lifecycle
    from app.database import get_db_connection

    app = create_app('testing')
    try:
        assert app.config['TESTING'] is True

        conn = get_db_connection()
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0] == 9
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()

        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO Companies (company_id, company_name) VALUES (999, 'FactoryTest')")
            conn.commit()
        finally:
            conn.close()

        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT company_name FROM Companies WHERE company_id = 999"
            ).fetchone()
            assert row is not None and row["company_name"] == "FactoryTest"
        finally:
            conn.close()
    finally:
        kill_switch._stop_event.set()
        schedule_lifecycle._stop_event.set()


def test_testing_database_is_isolated_between_runs(tmp_path, monkeypatch):
    """A second app factory run must not inherit rows from a previous run."""
    from app import create_app
    from app.camp_extensions import kill_switch, schedule_lifecycle
    from app.database import get_db_connection

    second_db = tmp_path / "camp_factory_isolated.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(second_db))
    monkeypatch.setattr(TestingConfig, "DATABASE_PATH", str(second_db))

    create_app('testing')
    try:
        conn = get_db_connection()
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM Companies WHERE company_id = 999"
            ).fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        kill_switch._stop_event.set()
        schedule_lifecycle._stop_event.set()
