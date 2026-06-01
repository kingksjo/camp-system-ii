"""
Pilot report (PIREP) and flight log routes for C.O.R.E. CAMP.
Handles crew discrepancies and auto-fault generation.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db

bp = Blueprint('flight_log', __name__)


@bp.route('/flight_log', methods=['GET', 'POST'])
def flight_log():
    """Display flight logs and pilot discrepancy reports."""
    if request.method == 'POST':
        aircraft_id = request.form.get('aircraft_id')
        reported_by = request.form.get('reported_by').strip()
        discrepancy_text = request.form.get('discrepancy_text').strip()
        
        with get_db() as conn:
            # Create pilot report
            cursor = conn.execute(
                'INSERT INTO PilotReports (aircraft_id, reported_by, discrepancy_text) VALUES (?, ?, ?)',
                (aircraft_id, reported_by, discrepancy_text)
            )
            pirep_id = cursor.lastrowid
            
            # Auto-generate fault from PIREP
            fault_type = f"PIREP ({reported_by}): {discrepancy_text}"
            unique_airframe_id = f"Airframe_{aircraft_id}"
            
            try:
                # Try to get or create component
                existing = conn.execute(
                    'SELECT component_id FROM Components WHERE component_id = ?',
                    (unique_airframe_id,)
                ).fetchone()
                
                if not existing:
                    conn.execute(
                        'INSERT INTO Components (component_id, aircraft_id) VALUES (?, ?)',
                        (unique_airframe_id, aircraft_id)
                    )
            except Exception:
                pass
            
            # Create fault record
            conn.execute(
                'INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference) '
                'VALUES (?, ?, "Pilot Report", 0, ?)',
                (unique_airframe_id, fault_type, f"PIREP_ID_{pirep_id}")
            )
            
            conn.commit()
        
        return redirect(url_for('flight_log.flight_log'))
    
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        reports = conn.execute(
            'SELECT rowid AS record_id, * FROM PilotReports WHERE status = "Open" ORDER BY rowid DESC'
        ).fetchall()
    
    return render_template('flight_log.html', fleet=fleet, reports=reports)
