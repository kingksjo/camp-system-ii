"""
Minimum Equipment List (MEL) and deferral management routes for C.O.R.E. CAMP.
Tracks deferred maintenance items by category.

Feature #5: deferrals can now reference an item on the Master Minimum
Equipment List (input via the CAMO workspace, see app/routes/workspace.py)
instead of a free-typed description + category. Manual/ad-hoc entries are
still supported for anything not yet in the master list.
"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action
from app.license_compliance import check_fault_signoff
from app.auth import get_current_company_id

bp = Blueprint('mel', __name__)

DEFAULT_CATEGORY_LIMITS = {'A': 0, 'B': 3, 'C': 10, 'D': 120}


def _ensure_mmel_link_column(conn):
    """Compatibility helper - MEL_Deferrals.mmel_id is applied by the
    versioned migrations (app/migrations.py). The connection argument is
    accepted for call-site compatibility; migrations run centrally."""
    from app.migrations import run_migrations
    run_migrations()


@bp.route('/mel', methods=['GET', 'POST'])
def mel():
    """Display MEL deferrals and manage deferral tracking."""
    with get_db() as conn:
        _ensure_mmel_link_column(conn)
        conn.commit()

    if request.method == 'POST':
        mmel_id = request.form.get('mmel_id') or None
        company_id = get_current_company_id()
        with get_db() as conn:
            aircraft = conn.execute(
                'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
                (request.form['aircraft_id'], company_id)
            ).fetchone()
            if not aircraft:
                flash('Unknown aircraft - deferral not recorded.', 'error')
                return redirect(url_for('mel.mel'))

            if mmel_id:
                mmel_item = conn.execute(
                    'SELECT 1 FROM MasterMEL WHERE mmel_id = ? AND company_id = ?',
                    (mmel_id, company_id)
                ).fetchone()
                if not mmel_item:
                    flash('Selected MMEL item no longer exists - deferral not recorded.', 'error')
                    return redirect(url_for('mel.mel'))

            conn.execute(
                'INSERT INTO MEL_Deferrals (aircraft_id, item_description, mel_category, date_deferred, mmel_id, company_id) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    request.form['aircraft_id'],
                    request.form['item_description'],
                    request.form['mel_category'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    mmel_id,
                    company_id,
                )
            )
            conn.commit()
        return redirect(url_for('mel.mel'))
    
    company_id = get_current_company_id()
    with get_db() as conn:
        fleet = conn.execute(
            'SELECT * FROM Aircraft WHERE company_id = ?', (company_id,)
        ).fetchall()
        raw_deferrals = conn.execute(
            'SELECT * FROM MEL_Deferrals WHERE company_id = ? AND status = "Active"',
            (company_id,)
        ).fetchall()

        mmel_items = conn.execute(
            'SELECT * FROM MasterMEL WHERE company_id = ? ORDER BY target_model, item_description',
            (company_id,)
        ).fetchall()
        
        try:
            engineers = conn.execute(
                "SELECT * FROM Engineers WHERE company_id = ?", (company_id,)
            ).fetchall()
        except Exception:
            engineers = []
    
    deferrals = []
    
    for d in raw_deferrals:
        days_deferred = (datetime.now() - datetime.strptime(d['date_deferred'], '%Y-%m-%d %H:%M:%S')).days

        # An MMEL-referenced deferral uses that item's own max_deferral_days
        # (set by CAMO in the workspace); a manual/ad-hoc entry falls back to
        # the generic A/B/C/D category limits, same as before.
        mmel_ref = None
        limit = DEFAULT_CATEGORY_LIMITS.get(d['mel_category'], 0)
        if 'mmel_id' in d.keys() and d['mmel_id']:
            with get_db() as conn:
                mmel_ref = conn.execute(
                    'SELECT * FROM MasterMEL WHERE mmel_id = ? AND company_id = ?',
                    (d['mmel_id'], company_id)
                ).fetchone()
            if mmel_ref:
                limit = mmel_ref['max_deferral_days']

        days_remaining = limit - days_deferred
        
        deferrals.append({
            'deferral_id': d['deferral_id'],
            'aircraft_id': d['aircraft_id'],
            'item_description': d['item_description'],
            'mel_category': d['mel_category'],
            'days_remaining': days_remaining,
            'mmel_referenced': mmel_ref is not None,
        })
    
    # Sort by urgency (expiring soonest first)
    deferrals.sort(key=lambda x: x['days_remaining'])
    
    return render_template('mel.html', fleet=fleet, deferrals=deferrals, engineers=engineers, mmel_items=mmel_items)


@bp.route('/api/mmel/by-model/<model>')
def api_mmel_by_model(model):
    """Used by the MEL Tracker's 'Reference MMEL Item' dropdown to filter by the selected aircraft's model."""
    with get_db() as conn:
        items = conn.execute(
            'SELECT * FROM MasterMEL WHERE target_model = ? AND company_id = ? ORDER BY item_description',
            (model, get_current_company_id())
        ).fetchall()
    return jsonify([dict(i) for i in items])


@bp.route('/resolve_mel/<int:deferral_id>', methods=['POST'])
def resolve_mel(deferral_id):
    """Clear a MEL deferral (license-gated by ATA chapter when the deferral references an MMEL item)."""
    emp_id = request.form.get('engineer_id')
    company_id = get_current_company_id()
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name, license_number, stamp_number, license_type FROM Engineers '
            'WHERE emp_id = ? AND company_id = ?',
            (emp_id, company_id)
        ).fetchone()
        
        deferral = conn.execute(
            'SELECT * FROM MEL_Deferrals WHERE deferral_id = ? AND company_id = ?',
            (deferral_id, company_id)
        ).fetchone()
        
        if engineer and deferral:
            # If this deferral references a Master MEL item with a known ATA
            # chapter, gate the clearance sign-off the same way fault
            # sign-off is gated (feature #3) - ad-hoc entries with no chapter
            # classification are allowed through, same as before.
            if 'mmel_id' in deferral.keys() and deferral['mmel_id']:
                mmel_ref = conn.execute(
                    'SELECT * FROM MasterMEL WHERE mmel_id = ? AND company_id = ?',
                    (deferral['mmel_id'], company_id)
                ).fetchone()
                if mmel_ref and mmel_ref['ata_chapter']:
                    allowed, required = check_fault_signoff(engineer['license_type'], mmel_ref['ata_chapter'])
                    if not allowed:
                        required_display = " or ".join(sorted(required)) if required else "an authorized"
                        return (
                            f"<h1>COMPLIANCE LOCKOUT</h1>"
                            f"<p>Clearing this MEL item ({mmel_ref['ata_chapter']}) requires {required_display} license. "
                            f"You hold a {engineer['license_type']}.</p>"
                            f"<a href='/mel'>Return to MEL Tracker</a>"
                        ), 403

            digital_signature = create_digital_signature(engineer)
            task_desc = f"Cleared MEL Deferral: {deferral['item_description']}"
            aircraft_reg = deferral['aircraft_id'].replace('Aircraft_', '')
            
            log_maintenance_action(
                aircraft_reg, task_desc, digital_signature,
                conn=conn, company_id=company_id,
            )
            
            conn.execute(
                'UPDATE MEL_Deferrals SET status = "Resolved" WHERE deferral_id = ? AND company_id = ?',
                (deferral_id, company_id)
            )
        
        conn.commit()
    
    return redirect(url_for('mel.mel'))
