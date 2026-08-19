"""
Maintenance history and digital signature audit routes for C.O.R.E. CAMP.
Displays all historical maintenance actions and CRS records.
"""
from flask import Blueprint, render_template
from app.database import get_db
from app.auth import get_current_company_id

bp = Blueprint('history', __name__)


@bp.route('/history')
def maintenance_history():
    """Display complete maintenance history with digital signatures."""
    company_id = get_current_company_id()
    with get_db() as conn:
        # Combine routine maintenance and resolved faults - each row now carries
        # its own source_type/source_id so the UI can request a document for
        # that specific record (feature: release an actual document per entry).
        routine_history = conn.execute('''
            SELECT aircraft_reg, task_description, signed_off_by, completion_date,
                   'maintenance_log' AS source_type, log_id AS source_id
            FROM MaintenanceHistory
            WHERE company_id = ?
            
            UNION
            
            SELECT REPLACE(c.aircraft_id, 'Aircraft_', '') AS aircraft_reg, 
                   'Resolved Fault: ' || f.fault_type AS task_description, 
                   f.resolved_by AS signed_off_by, 
                   f.resolved_date AS completion_date,
                   'fault' AS source_type, f.fault_id AS source_id
            FROM Faults f
            JOIN Components c ON f.component_id = c.component_id
            WHERE f.resolved = 1 AND f.company_id = ?
            
            ORDER BY completion_date DESC
        ''', (company_id, company_id)).fetchall()
        
        # Get CRS (Certificate of Release to Service) records
        try:
            crs_records = conn.execute(
                'SELECT * FROM CRS_Records WHERE company_id = ? ORDER BY release_date DESC',
                (company_id,)
            ).fetchall()
        except Exception:
            crs_records = []
    
    return render_template('history.html', routine_history=routine_history, crs_records=crs_records)
