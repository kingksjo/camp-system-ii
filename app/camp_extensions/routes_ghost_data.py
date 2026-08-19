"""Routes for the ghost data elimination console (Feature #13)."""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.camp_extensions import ghost_data as gd
from app.auth import get_current_company_id

bp = Blueprint('ghost_data', __name__)


@bp.route('/ghost-data')
def ghost_data_page():
    gd.ensure_ghost_schema()
    company_id = get_current_company_id()
    findings = gd.scan(apply_fixes=False, company_id=company_id)
    with get_db() as conn:
        recent_actions = conn.execute(
            "SELECT * FROM GhostDataLog WHERE action != 'Flagged' AND company_id = ? ORDER BY id DESC LIMIT 30",
            (company_id,)
        ).fetchall()
    total_ghosts = sum(len(v) for v in findings.values())
    return render_template(
        'extensions/ghost_data.html', findings=findings, total_ghosts=total_ghosts,
        recent_actions=recent_actions,
    )


@bp.route('/ghost-data/clean', methods=['POST'])
def ghost_data_clean():
    gd.scan(apply_fixes=True, company_id=get_current_company_id())
    return redirect(url_for('ghost_data.ghost_data_page'))
