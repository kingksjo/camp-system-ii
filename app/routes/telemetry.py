"""
Sensor telemetry routes for C.O.R.E. CAMP.
Displays digital twin sensor readings and maintenance history timeline.
Includes API endpoints for simulated live sensor data generation.

SENSOR FRAMEWORK (Round-3 revamp)
----------------------------------
Previously every component - engine, fuel tank, wing, whatever - was given
the exact same three sensors (Thermocouple, Vibration Sensor, Pressure
Sensor), regardless of whether that sensor made physical sense on it. That's
also what caused component/telemetry rows to sprawl with test debris: a
generic 'Engine_{aircraft_id}' component was silently invented for any
aircraft that didn't have one yet.

This is now two small registries, matching the same "add one line, nothing
else changes" pattern already used elsewhere in this codebase (see
CAMSISLimits/DEFAULT_LIMITS in app/camp_extensions/camsis.py and
COMPONENT_TECHNICAL_FIELDS in app/camp_extensions/maintenance_documents.py):

1. SENSOR_TYPE_REGISTRY - one definition per sensor type: baseline/noise,
   fault value/noise, unit, min/max, threshold + direction, and fault label.
   Single source of truth - nothing hardcodes a threshold anywhere else in
   this file anymore.

2. CATEGORY_SENSOR_MAP - which sensor types a *category* of component
   (Engine, FuelTank, Wing, ...) actually carries. Components.component_type
   in the DB is granular (LeftEngine, RightFuelTank, ...); `_category_for()`
   below folds those into a category by substring match, so
   'LeftEngine'/'RightEngine' both resolve to 'Engine', etc.

   Confirmed fleet-reality mapping (signed off by the head programmer):
     - Engine:   EGT (Thermocouple) + Vibration + Oil Pressure
     - FuelTank: Fuel Pressure + Fuel Temperature (freeze-risk monitoring)
     - Wing:     Vibration + Strain Gauge

TO INTEGRATE A NEW PHYSICAL SYSTEM LATER (avionics LRU, APU, landing gear,
hydraulics, ...):
    a) If it needs a sensor type that doesn't exist yet, add one entry to
       SENSOR_TYPE_REGISTRY (baseline/noise/threshold/unit/fault label).
    b) Add one entry to CATEGORY_SENSOR_MAP mapping the new category to the
       sensor type(s) it should carry.
    c) (Optional, only for sensors that should feed the AI reasoner's fault
       inference) add a matching threshold branch in
       app/ontology_reasoner.py's _evaluate_contextual_thresholds - it reads
       sensor_type as a plain string, so it's an additive elif, not a
       rewrite. Two examples (Oil Pressure Sensor, Strain Gauge) are already
       wired through end-to-end as a template to copy.
No other code in this file, telemetry.html, or api_telemetry_poll/history
needs to change - they all iterate the registries generically.
"""
import random
import json
from flask import Blueprint, render_template, request, jsonify
from app.database import get_db

bp = Blueprint('telemetry', __name__)

# In-memory fault injection flags (sufficient for demo)
_fault_flags = {}

# --- 1) Sensor type definitions (single source of truth) ------------------
SENSOR_TYPE_REGISTRY = {
    # --- Engine sensors ---
    'Thermocouple': {
        'baseline': 450.0, 'noise': 15.0,
        'fault_value': 960.0, 'fault_noise': 20.0,
        'unit': '°C', 'min': 0, 'max': 1200,
        'threshold': 900, 'direction': 'above', 'fault_label': 'Over Temp',
        'fault_type': 'overheat',
        'amm_reference': 'ATA_77 (Engine Indicating)',
    },
    'Vibration Sensor': {
        'baseline': 1.2, 'noise': 0.15,
        'fault_value': 5.2, 'fault_noise': 0.3,
        'unit': 'g', 'min': 0, 'max': 8,
        'threshold': 4.5, 'direction': 'above', 'fault_label': 'Vibration',
        'fault_type': 'vibration',
        'amm_reference': 'ATA_72 (Engine)',
    },
    'Oil Pressure Sensor': {
        'baseline': 55.0, 'noise': 3.0,
        'fault_value': 14.0, 'fault_noise': 2.0,
        'unit': 'PSI', 'min': 0, 'max': 100,
        'threshold': 25, 'direction': 'below', 'fault_label': 'Low Oil Pressure',
        'fault_type': 'oil_pressure',
        'amm_reference': 'ATA_79 (Engine Oil)',
    },
    # --- Fuel system sensors ---
    'Fuel Pressure Sensor': {
        'baseline': 45.0, 'noise': 2.0,
        'fault_value': 12.0, 'fault_noise': 1.5,
        'unit': 'PSI', 'min': 0, 'max': 80,
        'threshold': 20, 'direction': 'below', 'fault_label': 'Low Fuel Pressure',
        'fault_type': 'fuel_pressure',
        'amm_reference': 'ATA_28 (Fuel Systems)',
    },
    'Fuel Temperature Sensor': {
        'baseline': 15.0, 'noise': 3.0,
        'fault_value': -40.0, 'fault_noise': 2.0,
        'unit': '\u00b0C', 'min': -60, 'max': 60,
        'threshold': -37, 'direction': 'below', 'fault_label': 'Fuel Temp Low (Freeze Risk)',
        'fault_type': 'fuel_temp_low',
        'amm_reference': 'ATA_28 (Fuel Systems)',
    },
    # --- Airframe / structural sensors ---
    'Strain Gauge': {
        'baseline': 1.0, 'noise': 0.2,
        'fault_value': 4.2, 'fault_noise': 0.4,
        'unit': 'k\u03bc\u03b5', 'min': 0, 'max': 6,
        'threshold': 3.5, 'direction': 'above', 'fault_label': 'Structural Overstrain',
        'fault_type': 'overstrain',
        'amm_reference': 'ATA_57 (Wings)',
    },
}

# --- 2) Which sensor types each component *category* carries --------------
# Keys are categories, not raw Components.component_type strings - granular
# values like 'LeftEngine' / 'RightFuelTank' are folded into a category by
# _category_for() below via substring match, so seeding data doesn't need to
# match this dict's keys exactly.
CATEGORY_SENSOR_MAP = {
    'Engine': ['Thermocouple', 'Vibration Sensor', 'Oil Pressure Sensor'],
    'FuelTank': ['Fuel Pressure Sensor', 'Fuel Temperature Sensor'],
    'Wing': ['Vibration Sensor', 'Strain Gauge'],
    # Add new categories here as new physical systems are integrated, e.g.:
    # 'LandingGear': ['Strain Gauge'],
    # 'APU':         ['Thermocouple', 'Vibration Sensor'],
    # 'Avionics':    [],  # digital LRU - no analog sensor simulation needed
    '_default': ['Vibration Sensor'],
}

# Canonical component set created for any aircraft that has none yet, so the
# system never again invents a single miscellaneous 'Engine_{aircraft_id}'
# component on first telemetry view. (component_type, id_prefix)
CANONICAL_COMPONENT_TEMPLATE = [
    ('LeftEngine', 'Engine_L'),
    ('RightEngine', 'Engine_R'),
    ('LeftFuelTank', 'FuelTank_L'),
    ('RightFuelTank', 'FuelTank_R'),
    ('CenterFuelTank', 'FuelTank_C'),
    ('WingSystem', 'Wing_L'),
    ('WingSystem', 'Wing_R'),
]


# --- 3) Display metadata for the gauge cards / dropdown (UI only - keeps
#     the physical/threshold registry above free of presentation concerns).
#     Add one entry here alongside any new SENSOR_TYPE_REGISTRY entry.
SENSOR_UI_META = {
    'Thermocouple': {'icon': 'bi-thermometer-half', 'color': 'var(--status-warning)'},
    'Vibration Sensor': {'icon': 'bi-graph-up', 'color': 'var(--accent-cyan)'},
    'Oil Pressure Sensor': {'icon': 'bi-droplet-half', 'color': 'var(--accent-blue)'},
    'Fuel Pressure Sensor': {'icon': 'bi-fuel-pump-fill', 'color': '#8b5cf6'},
    'Fuel Temperature Sensor': {'icon': 'bi-thermometer-snow', 'color': '#38bdf8'},
    'Strain Gauge': {'icon': 'bi-activity', 'color': '#f472b6'},
}


def _category_for(component_type):
    """Fold a granular Components.component_type value into a sensor category."""
    if not component_type:
        return '_default'
    ct = component_type.lower()
    if 'engine' in ct:
        return 'Engine'
    if 'fueltank' in ct or 'fuel_tank' in ct:
        return 'FuelTank'
    if 'wing' in ct:
        return 'Wing'
    if 'landinggear' in ct or 'landing_gear' in ct:
        return 'LandingGear'
    if 'apu' in ct:
        return 'APU'
    if 'avionic' in ct:
        return 'Avionics'
    return '_default'


def sensors_for_component(component_type):
    """The list of sensor type names a component should carry, given its category."""
    category = _category_for(component_type)
    return CATEGORY_SENSOR_MAP.get(category, CATEGORY_SENSOR_MAP['_default'])


def _aircraft_exists(conn, aircraft_id):
    return conn.execute(
        'SELECT 1 FROM Aircraft WHERE aircraft_id = ?', (aircraft_id,)
    ).fetchone() is not None


def _get_or_create_components(conn, aircraft_id):
    """Get components for an aircraft, seeding the canonical airframe set if none exist yet.

    Only ever seeds for an aircraft_id that actually exists in the Aircraft
    table - this is what let orphan/test debris (component rows for
    aircraft_ids that were just typed into a URL/API call, never a real
    tail) accumulate before the Round-3 cleanup. Callers should check
    _aircraft_exists() first and return 404 rather than relying on this
    silently returning an empty list."""
    if not _aircraft_exists(conn, aircraft_id):
        return []

    components = conn.execute(
        'SELECT component_id, component_type FROM Components WHERE aircraft_id = ?',
        (aircraft_id,)
    ).fetchall()

    if not components:
        tail_suffix = aircraft_id.replace('Aircraft_', '')
        for component_type, id_prefix in CANONICAL_COMPONENT_TEMPLATE:
            component_id = f'{id_prefix}_{tail_suffix}'
            conn.execute(
                'INSERT OR IGNORE INTO Components (component_id, aircraft_id, component_type) VALUES (?, ?, ?)',
                (component_id, aircraft_id, component_type)
            )
        conn.commit()
        components = conn.execute(
            'SELECT component_id, component_type FROM Components WHERE aircraft_id = ?',
            (aircraft_id,)
        ).fetchall()

    return components


def _generate_reading(sensor_type):
    """Generate a realistic sensor reading with noise."""
    profile = SENSOR_TYPE_REGISTRY[sensor_type]
    aircraft_faults = _fault_flags.get('global', set())

    if profile['fault_type'] in aircraft_faults:
        value = profile['fault_value'] + random.uniform(-profile['fault_noise'], profile['fault_noise'])
    else:
        value = profile['baseline'] + random.uniform(-profile['noise'], profile['noise'])

    return round(max(profile['min'], min(profile['max'], value)), 2)


def _is_fault(sensor_type, value):
    """Single source of truth for fault evaluation - direction-aware."""
    profile = SENSOR_TYPE_REGISTRY.get(sensor_type)
    if not profile:
        return False
    if profile['direction'] == 'above':
        return value > profile['threshold']
    return value < profile['threshold']


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

        raw_telemetry = conn.execute('''
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

    # Server-computed fault status per row, so the template never needs its
    # own copy of per-sensor thresholds (single source of truth stays here).
    telemetry_rows = []
    for row in raw_telemetry:
        profile = SENSOR_TYPE_REGISTRY.get(row['sensor_type'])
        is_fault = _is_fault(row['sensor_type'], row['reading_value'])
        telemetry_rows.append(dict(row, status='fault' if is_fault else 'nominal',
                                    fault_label=(profile['fault_label'] if (profile and is_fault) else None)))

    # Which gauge cards to show: only sensor types this aircraft's
    # components actually carry (per CATEGORY_SENSOR_MAP), in a stable,
    # logical order (registry insertion order: engine, fuel, structural).
    present = set()
    for comp in components:
        present.update(sensors_for_component(comp['component_type']))
    active_sensor_types = [s for s in SENSOR_TYPE_REGISTRY if s in present]

    gauge_meta = {
        s: {**SENSOR_TYPE_REGISTRY[s], **SENSOR_UI_META.get(s, {'icon': 'bi-cpu', 'color': 'var(--text-secondary)'})}
        for s in SENSOR_TYPE_REGISTRY
    }

    fault_options = [
        {'value': profile['fault_type'],
         'label': f"{profile['fault_label']} ({'>' if profile['direction'] == 'above' else '<'}{profile['threshold']}{profile['unit']})"}
        for profile in SENSOR_TYPE_REGISTRY.values()
    ]

    sensor_registry_json = json.dumps({
        s: {'unit': p['unit'], 'min': p['min'], 'max': p['max'], 'threshold': p['threshold'],
            'lowerFault': p['direction'] == 'below', 'faultLabel': p['fault_label']}
        for s, p in SENSOR_TYPE_REGISTRY.items()
    })
    fault_labels_json = json.dumps({
        p['fault_type']: f"{p['fault_label']} ({'>' if p['direction'] == 'above' else '<'}{p['threshold']}{p['unit']})"
        for p in SENSOR_TYPE_REGISTRY.values()
    })

    return render_template('telemetry.html', fleet=fleet,
                           selected_aircraft=selected_aircraft,
                           telemetry=telemetry_rows,
                           components=components,
                           total_count=total_count,
                           active_sensor_types=active_sensor_types,
                           gauge_meta=gauge_meta,
                           fault_options=fault_options,
                           sensor_registry_json=sensor_registry_json,
                           fault_labels_json=fault_labels_json)


@bp.route('/api/telemetry/<aircraft_id>/poll')
def api_telemetry_poll(aircraft_id):
    """Generate one round of simulated sensor readings, persist to DB, return as JSON.

    Each component only gets the sensor types its category maps to
    (sensors_for_component) instead of every registered sensor type."""
    with get_db() as conn:
        if not _aircraft_exists(conn, aircraft_id):
            return jsonify({'status': 'error', 'message': f'Unknown aircraft_id: {aircraft_id}'}), 404

        components = _get_or_create_components(conn, aircraft_id)

        readings = []
        for comp in components:
            for sensor_type in sensors_for_component(comp['component_type']):
                value = _generate_reading(sensor_type)
                conn.execute(
                    'INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, ?, ?)',
                    (comp['component_id'], sensor_type, value)
                )

                profile = SENSOR_TYPE_REGISTRY[sensor_type]
                is_fault = _is_fault(sensor_type, value)

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

    # Derived from the registry, not hardcoded - any new sensor type added to
    # SENSOR_TYPE_REGISTRY automatically becomes a valid fault_type here.
    valid_faults = {profile['fault_type'] for profile in SENSOR_TYPE_REGISTRY.values()}
    if fault_type not in valid_faults:
        return jsonify({'status': 'error', 'message': f'Invalid fault_type. Must be one of: {sorted(valid_faults)}'}), 400

    if 'global' not in _fault_flags:
        _fault_flags['global'] = set()
    _fault_flags['global'].add(fault_type)

    fault_labels = {
        profile['fault_type']: f"{profile['fault_label']} ({'>' if profile['direction'] == 'above' else '<'}{profile['threshold']}{profile['unit']})"
        for profile in SENSOR_TYPE_REGISTRY.values()
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
        if not _aircraft_exists(conn, aircraft_id):
            return jsonify({'status': 'error', 'message': f'Unknown aircraft_id: {aircraft_id}'}), 404

        components = _get_or_create_components(conn, aircraft_id)
        for comp in components:
            for sensor_type in sensors_for_component(comp['component_type']):
                profile = SENSOR_TYPE_REGISTRY[sensor_type]
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
        is_fault = _is_fault(sensor_type, value)
        profile = SENSOR_TYPE_REGISTRY.get(sensor_type)
        history.append({
            'recorded_at': r['recorded_at'],
            'component_id': r['component_id'],
            'component_type': r['component_type'] or '—',
            'sensor_type': sensor_type,
            'reading_value': value,
            'status': 'fault' if is_fault else 'nominal',
            'fault_label': (profile['fault_label'] if (profile and is_fault) else None),
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
