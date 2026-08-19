"""
Calendar Kill Switch (Feature #10).

Problem this closes: app/routes/fault_resolution.py writes a CRS_Records row
the moment a fault is legally signed off, but it never reaches into the
Schedule table - so a hangar slot booked for that defect can sit on the
calendar forever even after the aircraft is released to service. The manual
"sign off schedule" flow in calendar.py already hides completed rows; this
module gives the CRS flow the same behaviour automatically, without editing
either file.

Approach: a lightweight background watcher polls CRS_Records for rows it
hasn't processed yet (tracked in KillSwitchProcessedCRS) and, for each new
CRS, auto-cancels any still-open Schedule / MEL_Deferrals / PilotReports rows
that clearly correlate to it (same aircraft + matching reference/keywords).
Every decision is written to KillSwitchLog for XAI-style auditability.
"""
import threading
import time
import re
from app.database import get_db


def ensure_ks_schema():
    """Compatibility wrapper - kill-switch tables are created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


_watcher_thread = None
_stop_event = threading.Event()
POLL_SECONDS = 10


def _keywords_from(text):
    """Pull meaningful tokens (3+ chars) out of a fault/CRS description for fuzzy matching."""
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text or "")}


def run_kill_switch_scan():
    """Process any CRS_Records rows not yet handled. Safe to call repeatedly."""
    ensure_ks_schema()
    actions = []

    with get_db() as conn:
        new_crs = conn.execute('''
            SELECT c.* FROM CRS_Records c
            LEFT JOIN KillSwitchProcessedCRS p ON c.id = p.crs_id
            WHERE p.crs_id IS NULL
        ''').fetchall()

        for crs in new_crs:
            # Prefer the stable aircraft_id when the row has one (migration
            # 009); fall back to the legacy registration-guess for old rows.
            aircraft_id_guess = crs['aircraft_id'] or f"Aircraft_{crs['aircraft_reg'].replace('-', '_')}"
            crs_keywords = _keywords_from(crs['reference_id']) | _keywords_from(crs['description'])

            # 1) Cancel matching open Schedule entries for this aircraft
            open_events = conn.execute(
                "SELECT rowid as record_id, * FROM Schedule "
                "WHERE aircraft_id = ? AND (status = 'Scheduled' OR status IS NULL)",
                (aircraft_id_guess,)
            ).fetchall()

            for ev in open_events:
                ev_ref = ev['related_reference'] if 'related_reference' in ev.keys() else None
                ev_keywords = _keywords_from(ev['title'])
                matched = (ev_ref and ev_ref == crs['reference_id']) or (crs_keywords & ev_keywords)

                if matched:
                    # Conditional update: only cancel if the event is STILL
                    # open. A manual sign-off or lifecycle expiry racing this
                    # watcher between the SELECT above and this UPDATE must
                    # not be overwritten, and the audit log must not claim a
                    # cancellation that did not happen (DB-09).
                    cur = conn.execute(
                        "UPDATE Schedule SET status = 'Cancelled-KillSwitch' "
                        "WHERE rowid = ? AND (status = 'Scheduled' OR status IS NULL)",
                        (ev['record_id'],)
                    )
                    if cur.rowcount == 0:
                        continue
                    reason = f"CRS {crs['reference_id']} released aircraft {crs['aircraft_reg']}; matched hangar slot '{ev['title']}'."
                    conn.execute(
                        "INSERT INTO KillSwitchLog (crs_id, aircraft_reg, target_table, target_record_id, action_taken, reason) "
                        "VALUES (?, ?, 'Schedule', ?, 'Cancelled', ?)",
                        (crs['id'], crs['aircraft_reg'], str(ev['record_id']), reason)
                    )
                    actions.append(reason)

            # 2) Auto-close any MEL deferral for this aircraft with matching keywords
            open_mels = conn.execute(
                "SELECT * FROM MEL_Deferrals WHERE aircraft_id = ? AND status = 'Active'",
                (aircraft_id_guess,)
            ).fetchall()
            for mel in open_mels:
                if crs_keywords & _keywords_from(mel['item_description']):
                    cur = conn.execute(
                        "UPDATE MEL_Deferrals SET status = 'Cleared-KillSwitch' "
                        "WHERE deferral_id = ? AND status = 'Active'",
                        (mel['deferral_id'],)
                    )
                    if cur.rowcount == 0:
                        continue
                    reason = f"CRS {crs['reference_id']} cleared MEL deferral: {mel['item_description']}."
                    conn.execute(
                        "INSERT INTO KillSwitchLog (crs_id, aircraft_reg, target_table, target_record_id, action_taken, reason) "
                        "VALUES (?, ?, 'MEL_Deferrals', ?, 'Cleared', ?)",
                        (crs['id'], crs['aircraft_reg'], str(mel['deferral_id']), reason)
                    )
                    actions.append(reason)

            conn.execute(
                "INSERT OR IGNORE INTO KillSwitchProcessedCRS (crs_id) VALUES (?)", (crs['id'],)
            )

        conn.commit()

    return actions


def _watcher_loop():
    while not _stop_event.is_set():
        try:
            run_kill_switch_scan()
        except Exception as e:
            print(f"⚠️ Kill switch scan error: {e}")
        _stop_event.wait(POLL_SECONDS)


def start_watcher():
    """Start the background CRS watcher once at app boot. Idempotent."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return
    if _stop_event.is_set():
        # Intentionally stopped (e.g. under the test harness) - do not
        # restart. Without this guard a create_app() call during tests would
        # spin a fresh thread whose first scan could hit a different test's
        # database.
        return
    ensure_ks_schema()
    _stop_event.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
    _watcher_thread.start()


def get_hangar_activity_log(limit=100):
    """
    Consolidated "past activities" record for the Hangar Schedule page's
    Activity Log tab - merges automatic kill-switch cancellations, automatic
    2-day schedule expirations, and manual engineer sign-offs into one
    chronological history, instead of the kill switch only ever showing its
    own automated actions.
    """
    from app.database import get_db as _get_db  # local import avoids any import-order surprises
    ensure_ks_schema()

    rows = []
    with _get_db() as conn:
        for r in conn.execute('SELECT * FROM KillSwitchLog ORDER BY id DESC LIMIT ?', (limit,)).fetchall():
            rows.append({
                'timestamp': r['timestamp'], 'category': 'Auto-Cancellation',
                'badge': 'warning', 'aircraft': r['aircraft_reg'],
                'detail': f"{r['target_table']} #{r['target_record_id']} - {r['reason']}",
            })

        try:
            for r in conn.execute('SELECT * FROM ScheduleLifecycleLog ORDER BY id DESC LIMIT ?', (limit,)).fetchall():
                rows.append({
                    'timestamp': r['timestamp'], 'category': 'Auto-Expired (2-Day Rule)',
                    'badge': 'danger', 'aircraft': None,
                    'detail': f"{r['title']} - {r['reason']}",
                })
        except Exception:
            pass  # ScheduleLifecycleLog may not exist yet on a fresh DB

        for r in conn.execute(
            "SELECT * FROM MaintenanceHistory WHERE task_description LIKE 'Hangar Check:%' "
            "ORDER BY log_id DESC LIMIT ?", (limit,)
        ).fetchall():
            rows.append({
                'timestamp': r['completion_date'] or r['sign_off_date'], 'category': 'Manual Sign-Off',
                'badge': 'success', 'aircraft': r['aircraft_reg'],
                'detail': f"{r['task_description']} \u2014 {r['signed_off_by']}",
            })

    rows.sort(key=lambda x: x['timestamp'] or '', reverse=True)
    return rows[:limit]
