"""
Dashboard routes for C.O.R.E. CAMP.
Main entry point with fleet overview and active faults.
"""
from flask import Blueprint, render_template, request
import json
from app.database import get_db
from app.cbr_engine import retrieve_similar_cases

bp = Blueprint('dashboard', __name__)


@bp.route('/')
def dashboard():
    """Main dashboard showing fleet status and active faults."""
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        
        # Get selected aircraft
        selected_tail = request.args.get('tail')
        if not selected_tail and fleet:
            selected_tail = fleet[0]['aircraft_id']
        
        selected_aircraft = next(
            (plane for plane in fleet if plane['aircraft_id'] == selected_tail),
            fleet[0] if fleet else None
        )
        
        if not selected_aircraft:
            return render_template('dashboard.html', fleet=fleet, faults=[], 
                                   telemetry=[], directives=[], schedule_sync=[])
        
        current_model = selected_aircraft['model']
        
        # Get directives for this model
        directives = conn.execute(
            'SELECT * FROM Directives WHERE target_model = ? AND status = "Open" ORDER BY doc_type ASC',
            (current_model,)
        ).fetchall()
        
        # Get active faults with CBR matches
        raw_faults = conn.execute('''
            SELECT f.fault_id, f.fault_type, f.severity, f.amm_reference, c.component_id, c.aircraft_id 
            FROM Faults f 
            JOIN Components c ON f.component_id = c.component_id 
            WHERE f.resolved = 0 AND c.aircraft_id = ?
        ''', (selected_tail,)).fetchall()
        
        # Enrich faults with CBR recommendations
        faults_with_cbr = []
        for f in raw_faults:
            fault_dict = dict(f)
            ac_reg = f['aircraft_id'].replace('Aircraft_', '')
            
            # Retrieve similar historical cases
            similar_past_repairs = retrieve_similar_cases(f['fault_type'], ac_reg)
            fault_dict['cbr_matches'] = similar_past_repairs
            faults_with_cbr.append(fault_dict)
        
        # Get latest telemetry
        telemetry = conn.execute('''
            SELECT t.sensor_type, t.reading_value, t.recorded_at, c.component_id 
            FROM SensorTelemetry t 
            JOIN Components c ON t.component_id = c.component_id 
            WHERE c.aircraft_id = ? 
            ORDER BY t.recorded_at DESC LIMIT 10
        ''', (selected_tail,)).fetchall()
        
        # Get scheduled events
        schedule_sync = conn.execute(
            'SELECT * FROM Schedule ORDER BY start_time ASC LIMIT 6'
        ).fetchall()
        
        # Get engineers for UI
        try:
            engineers = conn.execute('SELECT * FROM Engineers').fetchall()
        except Exception:
            engineers = []
        
        # Prepare chart data
        chart_labels = [plane['registration'] for plane in fleet]
        chart_data = [plane['total_flight_hours'] for plane in fleet]
    
    return render_template(
        'dashboard.html',
        fleet=fleet,
        selected_aircraft=selected_aircraft,
        faults=faults_with_cbr,
        telemetry=telemetry,
        directives=directives,
        schedule_sync=schedule_sync,
        chart_labels=json.dumps(chart_labels),
        chart_data=json.dumps(chart_data),
        engineers=engineers
    )
