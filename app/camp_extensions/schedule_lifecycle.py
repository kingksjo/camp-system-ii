"""
Time-Bound Schedule Events (Feature request #2).

Two behaviours the fleet schedule didn't have:

  1. A reminder that fires the moment a scheduled event's start time
     arrives (surfaced as a small toast, polled globally from base.html so
     it's visible no matter which page the user is on).
  2. Automatic removal of a schedule item if nobody signs it off within 2
     days of its END time - engineer sign-off (calendar.py /
     fullcalendar_schedule.py) already removes it immediately; this adds
     the "or after 2 days" half.

A background watcher (same pattern as kill_switch.py's CRS watcher) polls
every 30 seconds. Nothing in calendar.py or fullcalendar_schedule.py is
modified - both already filter out non-'Scheduled' rows, so an
auto-expired item disappears from both views the moment its status flips,
exactly like a manual sign-off does.
"""
import threading
from datetime import datetime, timedelta
from app.database import get_db
from app.auth import get_current_company_id

EXPIRY_GRACE_DAYS = 2
POLL_SECONDS = 30

_watcher_thread = None
_stop_event = threading.Event()


def ensure_lifecycle_schema():
    """Compatibility wrapper - lifecycle tables are created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def _resolve_company_scope(company_id):
    """Explicit company_id scopes to one tenant; None is a fleet-wide sweep
    across every company (background watchers never read the Flask session)."""
    if company_id is not None:
        return [company_id]
    with get_db() as conn:
        return [r['company_id'] for r in conn.execute('SELECT company_id FROM Companies').fetchall()]


def scan_and_fire_reminders(company_id=None):
    """Fire a reminder the first time 'now' passes a Scheduled event's start_time."""
    ensure_lifecycle_schema()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fired = []

    with get_db() as conn:
        for cid in _resolve_company_scope(company_id):
            due = conn.execute('''
                SELECT rowid as record_id, * FROM Schedule s
                WHERE s.company_id = ?
                  AND (status = 'Scheduled' OR status IS NULL)
                  AND start_time <= ?
                  AND NOT EXISTS (SELECT 1 FROM ScheduleReminders r WHERE r.record_id = s.rowid)
            ''', (cid, now_str)).fetchall()

            for ev in due:
                # INSERT OR IGNORE: record_id is the primary key, so a reminder
                # fired by a concurrent scan (or a re-scan) is a no-op, not an
                # IntegrityError (DB-09).
                cur = conn.execute(
                    'INSERT OR IGNORE INTO ScheduleReminders (record_id, title, aircraft_id, start_time, company_id) VALUES (?, ?, ?, ?, ?)',
                    (ev['record_id'], ev['title'], ev['aircraft_id'], ev['start_time'], cid)
                )
                if cur.rowcount:
                    fired.append(ev['record_id'])

        conn.commit()

    return fired


def scan_and_expire_stale(company_id=None):
    """Auto-remove a schedule item if it's more than EXPIRY_GRACE_DAYS past its end_time with no sign-off."""
    ensure_lifecycle_schema()
    cutoff = (datetime.now() - timedelta(days=EXPIRY_GRACE_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    expired = []

    with get_db() as conn:
        for cid in _resolve_company_scope(company_id):
            stale = conn.execute('''
                SELECT rowid as record_id, * FROM Schedule
                WHERE company_id = ?
                  AND (status = 'Scheduled' OR status IS NULL)
                  AND end_time < ?
            ''', (cid, cutoff)).fetchall()

            for ev in stale:
                # Conditional update: only expire if the event is STILL open. A
                # manual sign-off racing this watcher between the SELECT above
                # and this UPDATE must not be overwritten with a stale expiry,
                # and the lifecycle log must not claim an expiry that did not
                # happen (DB-09).
                cur = conn.execute(
                    "UPDATE Schedule SET status = 'Expired-AutoRemoved' "
                    "WHERE rowid = ? AND company_id = ? AND (status = 'Scheduled' OR status IS NULL)",
                    (ev['record_id'], cid)
                )
                if cur.rowcount == 0:
                    continue
                conn.execute(
                    'INSERT INTO ScheduleLifecycleLog (record_id, title, action, reason, company_id) VALUES (?, ?, ?, ?, ?)',
                    (ev['record_id'], ev['title'], 'Expired',
                     f"No sign-off within {EXPIRY_GRACE_DAYS} days of scheduled end time ({ev['end_time']}).", cid)
                )
                expired.append(ev['record_id'])

        conn.commit()

    return expired


def get_pending_reminders(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_lifecycle_schema()
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM ScheduleReminders WHERE acknowledged = 0 AND company_id = ? ORDER BY fired_at DESC LIMIT 10',
            (company_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def acknowledge_reminder(record_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_lifecycle_schema()
    with get_db() as conn:
        conn.execute(
            'UPDATE ScheduleReminders SET acknowledged = 1 WHERE record_id = ? AND company_id = ?', (record_id, company_id)
        )
        conn.commit()


def _watcher_loop():
    while not _stop_event.is_set():
        try:
            scan_and_fire_reminders()
            scan_and_expire_stale()
        except Exception as e:
            print(f"⚠️ Schedule lifecycle watcher error: {e}")
        _stop_event.wait(POLL_SECONDS)


def start_watcher():
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    if _stop_event.is_set():
        # Intentionally stopped (e.g. under the test harness) - do not
        # restart. Without this guard a create_app() call during tests would
        # spin a fresh thread whose first scan could hit a different test's
        # database.
        return
    ensure_lifecycle_schema()
    _stop_event.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
    _watcher_thread.start()
