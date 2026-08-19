"""Routes for the Layer 7 environmental stressor dashboard (Feature #20)."""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.camp_extensions import environmental_stressor as env
from app.auth import get_current_company_id

bp = Blueprint('environmental', __name__)


@bp.route('/environmental')
def environmental_page():
    env.ensure_environmental_schema()
    summary = env.fleet_environmental_summary()
    return render_template('extensions/environmental_stressor.html', summary=summary)


@bp.route('/environmental/update-context', methods=['POST'])
def update_context():
    aircraft_id = request.form['aircraft_id']
    company_id = get_current_company_id()
    with get_db() as conn:
        owned = conn.execute(
            'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
            (aircraft_id, company_id)
        ).fetchone()
        if not owned:
            return redirect(url_for('environmental.environmental_page'))
        conn.execute('''
            UPDATE AircraftEnvironmentContext
            SET ambient_temp_c = ?, humidity_pct = ?, corrosion_category = ?, updated_at = datetime('now','localtime')
            WHERE aircraft_id = ? AND company_id = ?
        ''', (request.form['ambient_temp_c'], request.form['humidity_pct'],
              request.form['corrosion_category'], aircraft_id, company_id))
        conn.commit()
    return redirect(url_for('environmental.environmental_page'))
