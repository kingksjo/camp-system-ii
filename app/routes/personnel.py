"""
Personnel and engineers roster routes for C.O.R.E. CAMP.
Manages licensed AME database and digital signatures.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db

bp = Blueprint('personnel', __name__)


@bp.route('/personnel')
def personnel():
    """Display personnel roster."""
    with get_db() as conn:
        try:
            engineers = conn.execute('SELECT * FROM Engineers').fetchall()
        except Exception:
            engineers = []
    
    return render_template('personnel.html', engineers=engineers)


@bp.route('/add_engineer', methods=['POST'])
def add_engineer():
    """Add a new licensed engineer to the roster."""
    emp_id = request.form['emp_id']
    full_name = request.form['full_name']
    license_type = request.form['license_type']
    license_number = request.form['license_number']
    stamp_number = request.form['stamp_number']
    
    with get_db() as conn:
        try:
            conn.execute(
                'INSERT INTO Engineers (emp_id, full_name, license_type, license_number, stamp_number) '
                'VALUES (?, ?, ?, ?, ?)',
                (emp_id, full_name, license_type, license_number, stamp_number)
            )
            conn.commit()
        except Exception:
            # Duplicate or other constraint error - silently ignore
            pass
    
    return redirect(url_for('personnel.personnel'))
