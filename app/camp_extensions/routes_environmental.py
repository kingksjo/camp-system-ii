"""Routes for the Layer 7 environmental stressor dashboard (Feature #20)."""
from flask import Blueprint, render_template, request
from app.database import get_db
from app.camp_extensions import environmental_stressor as env

bp = Blueprint('environmental', __name__)


@bp.route('/environmental')
def environmental_page():
    env.ensure_environmental_schema()
    summary = env.fleet_environmental_summary()
    return render_template('extensions/environmental_stressor.html', summary=summary)


@bp.route('/environmental/update-context', methods=['POST'])
def update_context():
    aircraft_id = request.form['aircraft_id']
    with get_db() as conn:
        conn.execute('''
            UPDATE AircraftEnvironmentContext
            SET ambient_temp_c = ?, humidity_pct = ?, corrosion_category = ?, updated_at = datetime('now','localtime')
            WHERE aircraft_id = ?
        ''', (request.form['ambient_temp_c'], request.form['humidity_pct'],
              request.form['corrosion_category'], aircraft_id))
        conn.commit()
    from flask import redirect, url_for
    return redirect(url_for('environmental.environmental_page'))
