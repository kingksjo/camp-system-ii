"""
Calendar and hangar schedule management routes for C.O.R.E. CAMP.
Manages AME schedule and check events (A/B/C checks).
"""
from flask import Blueprint, render_template, request, redirect, url_for
from datetime import datetime, timedelta
import json
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action

bp = Blueprint('calendar', __name__)

BADGE_CLASS_BY_COLOR = {
    '#fd7e14': 'badge--schedule-a',
    '#6c757d': 'badge--schedule-b',
    '#212529': 'badge--schedule-c',
    '#0d6efd': 'badge--schedule-default'
}
DAY_MINUTES = 24 * 60


def _parse_dt(value):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def _build_week_grid(schedule_data, week_start, aircraft_lookup=None):
    """Build a 7-day grid (Mon-Sun) of positioned events for the Google-Calendar-style view."""
    aircraft_lookup = aircraft_lookup or {}
    week_days = []
    for i in range(7):
        day_date = week_start + timedelta(days=i)
        day_start = datetime.combine(day_date, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        day_events = []
        for item in schedule_data:
            try:
                ev_start = _parse_dt(item['start_time'])
                ev_end = _parse_dt(item['end_time'])
            except (ValueError, TypeError):
                continue

            # Skip events that don't overlap this day at all
            if ev_end <= day_start or ev_start >= day_end:
                continue

            clipped_start = max(ev_start, day_start)
            clipped_end = min(ev_end, day_end)

            start_minutes = (clipped_start - day_start).total_seconds() / 60
            end_minutes = (clipped_end - day_start).total_seconds() / 60
            if end_minutes <= start_minutes:
                end_minutes = start_minutes + 30  # minimum visible sliver

            aircraft_id = item['aircraft_id'] if 'aircraft_id' in item.keys() else None
            aircraft_reg = aircraft_lookup.get(aircraft_id, (aircraft_id or 'Unknown').replace('Aircraft_', '').replace('_', '-'))

            day_events.append({
                'record_id': item['record_id'],
                'title': item['title'],
                'aircraft_reg': aircraft_reg,
                'badge_class': BADGE_CLASS_BY_COLOR.get(item['color'], 'badge--schedule-default'),
                'top_pct': round((start_minutes / DAY_MINUTES) * 100, 3),
                'height_pct': round(((end_minutes - start_minutes) / DAY_MINUTES) * 100, 3),
                'time_label': ev_start.strftime('%b %d, %I:%M %p') + ' \u2192 ' + ev_end.strftime('%b %d, %I:%M %p'),
                'spans_multi_day': ev_start.date() != ev_end.date()
            })

        week_days.append({
            'date': day_date,
            'label': day_date.strftime('%a'),
            'day_num': day_date.strftime('%d'),
            'is_today': day_date == datetime.now().date(),
            'events': day_events
        })

    return week_days


@bp.route('/calendar')
def calendar():
    """Display maintenance calendar and schedule as a weekly Google-Calendar-style grid."""
    try:
        offset = int(request.args.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0

    today = datetime.now().date()
    current_week_start = today - timedelta(days=today.weekday())  # Monday
    week_start = current_week_start + timedelta(weeks=offset)
    week_end = week_start + timedelta(days=6)

    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        schedule_data = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule WHERE status = "Scheduled" OR status IS NULL ORDER BY start_time ASC'
        ).fetchall()

        try:
            engineers = conn.execute("SELECT * FROM Engineers").fetchall()
        except Exception:
            engineers = []

    aircraft_lookup = {plane['aircraft_id']: plane['registration'] for plane in fleet}
    week_days = _build_week_grid(schedule_data, week_start, aircraft_lookup)

    # Format events for calendar UI (legacy/JS consumers)
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
        engineers=engineers,
        week_days=week_days,
        week_start=week_start,
        week_end=week_end,
        week_offset=offset,
        hours=range(24)
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
