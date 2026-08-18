"""
Ghost Data Elimination (Feature #13).

archives/clear_ghosts.py shows what "ghost data" meant in practice for this
project: PilotReports that never got auto-closed because the auto-close in
fault_resolution.py is wrapped in a bare `except Exception: pass` (a PIREP
whose amm_reference doesn't parse to an integer id silently fails to close).
That archived script fixed it with a single blunt
`UPDATE PilotReports SET status = 'Closed'` - closing EVERY pilot report,
resolved or not. This module replaces that hack with a real detector that
only touches rows that are genuinely orphaned/stale, and logs every action
for audit instead of silently mass-updating the table.

Ghost categories detected:
  1. Ghost PIREPs      - PilotReports still 'Open' whose linked fault (via
                         Faults.amm_reference = 'PIREP_ID_<id>') is already resolved.
  2. Orphaned Telemetry - SensorTelemetry rows pointing at a component_id that
                         no longer exists in Components.
  3. Orphaned Faults    - Faults rows pointing at a component_id that no
                         longer exists in Components.
  4. Frozen Sensors     - a sensor emitting the exact same reading_value many
                         times in a row (a stuck/disconnected sensor still
                         being treated as live data by the reasoner).
"""
from app.database import get_db

FROZEN_STREAK_THRESHOLD = 20


def ensure_ghost_schema():
    """Compatibility wrapper - GhostDataLog is created by the versioned
    migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def _log(conn, category, target_table, target_ref, detail, action):
    conn.execute(
        'INSERT INTO GhostDataLog (category, target_table, target_ref, detail, action) VALUES (?, ?, ?, ?, ?)',
        (category, target_table, target_ref, detail, action)
    )


def scan(apply_fixes=False):
    """
    Scan for ghost data. When apply_fixes is False this is a pure dry-run
    (nothing is changed, only reported). When True, safe fixes are applied:
    ghost PIREPs are closed, orphaned rows are purged, frozen sensors are
    only ever flagged (never auto-deleted - a real stuck sensor is itself
    a maintenance finding, not something to quietly delete).
    """
    ensure_ghost_schema()
    findings = {'ghost_pireps': [], 'orphaned_telemetry': [], 'orphaned_faults': [], 'frozen_sensors': []}

    with get_db() as conn:
        # 1) Ghost PIREPs
        pireps = conn.execute("SELECT * FROM PilotReports WHERE status = 'Open'").fetchall()
        for p in pireps:
            fault = conn.execute(
                "SELECT * FROM Faults WHERE amm_reference = ?", (f"PIREP_ID_{p['report_id']}",)
            ).fetchone()
            if fault and fault['resolved']:
                detail = f"PIREP #{p['report_id']} linked to resolved Fault #{fault['fault_id']} but still Open."
                findings['ghost_pireps'].append({'report_id': p['report_id'], 'detail': detail})
                if apply_fixes:
                    conn.execute("UPDATE PilotReports SET status = 'Closed' WHERE report_id = ?", (p['report_id'],))
                    _log(conn, 'GhostPIREP', 'PilotReports', str(p['report_id']), detail, 'AutoClosed')
                else:
                    _log(conn, 'GhostPIREP', 'PilotReports', str(p['report_id']), detail, 'Flagged')

        # 2) Orphaned telemetry
        orphan_telemetry = conn.execute('''
            SELECT t.telemetry_id, t.component_id FROM SensorTelemetry t
            LEFT JOIN Components c ON t.component_id = c.component_id
            WHERE c.component_id IS NULL
        ''').fetchall()
        for row in orphan_telemetry:
            detail = f"SensorTelemetry #{row['telemetry_id']} references missing component '{row['component_id']}'."
            findings['orphaned_telemetry'].append({'telemetry_id': row['telemetry_id'], 'detail': detail})
            if apply_fixes:
                conn.execute("DELETE FROM SensorTelemetry WHERE telemetry_id = ?", (row['telemetry_id'],))
                _log(conn, 'OrphanedTelemetry', 'SensorTelemetry', str(row['telemetry_id']), detail, 'Purged')
            else:
                _log(conn, 'OrphanedTelemetry', 'SensorTelemetry', str(row['telemetry_id']), detail, 'Flagged')

        # 3) Orphaned faults
        orphan_faults = conn.execute('''
            SELECT f.fault_id, f.component_id FROM Faults f
            LEFT JOIN Components c ON f.component_id = c.component_id
            WHERE c.component_id IS NULL
        ''').fetchall()
        for row in orphan_faults:
            detail = f"Fault #{row['fault_id']} references missing component '{row['component_id']}'."
            findings['orphaned_faults'].append({'fault_id': row['fault_id'], 'detail': detail})
            if apply_fixes:
                conn.execute("DELETE FROM Faults WHERE fault_id = ?", (row['fault_id'],))
                _log(conn, 'OrphanedFault', 'Faults', str(row['fault_id']), detail, 'Purged')
            else:
                _log(conn, 'OrphanedFault', 'Faults', str(row['fault_id']), detail, 'Flagged')

        # 4) Frozen sensors (never auto-deleted, always just flagged - it's a real finding)
        components = conn.execute('SELECT DISTINCT component_id FROM SensorTelemetry').fetchall()
        for comp in components:
            sensor_types = conn.execute(
                'SELECT DISTINCT sensor_type FROM SensorTelemetry WHERE component_id = ?',
                (comp['component_id'],)
            ).fetchall()
            for st in sensor_types:
                recent = conn.execute(
                    'SELECT reading_value FROM SensorTelemetry WHERE component_id = ? AND sensor_type = ? '
                    'ORDER BY recorded_at DESC LIMIT ?',
                    (comp['component_id'], st['sensor_type'], FROZEN_STREAK_THRESHOLD)
                ).fetchall()
                if len(recent) == FROZEN_STREAK_THRESHOLD and len({r['reading_value'] for r in recent}) == 1:
                    detail = (f"{comp['component_id']} / {st['sensor_type']} has reported the identical value "
                              f"{recent[0]['reading_value']} for {FROZEN_STREAK_THRESHOLD} consecutive readings.")
                    findings['frozen_sensors'].append({
                        'component_id': comp['component_id'], 'sensor_type': st['sensor_type'], 'detail': detail
                    })
                    _log(conn, 'FrozenSensor', 'SensorTelemetry',
                         f"{comp['component_id']}/{st['sensor_type']}", detail, 'Flagged')

        conn.commit()

    return findings
