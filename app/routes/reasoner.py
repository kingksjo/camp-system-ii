"""
AI Ontology Reasoner routes for C.O.R.E. CAMP (XAI - eXplainable AI).
Handles automated fault detection and reasoning engine execution.
"""
from flask import Blueprint, render_template, redirect, url_for
from app.database import get_db
from app.ontology_reasoner import run_fleet_analysis

bp = Blueprint('reasoner', __name__)


@bp.route('/run_reasoner/<aircraft_id>', methods=['GET', 'POST'])
def run_reasoner(aircraft_id):
    """
    Run the Pellet ontology reasoner on aircraft telemetry.
    Analyzes sensor data and generates faults/recommendations.
    """
    # Run full fleet analysis
    results = run_fleet_analysis(aircraft_id)
    
    return redirect(url_for('dashboard.dashboard', tail=aircraft_id))


@bp.route('/xai_reasoner')
def xai_reasoner():
    """Display XAI reasoning logs and AI decision history."""
    with get_db() as conn:
        try:
            logs = conn.execute('SELECT rowid, * FROM XAILogs ORDER BY rowid DESC').fetchall()
        except Exception:
            logs = []
    
    return render_template('xai_reasoner.html', logs=logs)
