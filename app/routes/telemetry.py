"""
Sensor telemetry routes for C.O.R.E. CAMP.
Displays digital twin sensor readings and maintenance history timeline.
Includes API endpoints for simulated live sensor data generation.
"""
import random
from flask import Blueprint, render_template, request, jsonify
from app.database import get_db

bp = Blueprint('telemetry', __name__)

# In-memory fault injection flags (sufficient for demo)
_fault_flags = {}

# Sensor baselines and fault values
_SENSOR_PROFILES = {
    'Thermocouple': {
        'baseline': 450.0, 'noise': 15.0,
        'fault_value': 960.0, 'fault_noise': 20.0,
        'unit': '°C', 'min': 0, 'max': 1200,
        'threshold': 900, 'fault_label': 'Over Temp',
        'fault_type': 'overheat',
    },
    'Vibration Sensor': {
        'baseline': 1.2, 'noise': 0.15,
        'fault_value': 5.2, 'fault_noise': 0.3,
        'unit': 'g', 'min': 0, 'max': 8,
        'threshold': 4.5, 'fault_label': 'Vibration',
        'fault_type': 'vibration',
    },
    'Pressure Sensor': {
        'baseline': 45.0, 'noise': 2.0,
        'fault_value': 12.0, 'fault_noise': 1.5,
        'unit': 'PSI', 'min': 0, 'max': 80,
        'threshold': 20, 'fault_label': 'Low Pressure',
        'fault_type': 'pressure',
    },
}


def _get_or_create_components(conn, aircraft_id):
    """Get components for an aircraft, creating a default engine if none exist."""
    components = conn.execute(
        'SELECT component_id, component_type FROM Components WHERE aircraft_id = ?',
        (aircraft_id,)
    ).fetchall()

    if not components:
        default_id = f'Engine_{aircraft_id}'
        conn.execute(
            'INSERT INTO Components (component_id, aircraft_id, component_type) VALUES (?, ?, ?)',
            (default_id, aircraft_id, 'Engine')
        )
        conn.commit()
        components = conn.execute(
            'SELECT component_id, component_type FROM Components WHERE aircraft_id = ?',
            (aircraft_id,)
        ).fetchall()

    return components


def _generate_reading(sensor_type):
    """Generate a realistic sensor reading with noise."""
    profile = _SENSOR_PROFILES[sensor_type]
    aircraft_faults = _fault_flags.get('global', set())

    if profile['fault_type'] in aircraft_faults:
        value = profile['fault_value'] + random.uniform(-profile['fault_noise'], profile['fault_noise'])
    else:
        value = profile['baseline'] + random.uniform(-profile['noise'], profile['noise'])

    return round(max(profile['min'], min(profile['max'], value)), 2)


@bp.route('/telemetry')
def telemetry():
    """Display sensor telemetry readings for the selected aircraft."""
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()

        selected_tail = request.args.get('tail')
        if not selected_tail and fleet:
            selected_tail = fleet[0]['aircraft_id']

        selected_aircraft = next(
            (plane for plane in fleet if plane['aircraft_id'] == selected_tail),
            fleet[0] if fleet else None
        )

        if not selected_aircraft:
            return render_template('telemetry.html', fleet=fleet,
                                   selected_aircraft=None, telemetry=[],
                                   components=[])

        components = _get_or_create_components(conn, selected_tail)

        telemetry = conn.execute('''
            SELECT t.telemetry_id, t.sensor_type, t.reading_value, t.recorded_at,
                   c.component_id, c.component_type, c.aircraft_id
            FROM SensorTelemetry t
            JOIN Components c ON t.component_id = c.component_id
            WHERE c.aircraft_id = ?
            ORDER BY t.recorded_at DESC
            LIMIT 15
        ''', (selected_tail,)).fetchall()

        total_count = conn.execute('''
            SELECT COUNT(*) as cnt
            FROM SensorTelemetry t
            JOIN Components c ON t.component_id = c.component_id
            WHERE c.aircraft_id = ?
        ''', (selected_tail,)).fetchone()['cnt']

    return render_template('telemetry.html', fleet=fleet,
                           selected_aircraft=selected_aircraft,
                           telemetry=telemetry,
                           components=components,
                           total_count=total_count)


@bp.route('/api/telemetry/<aircraft_id>/poll')
def api_telemetry_poll(aircraft_id):
    """Generate one round of simulated sensor readings, persist to DB, return as JSON."""
    with get_db() as conn:
        components = _get_or_create_components(conn, aircraft_id)

        readings = []
        for comp in components:
            for sensor_type in _SENSOR_PROFILES:
                value = _generate_reading(sensor_type)
                conn.execute(
                    'INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, ?, ?)',
                    (comp['component_id'], sensor_type, value)
                )

                profile = _SENSOR_PROFILES[sensor_type]
                is_fault = (sensor_type == 'Thermocouple' and value > profile['threshold']) or \
                           (sensor_type == 'Vibration Sensor' and value > profile['threshold']) or \
                           (sensor_type == 'Pressure Sensor' and value < profile['threshold'])

                readings.append({
                    'component_id': comp['component_id'],
                    'component_type': comp['component_type'] or '—',
                    'sensor_type': sensor_type,
                    'reading_value': value,
                    'unit': profile['unit'],
                    'status': 'fault' if is_fault else 'nominal',
                    'fault_label': profile['fault_label'] if is_fault else None,
                    'threshold': profile['threshold'],
                    'min': profile['min'],
                    'max': profile['max'],
                    'baseline': profile['baseline'],
                })

        conn.commit()

    return jsonify({
        'aircraft_id': aircraft_id,
        'readings': readings,
        'active_faults': list(_fault_flags.get('global', set())),
    })


@bp.route('/api/telemetry/<aircraft_id>/inject_fault', methods=['POST'])
def api_inject_fault(aircraft_id):
    """Set a fault flag so the next poll generates anomalous readings."""
    data = request.get_json(silent=True) or {}
    fault_type = data.get('fault_type', '')

    valid_faults = {'overheat', 'vibration', 'pressure'}
    if fault_type not in valid_faults:
        return jsonify({'status': 'error', 'message': f'Invalid fault_type. Must be one of: {valid_faults}'}), 400

    if 'global' not in _fault_flags:
        _fault_flags['global'] = set()
    _fault_flags['global'].add(fault_type)

    fault_labels = {
        'overheat': 'Engine Overheat (>900°C)',
        'vibration': 'Vibration Imbalance (>4.5g)',
        'pressure': 'Fuel Pressure Loss (<20 PSI)',
    }

    return jsonify({
        'status': 'ok',
        'message': f'Fault injected: {fault_labels.get(fault_type, fault_type)}',
        'active_faults': list(_fault_flags['global']),
    })


@bp.route('/api/telemetry/<aircraft_id>/clear_fault', methods=['POST'])
def api_clear_fault(aircraft_id):
    """Clear all fault flags and inject nominal baseline readings."""
    _fault_flags.pop('global', None)

    with get_db() as conn:
        components = _get_or_create_components(conn, aircraft_id)
        for comp in components:
            for sensor_type, profile in _SENSOR_PROFILES.items():
                conn.execute(
                    'INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, ?, ?)',
                    (comp['component_id'], sensor_type, profile['baseline'])
                )
        conn.commit()

    return jsonify({
        'status': 'ok',
        'message': 'Faults cleared — nominal baseline readings injected.',
        'active_faults': [],
    })


@bp.route('/api/telemetry/<aircraft_id>/history')
def api_telemetry_history(aircraft_id):
    """Return paginated historical telemetry readings as JSON."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    per_page = min(per_page, 100)  # cap at 100
    offset = (page - 1) * per_page

    with get_db() as conn:
        total = conn.execute('''
            SELECT COUNT(*) as cnt
            FROM SensorTelemetry t
            JOIN Components c ON t.component_id = c.component_id
            WHERE c.aircraft_id = ?
        ''', (aircraft_id,)).fetchone()['cnt']

        rows = conn.execute('''
            SELECT t.telemetry_id, t.sensor_type, t.reading_value, t.recorded_at,
                   c.component_id, c.component_type
            FROM SensorTelemetry t
            JOIN Components c ON t.component_id = c.component_id
            WHERE c.aircraft_id = ?
            ORDER BY t.recorded_at DESC
            LIMIT ? OFFSET ?
        ''', (aircraft_id, per_page, offset)).fetchall()

    history = []
    for r in rows:
        sensor_type = r['sensor_type']
        value = r['reading_value']
        is_fault = (sensor_type == 'Thermocouple' and value > 900) or \
                   (sensor_type == 'Vibration Sensor' and value > 4.5) or \
                   (sensor_type == 'Pressure Sensor' and value < 20)
        history.append({
            'recorded_at': r['recorded_at'],
            'component_id': r['component_id'],
            'component_type': r['component_type'] or '—',
            'sensor_type': sensor_type,
            'reading_value': value,
            'status': 'fault' if is_fault else 'nominal',
            'fault_label': 'Over Temp' if (sensor_type == 'Thermocouple' and value > 900) else
                           'Vibration' if (sensor_type == 'Vibration Sensor' and value > 4.5) else
                           'Low Pressure' if (sensor_type == 'Pressure Sensor' and value < 20) else None,
        })

    total_pages = max(1, (total + per_page - 1) // per_page)

    return jsonify({
        'aircraft_id': aircraft_id,
        'history': history,
        'count': len(history),
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })
