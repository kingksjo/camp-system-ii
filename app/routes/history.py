"""
Maintenance history and digital signature audit routes for C.O.R.E. CAMP.
Displays all historical maintenance actions and CRS records.
"""
from flask import Blueprint, render_template
from app.database import get_db

bp = Blueprint('history', __name__)


@bp.route('/history')
def maintenance_history():
    """Display complete maintenance history with digital signatures."""
    with get_db() as conn:
        # Combine routine maintenance and resolved faults
        routine_history = conn.execute('''
            SELECT aircraft_reg, task_description, signed_off_by, completion_date 
            FROM MaintenanceHistory
            
            UNION
            
            SELECT REPLACE(c.aircraft_id, 'Aircraft_', '') AS aircraft_reg, 
                   'Resolved Fault: ' || f.fault_type AS task_description, 
                   f.resolved_by AS signed_off_by, 
                   f.resolved_date AS completion_date
            FROM Faults f
            JOIN Components c ON f.component_id = c.component_id
            WHERE f.resolved = 1
            
            ORDER BY completion_date DESC
        ''').fetchall()
        
        # Get CRS (Certificate of Release to Service) records
        try:
            crs_records = conn.execute(
                'SELECT * FROM CRS_Records ORDER BY release_date DESC'
            ).fetchall()
        except Exception:
            crs_records = []
    
    return render_template('history.html', routine_history=routine_history, crs_records=crs_records)
