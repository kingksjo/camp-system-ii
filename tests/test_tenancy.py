"""
Phase 5 (DB-01/DB-13): cross-company isolation tests.

Every operational table now carries a company_id column (migration 010) and
every route/service filters on it. These tests prove the isolation actually
holds at the route level: a logged-in user from company A can neither see
nor mutate company B's rows, even when they know the exact IDs.
"""
import sqlite3

import pytest

from app.database import get_db_connection


@pytest.fixture
def tenancy_db(tmp_path, monkeypatch):
    """A database with two companies, each owning its own aircraft/parts of
    the fleet, engineers, tools, faults, history and deferrals."""
    from app.config import Config
    from app import migrations as migrations_module

    path = tmp_path / "camp_tenancy.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(path))
    monkeypatch.setattr(migrations_module.Config, "DATABASE_PATH", str(path))

    migrations_module.run_migrations()

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Company 1 is seeded by migration 002; make the insert idempotent.
        conn.execute("INSERT OR IGNORE INTO Companies (company_id, company_name) VALUES (1, 'Company One')")
        conn.execute("INSERT INTO Companies (company_id, company_name) VALUES (2, 'Company Two')")

        # Company 1 fleet
        conn.execute(
            "INSERT INTO Aircraft (aircraft_id, registration, model, company_id) VALUES (?, ?, ?, 1)",
            ('Aircraft_1A', '1A-ONE', 'Model-X',),
        )
        conn.execute(
            "INSERT INTO Components (component_id, aircraft_id, component_type, company_id) VALUES (?, ?, ?, 1)",
            ('Comp_1A', 'Aircraft_1A', 'Engine',),
        )
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference, company_id) "
            "VALUES (?, 'Company1 Fault', 'High', 0, 'ATA_77 (Engine Indicating)', 1)",
            ('Comp_1A',),
        )
        conn.execute(
            "INSERT INTO Engineers (emp_id, full_name, license_type, license_number, stamp_number, company_id) "
            "VALUES ('E1', 'Eng One', 'B1', 'L1', 'S1', 1)",
        )
        conn.execute(
            "INSERT INTO ToolCrib (tool_id, tool_name, category, calibration_due, status, company_id) "
            "VALUES ('T1', 'Torque Wrench', 'General', '2027-01-01', 'Available', 1)",
        )
        conn.execute(
            "INSERT INTO MaintenanceHistory (aircraft_reg, aircraft_id, task_description, signed_off_by, company_id) "
            "VALUES ('1A-ONE', 'Aircraft_1A', 'Company1 History', 'Eng One', 1)",
        )
        conn.execute(
            "INSERT INTO MEL_Deferrals (aircraft_id, item_description, mel_category, date_deferred, status, company_id) "
            "VALUES ('Aircraft_1A', 'Company1 Deferral', 'C', '2026-01-01 08:00:00', 'Active', 1)",
        )

        # Company 2 fleet
        conn.execute(
            "INSERT INTO Aircraft (aircraft_id, registration, model, company_id) VALUES (?, ?, ?, 2)",
            ('Aircraft_2B', '2B-TWO', 'Model-Y',),
        )
        conn.execute(
            "INSERT INTO Components (component_id, aircraft_id, component_type, company_id) VALUES (?, ?, ?, 2)",
            ('Comp_2B', 'Aircraft_2B', 'Engine',),
        )
        conn.execute(
            "INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference, company_id) "
            "VALUES (?, 'Company2 Fault', 'Critical', 0, 'ATA_72 (Engine)', 2)",
            ('Comp_2B',),
        )
        conn.execute(
            "INSERT INTO Engineers (emp_id, full_name, license_type, license_number, stamp_number, company_id) "
            "VALUES ('E2', 'Eng Two', 'B2', 'L2', 'S2', 2)",
        )
        conn.execute(
            "INSERT INTO ToolCrib (tool_id, tool_name, category, calibration_due, status, company_id) "
            "VALUES ('T2', 'Multimeter', 'Avionics', '2027-01-01', 'Available', 2)",
        )
        conn.execute(
            "INSERT INTO MaintenanceHistory (aircraft_reg, aircraft_id, task_description, signed_off_by, company_id) "
            "VALUES ('2B-TWO', 'Aircraft_2B', 'Company2 History', 'Eng Two', 2)",
        )
        conn.execute(
            "INSERT INTO MEL_Deferrals (aircraft_id, item_description, mel_category, date_deferred, status, company_id) "
            "VALUES ('Aircraft_2B', 'Company2 Deferral', 'C', '2026-01-01 08:00:00', 'Active', 2)",
        )
        conn.commit()
    finally:
        conn.close()

    return path


@pytest.fixture
def client(tenancy_db):
    from app import create_app

    app = create_app('testing')
    return app.test_client()


def _login_as(client, company_id, username='user'):
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = username
        sess['company_id'] = company_id


def test_dashboard_shows_only_own_faults(client):
    _login_as(client, 1)
    body = client.get('/').get_data(as_text=True)
    assert 'Company1 Fault' in body
    assert 'Company2 Fault' not in body

    _login_as(client, 2)
    body = client.get('/').get_data(as_text=True)
    assert 'Company2 Fault' in body
    assert 'Company1 Fault' not in body


def test_personnel_shows_only_own_engineers(client):
    _login_as(client, 1)
    body = client.get('/personnel').get_data(as_text=True)
    assert 'Eng One' in body
    assert 'Eng Two' not in body

    _login_as(client, 2)
    body = client.get('/personnel').get_data(as_text=True)
    assert 'Eng Two' in body
    assert 'Eng One' not in body


def test_tool_crib_shows_only_own_tools(client):
    _login_as(client, 1)
    body = client.get('/tool_crib').get_data(as_text=True)
    assert 'Torque Wrench' in body
    assert 'Multimeter' not in body

    _login_as(client, 2)
    body = client.get('/tool_crib').get_data(as_text=True)
    assert 'Multimeter' in body
    assert 'Torque Wrench' not in body


def test_mel_shows_only_own_deferrals(client):
    _login_as(client, 1)
    body = client.get('/mel').get_data(as_text=True)
    assert 'Company1 Deferral' in body
    assert 'Company2 Deferral' not in body

    _login_as(client, 2)
    body = client.get('/mel').get_data(as_text=True)
    assert 'Company2 Deferral' in body
    assert 'Company1 Deferral' not in body


def test_telemetry_poll_rejects_foreign_aircraft(client):
    _login_as(client, 1)
    resp = client.get('/api/telemetry/Aircraft_2B/poll')
    assert resp.status_code == 404

    _login_as(client, 2)
    resp = client.get('/api/telemetry/Aircraft_1A/poll')
    assert resp.status_code == 404


def test_telemetry_history_rejects_foreign_aircraft(client):
    _login_as(client, 1)
    resp = client.get('/api/telemetry/Aircraft_2B/history')
    assert resp.status_code == 404


def test_reasoner_rejects_foreign_aircraft(client):
    _login_as(client, 1)
    resp = client.post('/run_reasoner/Aircraft_2B', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 404


def test_resolve_fault_rejects_foreign_fault(client):
    _login_as(client, 1)
    resp = client.post(
        '/resolve_fault/2',
        data={'mechanic_id': 'E1', 'component_replaced': '0'},
        headers={'Accept': 'text/html'},
    )
    # Fault 2 belongs to company 2 - company 1 must not resolve it.
    assert b'Fault or Mechanic not found' in resp.data


def test_cbr_history_is_company_scoped(client):
    _login_as(client, 1)
    body = client.get('/history').get_data(as_text=True)
    assert 'Company1 History' in body
    assert 'Company2 History' not in body

    _login_as(client, 2)
    body = client.get('/history').get_data(as_text=True)
    assert 'Company2 History' in body
    assert 'Company1 History' not in body