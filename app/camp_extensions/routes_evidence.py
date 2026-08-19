"""Routes for the digital evidence locker (Feature #11).

The standalone /evidence page has been merged into the Integrated
Maintenance Documentation Framework (see app/camp_extensions/routes_imdf.py)
so evidence is captured in context of a specific Work Order instead of as
a disconnected page. /evidence now redirects there, same pattern already
used for /schedule/fullcalendar and /killswitch in Round 3. The upload
endpoint and verify API are unchanged and are what the merged page's forms
actually submit to.
"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from app.database import get_db
from app.camp_extensions import digital_evidence as eviq
from app.auth import get_current_company_id

bp = Blueprint('evidence', __name__)


@bp.route('/evidence')
def evidence_page():
    return redirect(url_for('imdf.work_orders_index'))


@bp.route('/evidence/upload', methods=['POST'])
def evidence_upload():
    aircraft_id = request.form.get('aircraft_id')
    fault_id = request.form.get('fault_id') or None
    component_id = request.form.get('component_id') or None
    uploaded_by = request.form.get('uploaded_by', 'Unknown')
    manual_lat = request.form.get('latitude') or None
    manual_lon = request.form.get('longitude') or None
    captured_at_client = request.form.get('captured_at') or None
    notes = request.form.get('notes', '')

    company_id = get_current_company_id()
    file_storage = request.files.get('evidence_file')
    try:
        eviq.store_evidence(
            file_storage, aircraft_id, fault_id, component_id, uploaded_by,
            manual_lat=manual_lat, manual_lon=manual_lon,
            captured_at_client=captured_at_client, notes=notes,
            company_id=company_id,
        )
    except ValueError as e:
        return str(e), 400

    if fault_id:
        return redirect(url_for('imdf.work_order_detail', fault_id=fault_id))
    return redirect(url_for('imdf.work_orders_index'))


@bp.route('/api/evidence/<aircraft_id>/verify')
def api_verify_chain(aircraft_id):
    return jsonify(eviq.verify_chain(aircraft_id, company_id=get_current_company_id()))
