"""Routes for the ghost data elimination console (Feature #13)."""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.camp_extensions import ghost_data as gd

bp = Blueprint('ghost_data', __name__)


@bp.route('/ghost-data')
def ghost_data_page():
    gd.ensure_ghost_schema()
    findings = gd.scan(apply_fixes=False)
    with get_db() as conn:
        recent_actions = conn.execute(
            "SELECT * FROM GhostDataLog WHERE action != 'Flagged' ORDER BY id DESC LIMIT 30"
        ).fetchall()
    total_ghosts = sum(len(v) for v in findings.values())
    return render_template(
        'extensions/ghost_data.html', findings=findings, total_ghosts=total_ghosts,
        recent_actions=recent_actions,
    )


@bp.route('/ghost-data/clean', methods=['POST'])
def ghost_data_clean():
    gd.scan(apply_fixes=True)
    return redirect(url_for('ghost_data.ghost_data_page'))
