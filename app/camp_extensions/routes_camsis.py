"""Routes for the CAMSIS-3 deterministic grounding dashboard (Feature #12)."""
from flask import Blueprint, render_template, request
from app.database import get_db
from app.camp_extensions import camsis

bp = Blueprint('camsis', __name__)


@bp.route('/camsis')
def camsis_page():
    camsis.ensure_camsis_schema()
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()

    selected_tail = request.args.get('tail')
    results = camsis.compute_grounding(aircraft_id=selected_tail)
    results.sort(key=lambda r: r['margin_pct'])

    overdue = [r for r in results if r['status'] == 'Overdue']
    due_soon = [r for r in results if r['status'] == 'Due Soon']

    return render_template(
        'extensions/camsis.html',
        fleet=fleet, selected_tail=selected_tail,
        results=results, overdue=overdue, due_soon=due_soon,
    )
