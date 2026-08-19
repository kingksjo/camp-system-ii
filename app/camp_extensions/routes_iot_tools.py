"""Routes for the IoT smart tool / Bluetooth torque bridge (Feature #18)."""
from flask import Blueprint, render_template, request, jsonify
from app.database import get_db
from app.camp_extensions import iot_tools as iot
from app.auth import get_current_company_id

bp = Blueprint('iot_tools', __name__)


@bp.route('/iot-tools')
def iot_tools_page():
    iot.ensure_iot_schema()
    company_id = get_current_company_id()
    with get_db() as conn:
        tools = conn.execute(
            'SELECT * FROM ToolCrib WHERE company_id = ?', (company_id,)
        ).fetchall()
        specs = conn.execute('SELECT * FROM TorqueSpecs').fetchall()
        readings = conn.execute(
            'SELECT * FROM IoTToolReadings WHERE company_id = ? ORDER BY received_at DESC LIMIT 30',
            (company_id,)
        ).fetchall()
    return render_template('extensions/iot_tools.html', tools=tools, specs=specs, readings=readings)


@bp.route('/api/iot/torque-reading', methods=['POST'])
def api_torque_reading():
    """
    Ingestion endpoint for BLE torque readings - callable from the browser's
    Web Bluetooth client on this page, or from any external gateway
    (phone app / Raspberry Pi BLE bridge) that can speak plain HTTP+JSON.
    """
    data = request.get_json(silent=True) or {}
    try:
        result = iot.ingest_reading(
            tool_id=data.get('tool_id'),
            task_id=data.get('task_id'),
            component_id=data.get('component_id'),
            torque_value=float(data['torque_value']),
            spec_id=data.get('spec_id'),
            device_name=data.get('device_name', 'Unknown BLE Device'),
            ingestion_source=data.get('ingestion_source', 'web-bluetooth'),
            unit=data.get('unit', 'Nm'),
            company_id=get_current_company_id(),
        )
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({'status': 'error', 'message': f'Invalid payload: {e}'}), 400

    return jsonify({'status': 'ok', **result})
