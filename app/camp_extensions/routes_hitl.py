"""Routes for the HITL FlightGear UDP telemetry bridge."""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, Response
from app.database import get_db
from app.camp_extensions import hitl_listener as hitl

bp = Blueprint('hitl', __name__)


@bp.route('/hitl')
def hitl_page():
    hitl.ensure_hitl_schema()
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        cfg = conn.execute('SELECT * FROM HITLListenerConfig WHERE id = 1').fetchone()
        recent_packets = conn.execute(
            'SELECT * FROM HITLPacketLog ORDER BY id DESC LIMIT 25'
        ).fetchall()

    return render_template(
        'extensions/hitl.html',
        fleet=fleet,
        cfg=cfg,
        status=hitl.get_status(),
        recent_packets=recent_packets,
    )


@bp.route('/hitl/start', methods=['POST'])
def hitl_start():
    port = int(request.form.get('port', 5599))
    default_aircraft_id = request.form.get('default_aircraft_id') or None
    ok, message = hitl.start_listener(port, default_aircraft_id)
    return redirect(url_for('hitl.hitl_page', msg=message, ok=int(ok)))


@bp.route('/hitl/stop', methods=['POST'])
def hitl_stop():
    ok, message = hitl.stop_listener()
    return redirect(url_for('hitl.hitl_page', msg=message, ok=int(ok)))


@bp.route('/api/hitl/status')
def api_hitl_status():
    return jsonify(hitl.get_status())


@bp.route('/hitl/flightgear-protocol.xml')
def hitl_fg_protocol():
    """Serve the FlightGear generic-protocol descriptor for download."""
    return Response(
        hitl.FG_PROTOCOL_XML,
        mimetype='application/xml',
        headers={'Content-Disposition': 'attachment; filename=camp_export.xml'}
    )
