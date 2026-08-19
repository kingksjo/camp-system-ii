"""
Phase 4 (DB-09): background writers must not overwrite newer user state.

The kill-switch and schedule-lifecycle watchers SELECT open rows and then
UPDATE them. If a user signs off an event between the two statements, the
watcher's unconditional UPDATE would silently overwrite the newer status and
the audit log would claim an action that never happened. The watchers now use
conditional UPDATEs (WHERE status = expected), guard their audit-log inserts
with rowcount, and use INSERT OR IGNORE for idempotent markers/reminders.

These tests verify the guards deterministically and with two concurrent
scan threads (SQLite's single-writer serialization makes the "both scans see
the same open row" interleaving reliable).
"""
import sqlite3
import threading

import pytest

from app import migrations as migrations_module
from app.camp_extensions import kill_switch, schedule_lifecycle
from app.config import Config


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "camp_watchers.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(path))
    migrations_module.run_migrations()
    return path


def _seed_aircraft_and_events(path, n=3):
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('Aircraft_5N_TAJ')")
    for i in range(n):
        conn.execute(
            "INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time, status) "
            "VALUES ('Aircraft_5N_TAJ', 'Maintenance', ?, '2020-01-01 09:00:00', '2020-01-02 10:00:00', 'Scheduled')",
            (f"Engine overheat hangar slot {i}",),
        )
    conn.commit()
    conn.close()


def test_lifecycle_never_expires_signed_off_events(db_path):
    _seed_aircraft_and_events(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE Schedule SET status = 'Completed' WHERE rowid = 1"
    )
    conn.execute(
        "UPDATE Schedule SET status = 'Cancelled-KillSwitch' WHERE rowid = 2"
    )
    conn.commit()
    conn.close()

    expired = schedule_lifecycle.scan_and_expire_stale()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        statuses = [r["status"] for r in conn.execute("SELECT status FROM Schedule ORDER BY rowid")]
        assert statuses == ["Completed", "Cancelled-KillSwitch", "Expired-AutoRemoved"]

        logs = conn.execute("SELECT record_id FROM ScheduleLifecycleLog").fetchall()
        assert [r["record_id"] for r in logs] == [3]
    finally:
        conn.close()

    assert expired == [3]


def test_lifecycle_reminder_fires_once(db_path):
    _seed_aircraft_and_events(db_path)

    fired_first = schedule_lifecycle.scan_and_fire_reminders()
    fired_second = schedule_lifecycle.scan_and_fire_reminders()

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM ScheduleReminders").fetchone()[0]
    finally:
        conn.close()

    assert len(fired_first) == 3
    assert fired_second == []
    assert count == 3


def test_kill_switch_cancels_open_events_only(db_path):
    _seed_aircraft_and_events(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE Schedule SET status = 'Completed' WHERE rowid = 1")
    conn.execute(
        "INSERT INTO CRS_Records (aircraft_reg, reference_id, description) "
        "VALUES ('5N-TAJ', 'CRS-1', 'Engine overheat fixed')"
    )
    conn.commit()
    conn.close()

    actions = kill_switch.run_kill_switch_scan()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        statuses = [r["status"] for r in conn.execute("SELECT status FROM Schedule ORDER BY rowid")]
        assert statuses == ["Completed", "Cancelled-KillSwitch", "Cancelled-KillSwitch"]

        logs = conn.execute("SELECT target_record_id FROM KillSwitchLog").fetchall()
        assert sorted(r["target_record_id"] for r in logs) == ["2", "3"]

        processed = conn.execute("SELECT COUNT(*) FROM KillSwitchProcessedCRS").fetchone()[0]
        assert processed == 1
    finally:
        conn.close()

    assert len(actions) == 2


def test_kill_switch_concurrent_scans_log_each_action_once(db_path):
    _seed_aircraft_and_events(db_path, n=1)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO CRS_Records (aircraft_reg, reference_id, description) "
        "VALUES ('5N-TAJ', 'CRS-1', 'Engine overheat fixed')"
    )
    conn.commit()
    conn.close()

    results = []

    def _scan():
        results.append(kill_switch.run_kill_switch_scan())

    threads = [threading.Thread(target=_scan) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        status = conn.execute("SELECT status FROM Schedule WHERE rowid = 1").fetchone()["status"]
        assert status == "Cancelled-KillSwitch"

        log_count = conn.execute("SELECT COUNT(*) FROM KillSwitchLog").fetchone()[0]
        assert log_count == 1

        processed = conn.execute("SELECT COUNT(*) FROM KillSwitchProcessedCRS").fetchone()[0]
        assert processed == 1
    finally:
        conn.close()


def test_lifecycle_reminder_concurrent_inserts_no_error(db_path):
    _seed_aircraft_and_events(db_path, n=1)

    results = []

    def _scan():
        results.append(schedule_lifecycle.scan_and_fire_reminders())

    threads = [threading.Thread(target=_scan) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM ScheduleReminders").fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert sorted(len(r) for r in results) == [0, 1]