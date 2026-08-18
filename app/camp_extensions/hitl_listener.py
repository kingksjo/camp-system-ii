"""
Hardware-in-the-Loop (HITL) FlightGear UDP Telemetry Bridge for C.O.R.E. CAMP.

Completes Feature #4 from the gap analysis: a real UDP listener that accepts
FlightGear "generic protocol" telemetry packets and feeds them straight into
the existing SensorTelemetry table - the same table telemetry.py, the
ontology reasoner, and the dashboard already read from. No changes are made
to any of those files; this module is a pure producer of rows they already
know how to consume.

Wire protocol
-------------
FlightGear's --generic=socket,out,<hz>,<host>,<port>,udp,camp_export protocol
is configured with the shipped XML descriptor (see get_fg_protocol_xml) to
emit ASCII lines of the form:

    <component_id>,<sensor_type>,<reading_value>\n

one sensor reading per UDP datagram (FlightGear supports multiple lines per
packet too - both are handled). This keeps the parser trivial and lets any
other HIL rig (an actual test bench, a hardware simulator, a bench-top rig
harness) drive the same digital twin simply by sending that 3-field CSV line
over UDP - FlightGear is one possible source, not a hard requirement.
"""
import socket
import threading
import time
from datetime import datetime

from app.database import get_db
# Round-3: single source of truth for sensor types now lives in
# app/routes/telemetry.py's SENSOR_TYPE_REGISTRY - import the keys rather
# than keeping a second, driftable hardcoded list here.
from app.routes.telemetry import SENSOR_TYPE_REGISTRY


def ensure_hitl_schema():
    """Compatibility wrapper - HITL tables are created by the versioned
    migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


class _HITLListenerState:
    """Module-level singleton tracking the background UDP thread (single Flask process demo)."""
    def __init__(self):
        self.thread = None
        self.sock = None
        self.stop_event = threading.Event()
        self.running = False
        self.port = 5599
        self.packets_received = 0
        self.last_packet_at = None
        self.last_error = None


_state = _HITLListenerState()

VALID_SENSOR_TYPES = set(SENSOR_TYPE_REGISTRY.keys())


def _handle_line(conn, line, default_aircraft_id):
    """Parse one CSV line and persist it as a SensorTelemetry reading."""
    line = line.strip()
    if not line:
        return None

    parts = [p.strip() for p in line.split(',')]
    status = 'Accepted'

    try:
        if len(parts) == 3:
            component_id, sensor_type, value_str = parts
        elif len(parts) == 2:
            # Allow a shorthand: "sensor_type,value" -> falls onto the default aircraft's component
            sensor_type, value_str = parts
            component_id = f"Engine_{default_aircraft_id}" if default_aircraft_id else None
        else:
            raise ValueError(f"Expected 2 or 3 CSV fields, got {len(parts)}")

        if not component_id:
            raise ValueError("No component_id resolvable (no default aircraft configured)")

        if sensor_type not in VALID_SENSOR_TYPES:
            status = 'Accepted-UnknownSensorType'

        reading_value = float(value_str)

        # Auto-create the component if the HIL rig references one that doesn't exist yet,
        # mirroring _get_or_create_components()'s pattern in telemetry.py.
        exists = conn.execute(
            'SELECT 1 FROM Components WHERE component_id = ?', (component_id,)
        ).fetchone()
        if not exists and default_aircraft_id:
            conn.execute(
                'INSERT INTO Components (component_id, aircraft_id, component_type) VALUES (?, ?, ?)',
                (component_id, default_aircraft_id, 'HIL-Rig')
            )

        conn.execute(
            'INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, ?, ?)',
            (component_id, sensor_type, reading_value)
        )
        conn.execute(
            'INSERT INTO HITLPacketLog (raw_payload, component_id, sensor_type, reading_value, status) '
            'VALUES (?, ?, ?, ?, ?)',
            (line, component_id, sensor_type, reading_value, status)
        )
        return status

    except Exception as e:
        conn.execute(
            'INSERT INTO HITLPacketLog (raw_payload, status) VALUES (?, ?)',
            (line, f'Rejected: {e}')
        )
        return f'Rejected: {e}'


def _listener_loop(port, default_aircraft_id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)
    try:
        sock.bind(('0.0.0.0', port))
    except OSError as e:
        _state.last_error = str(e)
        _state.running = False
        return

    _state.sock = sock
    _state.running = True

    while not _state.stop_event.is_set():
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            text = data.decode('utf-8', errors='ignore')
        except Exception:
            continue

        with get_db() as conn:
            for line in text.splitlines():
                _handle_line(conn, line, default_aircraft_id)
            conn.commit()

        _state.packets_received += 1
        _state.last_packet_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    sock.close()
    _state.running = False


def start_listener(port, default_aircraft_id):
    """Start the UDP listener thread if not already running."""
    if _state.running:
        return False, "Listener already running."

    ensure_hitl_schema()
    _state.stop_event.clear()
    _state.port = port
    _state.packets_received = 0
    _state.last_error = None
    _state.thread = threading.Thread(
        target=_listener_loop, args=(port, default_aircraft_id), daemon=True
    )
    _state.thread.start()
    time.sleep(0.2)  # let bind() fail fast if the port is taken

    with get_db() as conn:
        conn.execute(
            "UPDATE HITLListenerConfig SET port = ?, default_aircraft_id = ?, last_started = datetime('now','localtime') WHERE id = 1",
            (port, default_aircraft_id)
        )
        conn.commit()

    if _state.last_error:
        return False, f"Failed to bind UDP port {port}: {_state.last_error}"
    return True, f"HITL listener is live on UDP port {port}."


def stop_listener():
    if not _state.running:
        return False, "Listener is not running."
    _state.stop_event.set()
    if _state.thread:
        _state.thread.join(timeout=2.0)
    with get_db() as conn:
        conn.execute("UPDATE HITLListenerConfig SET last_stopped = datetime('now','localtime') WHERE id = 1")
        conn.commit()
    return True, "HITL listener stopped."


def get_status():
    return {
        'running': _state.running,
        'port': _state.port,
        'packets_received': _state.packets_received,
        'last_packet_at': _state.last_packet_at,
        'last_error': _state.last_error,
    }


FG_PROTOCOL_XML = """<?xml version="1.0"?>
<!--
  C.O.R.E. CAMP FlightGear Generic Protocol export descriptor.
  Launch FlightGear with, e.g.:
    --generic=socket,out,5,127.0.0.1,5599,udp,camp_export
  (drop this file in $FG_ROOT/Protocol/camp_export.xml)
-->
<PropertyList>
  <generic>
    <output>
      <line_separator>newline</line_separator>
      <var_separator>,</var_separator>
      <chunk>
        <name>component_id</name>
        <type>string</type>
        <format>Engine_Aircraft_5N_TAJ</format>
        <node>/sim/camp/component-id</node>
      </chunk>
      <chunk>
        <name>sensor_type</name>
        <type>string</type>
        <format>Thermocouple</format>
        <node>/sim/camp/sensor-type</node>
      </chunk>
      <chunk>
        <name>reading_value</name>
        <type>double</type>
        <node>/engines/engine[0]/egt-degf</node>
      </chunk>
    </output>
  </generic>
</PropertyList>
"""
