"""Routes for RFID/QR part serial scanning and EASA Form 1 traceability (Feature #19).

The standalone /parts page has been merged into the Integrated Maintenance
Documentation Framework (see app/camp_extensions/routes_imdf.py) so parts
traceability is captured in context of a specific Work Order. /parts now
redirects there, same pattern already used for /schedule/fullcalendar and
/killswitch in Round 3. The register/scan endpoints are unchanged.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app.database import get_db
from app.camp_extensions import parts_traceability as parts

bp = Blueprint('parts', __name__)


@bp.route('/parts')
def parts_page():
    return redirect(url_for('imdf.work_orders_index'))


@bp.route('/parts/register', methods=['POST'])
def parts_register():
    try:
        part_serial = parts.register_part(
            part_name=request.form.get('part_name'),
            ata_chapter=request.form.get('ata_chapter'),
            component_id=request.form.get('component_id') or None,
            aircraft_id=request.form.get('aircraft_id') or None,
            easa_form1_ref=request.form.get('easa_form1_ref'),
            manufactured_date=request.form.get('manufactured_date'),
        )
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('imdf.work_orders_index'))

    fault_id = request.form.get('fault_id')
    if fault_id:
        return redirect(url_for('imdf.work_order_detail', fault_id=fault_id, new_part=part_serial))
    return redirect(url_for('imdf.work_orders_index'))


@bp.route('/api/parts/scan', methods=['POST'])
def api_scan_part():
    data = request.get_json(silent=True) or request.form
    part_serial = (data.get('part_serial') or '').strip()
    scan_type = data.get('scan_type', 'Manual')
    scanned_by = data.get('scanned_by', 'Unknown')

    if not part_serial:
        return jsonify({'status': 'error', 'message': 'part_serial is required'}), 400

    result = parts.scan_part(part_serial, scan_type, scanned_by)
    return jsonify({'status': 'ok', **result})


@bp.route('/api/parts/<part_serial>')
def api_get_part(part_serial):
    with get_db() as conn:
        part = conn.execute('SELECT * FROM PartRecords WHERE part_serial = ?', (part_serial,)).fetchone()
    if not part:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return jsonify(dict(part))
