"""
Minimum Equipment List (MEL) and deferral management routes for C.O.R.E. CAMP.
Tracks deferred maintenance items by category.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from datetime import datetime
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action

bp = Blueprint('mel', __name__)


@bp.route('/mel', methods=['GET', 'POST'])
def mel():
    """Display MEL deferrals and manage deferral tracking."""
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                'INSERT INTO MEL_Deferrals (aircraft_id, item_description, mel_category, date_deferred) '
                'VALUES (?, ?, ?, ?)',
                (
                    request.form['aircraft_id'],
                    request.form['item_description'],
                    request.form['mel_category'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                )
            )
            conn.commit()
        return redirect(url_for('mel.mel'))
    
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        raw_deferrals = conn.execute(
            'SELECT * FROM MEL_Deferrals WHERE status = "Active"'
        ).fetchall()
        
        try:
            engineers = conn.execute("SELECT * FROM Engineers").fetchall()
        except Exception:
            engineers = []
    
    # Calculate days remaining by category
    category_limits = {'B': 3, 'C': 10, 'D': 120}
    deferrals = []
    
    for d in raw_deferrals:
        days_deferred = (datetime.now() - datetime.strptime(d['date_deferred'], '%Y-%m-%d %H:%M:%S')).days
        days_remaining = category_limits.get(d['mel_category'], 0) - days_deferred
        
        deferrals.append({
            'deferral_id': d['deferral_id'],
            'aircraft_id': d['aircraft_id'],
            'item_description': d['item_description'],
            'mel_category': d['mel_category'],
            'days_remaining': days_remaining
        })
    
    # Sort by urgency (expiring soonest first)
    deferrals.sort(key=lambda x: x['days_remaining'])
    
    return render_template('mel.html', fleet=fleet, deferrals=deferrals, engineers=engineers)


@bp.route('/resolve_mel/<int:deferral_id>', methods=['POST'])
def resolve_mel(deferral_id):
    """Clear a MEL deferral."""
    emp_id = request.form.get('engineer_id')
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?',
            (emp_id,)
        ).fetchone()
        
        deferral = conn.execute(
            'SELECT aircraft_id, item_description FROM MEL_Deferrals WHERE deferral_id = ?',
            (deferral_id,)
        ).fetchone()
        
        if engineer and deferral:
            digital_signature = create_digital_signature(engineer)
            task_desc = f"Cleared MEL Deferral: {deferral['item_description']}"
            aircraft_reg = deferral['aircraft_id'].replace('Aircraft_', '')
            
            log_maintenance_action(aircraft_reg, task_desc, digital_signature, conn=conn)
            
            conn.execute(
                'UPDATE MEL_Deferrals SET status = "Resolved" WHERE deferral_id = ?',
                (deferral_id,)
            )
        
        conn.commit()
    
    return redirect(url_for('mel.mel'))
