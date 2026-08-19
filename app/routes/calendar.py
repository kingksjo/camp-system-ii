"""
Calendar and hangar schedule management routes for C.O.R.E. CAMP.
Manages AME schedule and check events (A/B/C checks).

This page now also hosts what used to be two separate sidebar items:
the "Interactive Schedule" (FullCalendar drag/drop view) and the
"Calendar Kill Switch" (now a hybrid live-watcher + activity log), both as
tabs alongside the original weekly grid - see templates/calendar.html.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
import json
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action
from app.license_compliance import check_schedule_signoff
from app.auth import get_current_company_id
from app.camp_extensions import kill_switch

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

    company_id = get_current_company_id()
    with get_db() as conn:
        fleet = conn.execute(
            'SELECT * FROM Aircraft WHERE company_id = ?', (company_id,)
        ).fetchall()
        schedule_data = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule '
            'WHERE company_id = ? AND (status = "Scheduled" OR status IS NULL) ORDER BY start_time ASC',
            (company_id,)
        ).fetchall()

        try:
            engineers = conn.execute(
                "SELECT * FROM Engineers WHERE company_id = ?", (company_id,)
            ).fetchall()
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

    # Data for the merged "Interactive Calendar" and "Activity Log" tabs
    # (previously the separate Interactive Schedule / Calendar Kill Switch pages).
    kill_switch.ensure_ks_schema()
    with get_db() as conn:
        pending_crs_scans = conn.execute('''
            SELECT COUNT(*) as cnt FROM CRS_Records c
            LEFT JOIN KillSwitchProcessedCRS p ON c.id = p.crs_id
            WHERE p.crs_id IS NULL AND c.company_id = ?
        ''', (company_id,)).fetchone()['cnt']
    activity_log = kill_switch.get_hangar_activity_log(limit=100, company_id=company_id)

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
        hours=range(24),
        pending_crs_scans=pending_crs_scans,
        activity_log=activity_log,
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
    
    company_id = get_current_company_id()
    with get_db() as conn:
        aircraft = conn.execute(
            'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
            (aircraft_id, company_id)
        ).fetchone()
        if not aircraft:
            flash(f'Unknown aircraft {aircraft_id} - event not scheduled.', 'error')
            return redirect(url_for('calendar.calendar'))

        conn.execute(
            'INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time, color, company_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                aircraft_id,
                'Maintenance',
                title,
                start_time.strftime('%Y-%m-%d %H:%M:%S'),
                end_time.strftime('%Y-%m-%d %H:%M:%S'),
                color,
                company_id
            )
        )
        conn.commit()
    
    return redirect(url_for('calendar.calendar'))


@bp.route('/sign_off_schedule/<int:record_id>', methods=['POST'])
def sign_off_schedule(record_id):
    """Sign off a completed schedule item (license-gated - see app/license_compliance.py)."""
    emp_id = request.form.get('engineer_id')
    company_id = get_current_company_id()
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name, license_number, stamp_number, license_type FROM Engineers '
            'WHERE emp_id = ? AND company_id = ?',
            (emp_id, company_id)
        ).fetchone()
        
        schedule_item = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule WHERE event_id = ? AND company_id = ?',
            (record_id, company_id)
        ).fetchone()
        
        if engineer and schedule_item:
            # License compliance gate: only engineers holding a license
            # authorized for this class of check may sign it off (fixes the
            # cross sign-off report - previously ANY engineer_id could sign
            # off ANY check type here).
            check_type = schedule_item['event_type'] if 'event_type' in schedule_item.keys() else None
            allowed, required = check_schedule_signoff(engineer['license_type'], check_type)
            if not allowed:
                required_display = " or ".join(sorted(required)) if required else "an authorized"
                return (
                    f"<h1>COMPLIANCE LOCKOUT</h1>"
                    f"<p>Signing off on a <b>{check_type}</b> requires {required_display} license. "
                    f"You hold a {engineer['license_type']}.</p>"
                    f"<a href='/calendar'>Return to Calendar</a>"
                ), 403

            digital_signature = create_digital_signature(engineer)
            task_desc = f"Hangar Check: {schedule_item['title']}"
            aircraft_reg = schedule_item['aircraft_id'].replace('Aircraft_', '')
            
            log_maintenance_action(
                aircraft_reg, task_desc, digital_signature, conn=conn, company_id=company_id
            )
            
            conn.execute(
                "UPDATE Schedule SET status = 'Completed' WHERE event_id = ? AND company_id = ?",
                (record_id, company_id)
            )
        
        conn.commit()
    
    return redirect(url_for('calendar.calendar'))
