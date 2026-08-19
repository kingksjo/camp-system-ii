"""
AI Ontology Reasoner routes for C.O.R.E. CAMP (XAI - eXplainable AI).
Handles automated fault detection and reasoning engine execution.
"""
from flask import Blueprint, render_template, redirect, url_for, request, jsonify
from app.database import get_db
from app.ontology_reasoner import run_fleet_analysis
from app.diagnostics_jobs import start_diagnostic_job, get_job_status
from app.auth import get_current_company_id

bp = Blueprint('reasoner', __name__)


@bp.route('/run_reasoner/<aircraft_id>', methods=['GET', 'POST'])
def run_reasoner(aircraft_id):
    """
    Run the Pellet ontology reasoner on aircraft telemetry.
    Analyzes sensor data and generates faults/recommendations.

    If the request asks for JSON (the dashboard's diagnostics modal does),
    the analysis is kicked off on a background thread and a job_id is
    returned immediately - see /api/reasoner/status/<job_id>. This is what
    fixes the previous freeze: the HTTP request no longer blocks on the
    (potentially slow) Pellet reasoner at all.

    Any other caller (JS disabled, direct link, GET) gets the original
    synchronous behavior for backward compatibility - it's simply much
    faster now since app/ontology_reasoner.py batches Pellet into a single
    call instead of one per sensor reading.
    """
    company_id = get_current_company_id()
    with get_db() as conn:
        if not conn.execute(
            'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
            (aircraft_id, company_id)
        ).fetchone():
            return jsonify({'status': 'error', 'message': 'Unknown aircraft'}), 404

    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )

    if request.method == 'POST' and wants_json:
        job_id = start_diagnostic_job(aircraft_id, company_id)
        return jsonify({'status': 'started', 'job_id': job_id})

    # Synchronous fallback (no-JS / direct navigation)
    run_fleet_analysis(aircraft_id, company_id=company_id)
    return redirect(url_for('dashboard.dashboard', tail=aircraft_id))


@bp.route('/api/reasoner/status/<job_id>')
def reasoner_job_status(job_id):
    """Polled by the dashboard's diagnostics modal to know when to navigate."""
    job = get_job_status(job_id)
    if not job:
        return jsonify({'status': 'unknown'}), 404
    return jsonify(job)


@bp.route('/xai_reasoner')
def xai_reasoner():
    """Display XAI reasoning logs and AI decision history."""
    company_id = get_current_company_id()
    with get_db() as conn:
        try:
            logs = conn.execute(
                'SELECT rowid, * FROM XAILogs WHERE company_id = ? ORDER BY rowid DESC',
                (company_id,)
            ).fetchall()
        except Exception:
            logs = []
    
    return render_template('xai_reasoner.html', logs=logs)
