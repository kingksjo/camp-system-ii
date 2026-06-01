"""
Calendar and hangar schedule management routes for C.O.R.E. CAMP.
Manages AME schedule and check events (A/B/C checks).
"""
from flask import Blueprint, render_template, request, redirect, url_for
from datetime import datetime
import json
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action

bp = Blueprint('calendar', __name__)


@bp.route('/calendar')
def calendar():
    """Display maintenance calendar and schedule."""
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        schedule_data = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule WHERE status = "Scheduled" OR status IS NULL ORDER BY start_time ASC'
        ).fetchall()
        
        try:
            engineers = conn.execute("SELECT * FROM Engineers").fetchall()
        except Exception:
            engineers = []
    
    # Format events for calendar UI
    events = [
        {
            'title': item['title'],
            'start': item['start_time'].replace(' ', 'T'),
            'end': item['end_time'].replace(' ', 'T'),
            'color': item['color']
        }
        for item in schedule_data
    ]
    
    return render_template(
        'calendar.html',
        events=json.dumps(events),
        fleet=fleet,
        schedule_data=schedule_data,
        engineers=engineers
    )


@bp.route('/schedule_check', methods=['POST'])
def schedule_check():
    """Schedule a maintenance check (A/B/C)."""
    aircraft_id = request.form['aircraft_id']
    check_type = request.form['check_type']
    
    start_time = datetime.strptime(request.form['start_time'], '%Y-%m-%dT%H:%M')
    end_time = datetime.strptime(request.form['end_time'], '%Y-%m-%dT%H:%M')
    
    # Assign color by check type
    colors = {
        'A-Check': "#fd7e14",
        'B-Check': "#6c757d",
        'C-Check': "#212529"
    }
    color = colors.get(check_type, "#0d6efd")
    
    title = f"Scheduled {check_type} ({aircraft_id.replace('Aircraft_', '')})"
    
    with get_db() as conn:
        conn.execute(
            'INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time, color) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                aircraft_id,
                'Maintenance',
                title,
                start_time.strftime('%Y-%m-%d %H:%M:%S'),
                end_time.strftime('%Y-%m-%d %H:%M:%S'),
                color
            )
        )
        conn.commit()
    
    return redirect(url_for('calendar.calendar'))


@bp.route('/sign_off_schedule/<int:record_id>', methods=['POST'])
def sign_off_schedule(record_id):
    """Sign off a completed schedule item."""
    emp_id = request.form.get('engineer_id')
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?',
            (emp_id,)
        ).fetchone()
        
        schedule_item = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule WHERE rowid = ?',
            (record_id,)
        ).fetchone()
        
        if engineer and schedule_item:
            digital_signature = create_digital_signature(engineer)
            task_desc = f"Hangar Check: {schedule_item['title']}"
            aircraft_reg = schedule_item['aircraft_id'].replace('Aircraft_', '')
            
            log_maintenance_action(aircraft_reg, task_desc, digital_signature, conn=conn)
            
            conn.execute("UPDATE Schedule SET status = 'Completed' WHERE rowid = ?", (record_id,))
        
        conn.commit()
    
    return redirect(url_for('calendar.calendar'))
