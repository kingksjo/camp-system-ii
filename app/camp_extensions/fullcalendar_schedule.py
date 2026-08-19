"""
Interactive Hangar Schedule - FullCalendar integration (Feature #9).

This is a second, drag-and-drop view of the SAME `Schedule` table that
app/routes/calendar.py already reads and writes. Nothing in calendar.py is
touched: this module only adds a JSON event feed + a FullCalendar.js page so
maintenance controllers get real interactive scheduling (click-to-create,
drag to reschedule, resize to extend) on top of the existing data model.
"""
from flask import Blueprint, render_template, request, jsonify, redirect
from datetime import datetime
from app.database import get_db

bp = Blueprint('fc_schedule', __name__)

COLOR_BY_TYPE = {
    'A-Check': '#fd7e14',
    'B-Check': '#6c757d',
    'C-Check': '#212529',
    'HIL-Test': '#8b5cf6',
    'AOG': '#ef4444',
    'Flight': '#0ea5e9',
}


def ensure_fc_schema():
    """Compatibility wrapper - Schedule.source/related_reference are applied
    by the versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


@bp.route('/schedule/fullcalendar')
def fullcalendar_page():
    """
    The Interactive Schedule is now a tab on the Hangar Schedule page
    (see templates/calendar.html) instead of its own sidebar entry - this
    keeps the old URL working for anyone with it bookmarked.
    """
    return redirect('/calendar#interactive')


@bp.route('/api/schedule/events')
def api_list_events():
    """FullCalendar JSON event feed."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT rowid as record_id, * FROM Schedule ORDER BY start_time ASC'
        ).fetchall()

    events = []
    for r in rows:
        cancelled = (r['status'] or '').startswith('Cancelled')
        events.append({
            'id': r['record_id'],
            'title': r['title'],
            'start': (r['start_time'] or '').replace(' ', 'T'),
            'end': (r['end_time'] or '').replace(' ', 'T'),
            'color': '#475569' if cancelled else (r['color'] or '#0d6efd'),
            'extendedProps': {
                'aircraft_id': r['aircraft_id'],
                'event_type': r['event_type'],
                'status': r['status'],
                'related_reference': r['related_reference'] if 'related_reference' in r.keys() else None,
            }
        })
    return jsonify(events)


@bp.route('/api/schedule/events', methods=['POST'])
def api_create_event():
    ensure_fc_schema()
    data = request.get_json(silent=True) or request.form
    aircraft_id = data.get('aircraft_id')
    event_type = data.get('event_type', 'Maintenance')
    title = data.get('title') or f"{event_type} ({aircraft_id})"
    start_time = data.get('start')
    end_time = data.get('end')
    related_reference = data.get('related_reference') or None
    color = COLOR_BY_TYPE.get(event_type, '#0d6efd')

    if not (aircraft_id and start_time and end_time):
        return jsonify({'status': 'error', 'message': 'aircraft_id, start and end are required'}), 400

    def _norm(dt_str):
        dt_str = dt_str.replace('T', ' ')
        if len(dt_str) == 16:  # 'YYYY-MM-DD HH:MM'
            dt_str += ':00'
        return dt_str

    with get_db() as conn:
        aircraft = conn.execute(
            'SELECT 1 FROM Aircraft WHERE aircraft_id = ?', (aircraft_id,)
        ).fetchone()
        if not aircraft:
            return jsonify({'status': 'error', 'message': f'Unknown aircraft {aircraft_id}'}), 400

        cur = conn.execute(
            'INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time, color, status, source, related_reference) '
            'VALUES (?, ?, ?, ?, ?, ?, "Scheduled", "fullcalendar", ?)',
            (aircraft_id, event_type, title, _norm(start_time), _norm(end_time), color, related_reference)
        )
        conn.commit()
        new_id = cur.lastrowid

    return jsonify({'status': 'ok', 'id': new_id})


@bp.route('/api/schedule/events/<int:record_id>/move', methods=['POST'])
def api_move_event(record_id):
    """Drag/resize handler - updates start/end for a rescheduled event."""
    data = request.get_json(silent=True) or {}
    start_time = data.get('start')
    end_time = data.get('end')

    if not (start_time and end_time):
        return jsonify({'status': 'error', 'message': 'start and end are required'}), 400

    start_time = start_time.replace('T', ' ')[:19]
    end_time = end_time.replace('T', ' ')[:19]

    with get_db() as conn:
        conn.execute(
            'UPDATE Schedule SET start_time = ?, end_time = ? WHERE rowid = ?',
            (start_time, end_time, record_id)
        )
        conn.commit()

    return jsonify({'status': 'ok'})


@bp.route('/api/schedule/events/<int:record_id>/cancel', methods=['POST'])
def api_cancel_event(record_id):
    """Manual cancel button - distinct from the automatic CRS kill switch (see kill_switch.py)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE Schedule SET status = 'Cancelled-Manual' WHERE rowid = ?",
            (record_id,)
        )
        conn.commit()
    return jsonify({'status': 'ok'})
