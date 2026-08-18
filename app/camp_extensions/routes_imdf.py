"""
Routes for the Integrated Maintenance Documentation Framework (IMDF).

This is the single merged entry point for Evidence Locker + Parts
Traceability, scoped to a Work Order (an AI-detected Fault). It composes
the existing evidence/parts engines rather than replacing them - the
underlying upload/scan/register endpoints in routes_evidence.py and
routes_parts.py are unchanged and still do the real work.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.camp_extensions import imdf

bp = Blueprint('imdf', __name__)


@bp.route('/work-orders')
def work_orders_index():
    """
    Landing page listing every Work Order (AI-detected Fault) with a link
    into its merged documentation page. This replaces the separate
    top-level Evidence Locker and Part Traceability pages in the sidebar -
    both of those are still fully functional underneath, just reached
    through here now instead of as independent, disconnected pages.
    """
    imdf.ensure_imdf_schema()
    with get_db() as conn:
        rows = conn.execute('''
            SELECT f.fault_id, f.fault_type, f.amm_reference, f.severity, f.resolved,
                   f.detected_time, f.resolved_date, c.component_id, c.aircraft_id
            FROM Faults f
            JOIN Components c ON f.component_id = c.component_id
            ORDER BY f.resolved ASC, f.detected_time DESC
        ''').fetchall()

    work_orders = []
    for r in rows:
        work_orders.append({
            'fault_id': r['fault_id'],
            'work_order_number': imdf.get_work_order_number(r['fault_id'], r['detected_time']),
            'fault_type': r['fault_type'],
            'amm_reference': r['amm_reference'],
            'severity': r['severity'],
            'resolved': r['resolved'],
            'detected_time': r['detected_time'],
            'aircraft_reg': (r['aircraft_id'] or '').replace('Aircraft_', ''),
        })

    return render_template('extensions/imdf_index.html', work_orders=work_orders)


@bp.route('/work-order/<int:fault_id>')
def work_order_detail(fault_id):
    """The merged Evidence Locker + Parts Traceability + Sign-Off page for one Work Order."""
    ctx = imdf.get_work_order_context(fault_id)
    if not ctx:
        return redirect(url_for('imdf.work_orders_index'))

    with get_db() as conn:
        engineers = conn.execute('SELECT * FROM Engineers').fetchall()

    new_part = request.args.get('new_part')

    return render_template('extensions/imdf_work_order.html', ctx=ctx, engineers=engineers, new_part=new_part)


@bp.route('/work-order/<int:fault_id>/mark_removed', methods=['POST'])
def mark_removed(fault_id):
    """Stage 3: document the removed component against an existing part record."""
    imdf.mark_part_removed(
        part_serial=request.form.get('part_serial'),
        removal_reason=request.form.get('removal_reason'),
        condition_assessment=request.form.get('condition_assessment'),
        fault_code=request.form.get('fault_code'),
        flight_hours=request.form.get('flight_hours') or None,
        flight_cycles=request.form.get('flight_cycles') or None,
        position_on_aircraft=request.form.get('position_on_aircraft') or None,
    )
    return redirect(url_for('imdf.work_order_detail', fault_id=fault_id))
