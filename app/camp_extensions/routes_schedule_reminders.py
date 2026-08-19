"""Routes for time-bound schedule reminders (Feature #2)."""
from flask import Blueprint, jsonify, request
from app.camp_extensions import schedule_lifecycle as lifecycle
from app.auth import get_current_company_id

bp = Blueprint('schedule_reminders', __name__)


@bp.route('/api/schedule/pending-reminders')
def api_pending_reminders():
    return jsonify(lifecycle.get_pending_reminders(company_id=get_current_company_id()))


@bp.route('/api/schedule/reminders/<int:record_id>/ack', methods=['POST'])
def api_ack_reminder(record_id):
    lifecycle.acknowledge_reminder(record_id)
    return jsonify({'status': 'ok'})
