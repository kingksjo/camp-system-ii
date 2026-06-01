"""
Smart due list and predictive maintenance routes for C.O.R.E. CAMP.
Tracks maintenance intervals and scheduling.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action

bp = Blueprint('due_list', __name__)


@bp.route('/due_list')
def due_list():
    """Display smart due list with maintenance projections."""
    with get_db() as conn:
        aircraft = conn.execute('SELECT * FROM Aircraft').fetchall()
        tasks = conn.execute('SELECT * FROM MaintenanceTasks').fetchall()
        
        try:
            engineers = conn.execute("SELECT * FROM Engineers WHERE status = 'Active'").fetchall()
        except Exception:
            engineers = []
        
        # Get completed tasks for reference
        completed_history = conn.execute(
            'SELECT aircraft_reg, task_description FROM MaintenanceHistory'
        ).fetchall()
        completed_set = {
            f"{r['aircraft_reg']}_{r['task_description'].split(':')[0].replace('Completed ', '')}"
            for r in completed_history
        }
        
        projections = []
        for plane in aircraft:
            for task in tasks:
                task_key = f"{plane['registration']}_{task['task_id']}"
                
                if task_key in completed_set:
                    status = "COMPLETED"
                    remaining = 99999
                elif task['interval_hours'] > 0:
                    current_hrs, interval = plane['total_flight_hours'], task['interval_hours']
                    remaining = round((((current_hrs // interval) + 1) * interval) - current_hrs, 1)
                    status = "OVERDUE" if remaining <= 0 else "DUE SOON" if remaining <= 50 else "GOOD"
                elif task['interval_cycles'] > 0:
                    current_cyc, interval = plane['total_cycles'], task['interval_cycles']
                    remaining = int((((current_cyc // interval) + 1) * interval) - current_cyc)
                    status = "OVERDUE" if remaining <= 0 else "DUE SOON" if remaining <= 100 else "GOOD"
                else:
                    remaining = 99999
                    status = "GOOD"
                
                projections.append({
                    'registration': plane['registration'],
                    'task_id': task['task_id'],
                    'task_name': task['task_name'],
                    'current_hours': plane['total_flight_hours'],
                    'current_cycles': plane['total_cycles'],
                    'interval_hours': task['interval_hours'],
                    'interval_cycles': task['interval_cycles'],
                    'remaining': remaining,
                    'status': status
                })
    
    # Sort by urgency (tasks due soonest first)
    projections.sort(key=lambda x: x['remaining'])
    return render_template('due_list.html', projections=projections, engineers=engineers)


@bp.route('/sign_off_due/<registration>/<task_id>', methods=['POST'])
def sign_off_due(registration, task_id):
    """Sign off a completed maintenance task."""
    emp_id = request.form.get('engineer_id')
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?',
            (emp_id,)
        ).fetchone()
        
        task = conn.execute(
            'SELECT task_name FROM MaintenanceTasks WHERE task_id = ?',
            (task_id,)
        ).fetchone()
        
        if engineer and task:
            digital_signature = create_digital_signature(engineer)
            task_desc = f"Completed {task_id}: {task['task_name']}"
            
            log_maintenance_action(registration, task_desc, digital_signature, conn=conn)
        
        conn.commit()
    
    return redirect(url_for('due_list.due_list'))
