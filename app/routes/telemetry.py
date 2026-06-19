"""
Sensor telemetry routes for C.O.R.E. CAMP.
Displays digital twin sensor readings and maintenance history timeline.
"""
from flask import Blueprint, render_template, request
from app.database import get_db

bp = Blueprint('telemetry', __name__)


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
                                   selected_aircraft=None, telemetry=[])

        telemetry = conn.execute('''
            SELECT t.telemetry_id, t.sensor_type, t.reading_value, t.recorded_at,
                   c.component_id, c.component_type, c.aircraft_id
            FROM SensorTelemetry t
            JOIN Components c ON t.component_id = c.component_id
            WHERE c.aircraft_id = ?
            ORDER BY t.recorded_at DESC
        ''', (selected_tail,)).fetchall()

    return render_template('telemetry.html', fleet=fleet,
                           selected_aircraft=selected_aircraft,
                           telemetry=telemetry)
