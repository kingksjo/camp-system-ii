import sqlite3

import pytest

from app import migrations as migrations_module
from app.config import Config
from app.database import get_db_connection


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "camp_test.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(path))
    return path


def _check_fk(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        return conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def _table_names(path):
    conn = sqlite3.connect(path)
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        conn.close()


def test_fresh_database_migrates_fully(db_path):
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    try:
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    finally:
        conn.close()
    assert versions == {1, 2, 3, 4, 5, 6, 7, 8}

    tables = _table_names(str(db_path))
    for required in (
        "Aircraft", "Components", "SensorTelemetry", "Faults", "Schedule",
        "Companies", "Users", "IngestedDocuments", "DigitalEvidence",
        "MaintenanceDocuments", "FKRepairAudit", "schema_migrations",
    ):
        assert required in tables

    assert _check_fk(str(db_path)) == []


def test_run_migrations_is_idempotent(db_path):
    migrations_module.run_migrations()
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()
    assert count == 8


def test_upgrade_repairs_known_orphans(db_path, monkeypatch):
    full_migrations = migrations_module.MIGRATIONS
    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations[:4])
    migrations_module.run_migrations()

    raw = sqlite3.connect(str(db_path))
    raw.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
    raw.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
    raw.execute("INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES ('C1', 'T', 1.0)")
    raw.execute(
        "INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference) "
        "VALUES ('Ghost_Component_X', 'TestFault', 'High', 0, 'x')"
    )
    raw.execute(
        "INSERT INTO Faults (component_id, telemetry_id, fault_type, severity, resolved) "
        "VALUES ('C1', 999999, 'TestFault', 'High', 0)"
    )
    raw.execute(
        "INSERT INTO Schedule (aircraft_id, event_type, title, status) "
        "VALUES ('Gone_Aircraft', 'Test', 'orphan event', 'Scheduled')"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations)
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ghost = conn.execute(
            "SELECT component_id FROM Faults WHERE component_id = 'Ghost_Component_X'"
        ).fetchall()
        assert ghost == []

        nulled = conn.execute(
            "SELECT component_id, telemetry_id FROM Faults "
            "WHERE fault_type = 'TestFault' ORDER BY fault_id"
        ).fetchall()
        assert [tuple(r) for r in nulled] == [(None, None), ('C1', None)]

        schedule_rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM Schedule WHERE aircraft_id = 'Gone_Aircraft'"
        ).fetchone()["cnt"]
        assert schedule_rows == 0

        repairs = conn.execute(
            "SELECT repaired_table, repaired_column, old_value FROM FKRepairAudit ORDER BY repair_id"
        ).fetchall()
        assert [tuple(r) for r in repairs[:2]] == [
            ('Faults', 'component_id', 'Ghost_Component_X'),
            ('Faults', 'telemetry_id', '999999'),
        ]
        assert repairs[2]["repaired_table"] == 'Schedule'
        assert repairs[2]["repaired_column"] == 'aircraft_id'
        assert 'Gone_Aircraft' in repairs[2]["old_value"]
        assert 'orphan event' in repairs[2]["old_value"]
    finally:
        conn.close()

    assert _check_fk(str(db_path)) == []

    versions = {
        r[0]
        for r in sqlite3.connect(str(db_path)).execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }
    assert versions == {1, 2, 3, 4, 5, 6, 7, 8}


def test_fresh_database_declares_missing_foreign_keys(db_path):
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    try:
        schemas = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "REFERENCES Aircraft(aircraft_id) ON DELETE RESTRICT" in schemas["Schedule"]
    assert "REFERENCES Aircraft(aircraft_id) ON DELETE RESTRICT" in schemas["MEL_Deferrals"]
    assert "REFERENCES MasterMEL(mmel_id) ON DELETE SET NULL" in schemas["MEL_Deferrals"]
    assert "REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL" in schemas["DigitalEvidence"]
    assert "REFERENCES Faults(fault_id) ON DELETE SET NULL" in schemas["DigitalEvidence"]
    assert "REFERENCES Components(component_id) ON DELETE SET NULL" in schemas["DigitalEvidence"]
    assert "REFERENCES Components(component_id) ON DELETE SET NULL" in schemas["PartRecords"]
    assert "REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL" in schemas["PartRecords"]
    assert "REFERENCES PartRecords(part_serial) ON DELETE SET NULL" in schemas["PartRecords"]
    assert "REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL" in schemas["EnvironmentalRiskLog"]
    assert "REFERENCES Components(component_id) ON DELETE SET NULL" in schemas["EnvironmentalRiskLog"]
    assert "REFERENCES Components(component_id) ON DELETE SET NULL" in schemas["CAMSISGroundingLog"]
    assert "REFERENCES CAMSISLimits(limit_id) ON DELETE SET NULL" in schemas["CAMSISGroundingLog"]
    assert "REFERENCES ToolCrib(tool_id) ON DELETE SET NULL" in schemas["IoTToolReadings"]
    assert "REFERENCES MaintenanceTasks(task_id) ON DELETE SET NULL" in schemas["IoTToolReadings"]
    assert "REFERENCES Components(component_id) ON DELETE SET NULL" in schemas["IoTToolReadings"]
    assert "REFERENCES TorqueSpecs(spec_id) ON DELETE SET NULL" in schemas["IoTToolReadings"]


def test_invalid_child_inserts_fail(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time) "
                "VALUES ('No_Such_Aircraft', 'Test', 'x', '2026-01-01 09:00:00', '2026-01-01 10:00:00')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO MEL_Deferrals (aircraft_id, item_description) "
                "VALUES ('No_Such_Aircraft', 'x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO MEL_Deferrals (aircraft_id, item_description, mmel_id) "
                "VALUES ('A1', 'x', 99999)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO DigitalEvidence (evidence_id, aircraft_id) "
                "VALUES ('ev1', 'No_Such_Aircraft')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO IoTToolReadings (reading_id, tool_id) "
                "VALUES ('r1', 'No_Such_Tool')"
            )
    finally:
        conn.close()


def test_aircraft_deletion_blocked_by_schedule_and_mel(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        conn.execute(
            "INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time) "
            "VALUES ('A1', 'Maintenance', 'x', '2026-01-01 09:00:00', '2026-01-01 10:00:00')"
        )
        conn.execute(
            "INSERT INTO MEL_Deferrals (aircraft_id, item_description) VALUES ('A1', 'y')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM Aircraft WHERE aircraft_id = 'A1'")
    finally:
        conn.close()


def test_set_null_policies_preserve_audit_rows(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        conn.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
            "VALUES ('C1', 'TestFault', 'High', 0)"
        )
        conn.execute(
            "INSERT INTO DigitalEvidence (evidence_id, aircraft_id, fault_id, component_id) "
            "VALUES ('ev1', 'A1', 1, 'C1')"
        )
        conn.execute(
            "INSERT INTO PartRecords (part_serial, component_id, aircraft_id) "
            "VALUES ('PN1', 'C1', 'A1')"
        )
        conn.execute(
            "INSERT INTO EnvironmentalRiskLog (aircraft_id, component_id, stressor) "
            "VALUES ('A1', 'C1', 'Corrosion')"
        )
        conn.execute(
            "INSERT INTO CAMSISGroundingLog (component_id, limit_category, used_value, limit_value, status) "
            "VALUES ('C1', 'Hours', 100.0, 3000.0, 'OK')"
        )
        conn.execute(
            "INSERT INTO IoTToolReadings (reading_id, component_id, torque_value) "
            "VALUES ('r1', 'C1', 10.0)"
        )

        conn.execute("DELETE FROM Faults WHERE fault_id = 1")
        conn.execute("DELETE FROM Components WHERE component_id = 'C1'")
        conn.execute("DELETE FROM Aircraft WHERE aircraft_id = 'A1'")

        evidence = conn.execute(
            "SELECT aircraft_id, fault_id, component_id FROM DigitalEvidence WHERE evidence_id = 'ev1'"
        ).fetchone()
        assert tuple(evidence) == (None, None, None)

        part = conn.execute(
            "SELECT component_id, aircraft_id FROM PartRecords WHERE part_serial = 'PN1'"
        ).fetchone()
        assert tuple(part) == (None, None)

        env = conn.execute(
            "SELECT aircraft_id, component_id FROM EnvironmentalRiskLog WHERE id = 1"
        ).fetchone()
        assert tuple(env) == (None, None)

        camsis = conn.execute(
            "SELECT component_id FROM CAMSISGroundingLog WHERE id = 1"
        ).fetchone()
        assert tuple(camsis) == (None,)

        iot = conn.execute(
            "SELECT component_id FROM IoTToolReadings WHERE reading_id = 'r1'"
        ).fetchone()
        assert tuple(iot) == (None,)
    finally:
        conn.close()

    assert _check_fk(str(db_path)) == []


def test_upgrade_to_006_preserves_valid_rows(db_path, monkeypatch):
    full_migrations = migrations_module.MIGRATIONS
    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations[:5])
    migrations_module.run_migrations()

    raw = sqlite3.connect(str(db_path))
    raw.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
    raw.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
    raw.execute(
        "INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time) "
        "VALUES ('A1', 'Maintenance', 'x', '2026-01-01 09:00:00', '2026-01-01 10:00:00')"
    )
    raw.execute(
        "INSERT INTO MEL_Deferrals (aircraft_id, item_description) VALUES ('A1', 'y')"
    )
    raw.execute(
        "INSERT INTO CAMSISGroundingLog (component_id, limit_category, used_value, limit_value, status) "
        "VALUES ('C1', 'Hours', 100.0, 3000.0, 'OK')"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations)
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) AS cnt FROM Schedule").fetchone()["cnt"] == 1
        assert conn.execute("SELECT COUNT(*) AS cnt FROM MEL_Deferrals").fetchone()["cnt"] == 1
        assert conn.execute("SELECT COUNT(*) AS cnt FROM CAMSISGroundingLog").fetchone()["cnt"] == 1
        assert conn.execute("SELECT COUNT(*) AS cnt FROM Aircraft").fetchone()["cnt"] == 1
        assert conn.execute("SELECT COUNT(*) AS cnt FROM Components").fetchone()["cnt"] == 1
    finally:
        conn.close()

    assert _check_fk(str(db_path)) == []


def test_app_connection_enforces_foreign_keys(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        conn.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
                "VALUES ('Missing_Component', 'TestFault', 'High', 0)"
            )
    finally:
        conn.close()


def _index_names(path):
    conn = sqlite3.connect(path)
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx\\_%' ESCAPE '\\'"
            ).fetchall()
        }
    finally:
        conn.close()


EXPECTED_INDEXES = {
    "idx_schedule_start_time",
    "idx_schedule_aircraft_status",
    "idx_telemetry_component_sensor_recorded",
    "idx_faults_component_resolved",
    "idx_evidence_fault_chain",
    "idx_parts_component_status",
    "idx_mel_aircraft_status",
    "idx_pilotreports_status",
    "idx_maintdocs_generated",
    "idx_components_aircraft",
    "idx_faults_open_component_type",
    "idx_ingested_documents_doc_id",
    "idx_maintdocs_source_unique",
    "idx_evidence_chain_position",
}


def test_migration_007_declares_query_indexes(db_path):
    migrations_module.run_migrations()

    assert EXPECTED_INDEXES <= _index_names(str(db_path))


def test_query_plans_use_indexes_after_007(db_path):
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    try:
        plans = {
            r[3]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT rowid FROM Schedule WHERE aircraft_id = 'x'"
            ).fetchall()
        }
        assert any("idx_schedule_aircraft_status" in p for p in plans), plans

        plans = {
            r[3]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM Faults "
                "WHERE component_id = 'x' AND resolved = 0"
            ).fetchall()
        }
        assert any("idx_faults_component_resolved" in p for p in plans), plans

        plans = {
            r[3]
            for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT reading_value FROM SensorTelemetry "
                "WHERE component_id = 'x' AND sensor_type = 'T' "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchall()
        }
        assert any("idx_telemetry_component_sensor_recorded" in p for p in plans), plans
    finally:
        conn.close()


def test_migration_007_enables_wal_journal_mode(db_path):
    migrations_module.run_migrations()

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_connections_apply_busy_timeout(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == Config.DB_BUSY_TIMEOUT_MS
    finally:
        conn.close()


def _unique_index_names(path):
    conn = sqlite3.connect(path)
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND sql LIKE 'CREATE UNIQUE INDEX%'"
            ).fetchall()
        }
    finally:
        conn.close()


def test_migration_008_declares_uniqueness_indexes(db_path):
    migrations_module.run_migrations()

    assert {
        "idx_faults_open_component_type",
        "idx_ingested_documents_doc_id",
        "idx_maintdocs_source_unique",
        "idx_evidence_chain_position",
    } <= _unique_index_names(str(db_path))

    redundant = _index_names(str(db_path)) & {"idx_maintdocs_source", "idx_evidence_aircraft_chain"}
    assert redundant == set()


def test_migration_008_blocks_duplicate_open_faults(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        conn.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
            "VALUES ('C1', 'Overheat', 'High', 0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
                "VALUES ('C1', 'Overheat', 'High', 0)"
            )
        # A resolved fault may legitimately be detected again later...
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
            "VALUES ('C1', 'Overheat', 'High', 1)"
        )
        # ...and NULL-component open faults never collide (SQLite NULLs are distinct).
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
            "VALUES (NULL, 'Overheat', 'High', 0)"
        )
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
            "VALUES (NULL, 'Overheat', 'High', 0)"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_008_ingested_documents_doc_id_unique(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO AircraftDocuments (doc_id, aircraft_id, company_id, doc_label, doc_type, file_path) "
            "VALUES (10, 'A1', 1, 'x', 'y', 'z')"
        )
        conn.execute(
            "INSERT INTO IngestedDocuments (doc_id, company_id, manual_type, tier) "
            "VALUES (10, 1, 'MMEL', 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO IngestedDocuments (doc_id, company_id, manual_type, tier) "
                "VALUES (10, 1, 'MMEL', 1)"
            )
        conn.commit()
    finally:
        conn.close()


def test_migration_008_maintenance_documents_source_unique(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO MaintenanceDocuments (document_id, source_type, source_id, file_path) "
            "VALUES ('d1', 'crs', '1', 'p1')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO MaintenanceDocuments (document_id, source_type, source_id, file_path) "
                "VALUES ('d2', 'crs', '1', 'p2')"
            )
        # The same source_type under a different source_id is fine.
        conn.execute(
            "INSERT INTO MaintenanceDocuments (document_id, source_type, source_id, file_path) "
            "VALUES ('d3', 'crs', '2', 'p3')"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_008_evidence_chain_position_unique(db_path):
    migrations_module.run_migrations()

    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
        conn.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A2')")
        conn.execute(
            "INSERT INTO DigitalEvidence (evidence_id, aircraft_id, chain_position) "
            "VALUES ('ev1', 'A1', 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO DigitalEvidence (evidence_id, aircraft_id, chain_position) "
                "VALUES ('ev2', 'A1', 1)"
            )
        # The same position on a different aircraft is fine.
        conn.execute(
            "INSERT INTO DigitalEvidence (evidence_id, aircraft_id, chain_position) "
            "VALUES ('ev3', 'A2', 1)"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_008_fails_loudly_on_preexisting_duplicates(db_path, monkeypatch):
    full_migrations = migrations_module.MIGRATIONS
    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations[:7])
    migrations_module.run_migrations()

    raw = sqlite3.connect(str(db_path))
    raw.execute("INSERT INTO Aircraft (aircraft_id) VALUES ('A1')")
    raw.execute("INSERT INTO Components (component_id, aircraft_id) VALUES ('C1', 'A1')")
    raw.execute(
        "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
        "VALUES ('C1', 'Overheat', 'High', 0)"
    )
    raw.execute(
        "INSERT INTO Faults (component_id, fault_type, severity, resolved) "
        "VALUES ('C1', 'Overheat', 'High', 0)"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(migrations_module, "MIGRATIONS", full_migrations)
    with pytest.raises(sqlite3.IntegrityError):
        migrations_module.run_migrations()

    versions = {
        r[0]
        for r in sqlite3.connect(str(db_path)).execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }
    assert versions == {1, 2, 3, 4, 5, 6, 7}