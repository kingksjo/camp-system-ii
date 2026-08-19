"""
Background job tracking for the AI Diagnostics run (fixes the "Run Diagnostics"
button freeze / long inference time report).

Root cause was twofold:
  1. app/ontology_reasoner.py invoked the external Pellet/Java reasoner once
     PER telemetry reading in a blocking loop (fixed there - see that file).
  2. The dashboard's "Run Diagnostics" button did a plain (non-AJAX) HTML
     form POST straight to that slow endpoint, so the whole browser tab sat
     there with no feedback until the server eventually responded - or, on
     environments where Java/Pellet isn't set up correctly, never did.

This module lets /run_reasoner/<aircraft_id> kick the (now much faster,
still potentially slow) analysis off on a background thread and return
immediately with a job_id. The dashboard polls /api/reasoner/status/<job_id>
and only navigates once the job is actually done - so the button can never
"freeze" the tab again, no matter how long the reasoner takes.
"""
import threading
import uuid
from datetime import datetime
from app.database import get_db


def ensure_jobs_schema():
    """Compatibility wrapper - DiagnosticJobs is created by the versioned
    migrations (app/migrations.py, migration 001). No-op once current."""
    from app.migrations import run_migrations
    run_migrations()


def start_diagnostic_job(aircraft_id, company_id=None):
    """Kick off run_fleet_analysis() on a background thread. Returns the new job_id immediately."""
    from app.ontology_reasoner import run_fleet_analysis  # local import avoids any import-order issues

    ensure_jobs_schema()
    job_id = uuid.uuid4().hex[:12]

    with get_db() as conn:
        conn.execute(
            "INSERT INTO DiagnosticJobs (job_id, aircraft_id, status) VALUES (?, ?, 'Running')",
            (job_id, aircraft_id)
        )
        conn.commit()

    def _worker():
        try:
            results = run_fleet_analysis(aircraft_id, company_id=company_id)
            fault_count = sum(1 for r in results if r.get('fault_detected'))
            with get_db() as conn:
                conn.execute(
                    "UPDATE DiagnosticJobs SET status = 'Complete', fault_count = ?, "
                    "completed_at = datetime('now','localtime') WHERE job_id = ?",
                    (fault_count, job_id)
                )
                conn.commit()
        except Exception as e:
            with get_db() as conn:
                conn.execute(
                    "UPDATE DiagnosticJobs SET status = 'Error', error_message = ?, "
                    "completed_at = datetime('now','localtime') WHERE job_id = ?",
                    (str(e), job_id)
                )
                conn.commit()

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_job_status(job_id):
    ensure_jobs_schema()
    with get_db() as conn:
        row = conn.execute('SELECT * FROM DiagnosticJobs WHERE job_id = ?', (job_id,)).fetchone()
    return dict(row) if row else None
