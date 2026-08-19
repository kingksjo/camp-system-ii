"""
IoT Smart Tool Integration (Feature #18).

The gap noted was "architecture references torque tools, but Bluetooth
ingestion is not fully implemented." A server process cannot itself hold a
Bluetooth radio conversation with a wrench sitting in someone's hand - the
browser can, though, via the real Web Bluetooth API (Chrome/Edge, HTTPS or
localhost). This module provides:

  1. A genuine Web Bluetooth GATT client (in the template's <script>) that
     scans for, pairs with, and subscribes to notifications from a BLE
     torque tool - no simulation, this is the actual browser API.
  2. A plain HTTP ingestion endpoint any Bluetooth gateway (a phone app, a
     Raspberry Pi BLE-to-WiFi bridge, etc.) can also POST readings to, since
     not every deployment will have a Chrome tab open on the hangar floor.

Either path lands in the same IoTToolReadings table and is checked against
TorqueSpecs so an out-of-spec fastening is flagged immediately, before the
sign-off step ever sees it.
"""
import uuid
from app.database import get_db
from app.auth import get_current_company_id


def ensure_iot_schema():
    """Compatibility wrapper - IoT tables + seed specs are created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def ingest_reading(tool_id, task_id, component_id, torque_value, spec_id, device_name, ingestion_source, unit='Nm', company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_iot_schema()
    reading_id = uuid.uuid4().hex

    with get_db() as conn:
        if tool_id:
            tool = conn.execute(
                'SELECT 1 FROM ToolCrib WHERE tool_id = ? AND company_id = ?', (tool_id, company_id)
            ).fetchone()
            if not tool:
                raise ValueError(f"Unknown tool {tool_id}")
        if task_id:
            task = conn.execute(
                'SELECT 1 FROM MaintenanceTasks WHERE task_id = ?', (task_id,)
            ).fetchone()
            if not task:
                raise ValueError(f"Unknown task {task_id}")
        if component_id:
            component = conn.execute(
                'SELECT 1 FROM Components WHERE component_id = ? AND company_id = ?', (component_id, company_id)
            ).fetchone()
            if not component:
                raise ValueError(f"Unknown component {component_id}")

        in_spec = None
        spec = None
        if spec_id:
            spec = conn.execute('SELECT * FROM TorqueSpecs WHERE spec_id = ?', (spec_id,)).fetchone()
            if spec:
                in_spec = int(spec['min_torque'] <= torque_value <= spec['max_torque'])
            else:
                raise ValueError(f"Unknown torque spec {spec_id}")

        conn.execute('''
            INSERT INTO IoTToolReadings
                (reading_id, tool_id, task_id, component_id, torque_value, unit, spec_id, in_spec, device_name, ingestion_source, company_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (reading_id, tool_id, task_id, component_id, torque_value, unit, spec_id, in_spec, device_name, ingestion_source, company_id))

        if in_spec == 0:
            conn.execute(
                'INSERT INTO XAILogs (component_id, ai_decision, explanation_text, company_id) VALUES (?, ?, ?, ?)',
                (component_id or 'Unknown', 'Torque Out Of Spec',
                 f"Tool {tool_id} recorded {torque_value}{unit}, outside spec "
                 f"{spec['min_torque']}-{spec['max_torque']}{unit} for {spec['fastener_description']}.",
                 company_id)
            )
        conn.commit()

    return {'reading_id': reading_id, 'in_spec': in_spec}
