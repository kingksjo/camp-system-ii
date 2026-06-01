"""
Fault resolution routes for C.O.R.E. CAMP.
Handles ontology-driven fault resolution and digital sign-offs.
"""
from flask import Blueprint, request, redirect, url_for
from datetime import datetime
from owlready2 import get_ontology, onto_path
from app.database import get_db
from app.utils import create_digital_signature
from app.cbr_engine import log_maintenance_action

bp = Blueprint('fault_resolution', __name__)


@bp.route('/resolve_fault/<int:fault_id>', methods=['POST'])
def resolve_fault(fault_id):
    """
    Resolve a fault with ontology compliance check and digital signature.
    
    Args:
        fault_id: ID of the fault to resolve
    """
    mechanic_id = request.form.get('mechanic_id')
    
    with get_db() as conn:
        fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (fault_id,)).fetchone()
        mechanic = conn.execute('SELECT * FROM Engineers WHERE emp_id = ?', (mechanic_id,)).fetchone()
        
        if not fault or not mechanic:
            return "Error: Fault or Mechanic not found.", 400
        
        # 1. AI Ontology Compliance Check
        required_license = "None"
        try:
            onto_path.append(".")
            base_onto = get_ontology("camp.owl").load()
            onto = get_ontology("camp_multi_ontology.owl").load()
            
            amm_chapter = fault['amm_reference'].split(" ")[0]
            
            with onto:
                if hasattr(onto, amm_chapter):
                    chapter_class = getattr(onto, amm_chapter)
                    if chapter_class is not None and hasattr(chapter_class, 'requiresLicense'):
                        if chapter_class.requiresLicense:
                            required_license = chapter_class.requiresLicense[0].name
        except Exception:
            # Ontology not available or error - continue without compliance check
            pass
        
        # 2. Check license compliance
        mechanic_license = mechanic['license_type']
        if required_license != "None" and required_license != mechanic_license:
            return (
                f"<h1>COMPLIANCE LOCKOUT</h1>"
                f"<p>The ontology requires a <b>{required_license}</b> to sign off on {amm_chapter}. "
                f"You hold a {mechanic_license}.</p>"
                f"<a href='/'>Return to Dashboard</a>"
            ), 403
        
        # 3. Create digital signature
        digital_signature = create_digital_signature(mechanic)
        
        # 4. Mark fault as resolved
        conn.execute('''
            UPDATE Faults 
            SET resolved = 1, resolved_by = ?, resolved_date = datetime('now', 'localtime') 
            WHERE fault_id = ?
        ''', (digital_signature, fault_id))
        
        # 5. Auto-close PIREP if applicable
        if fault['amm_reference'].startswith("PIREP_ID_"):
            exact_pirep_id = fault['amm_reference'].split("_")[-1]
            try:
                conn.execute(
                    "UPDATE PilotReports SET status = 'Closed' WHERE id = ?",
                    (exact_pirep_id,)
                )
            except Exception:
                pass
        
        # 6. Get aircraft registration
        comp = conn.execute(
            'SELECT aircraft_id FROM Components WHERE component_id = ?',
            (fault['component_id'],)
        ).fetchone()
        
        ac_reg = comp['aircraft_id'].replace('Aircraft_', '') if comp else "UNKNOWN"
        
        # 7. Generate Certificate of Release to Service (CRS)
        conn.execute('''
            INSERT INTO CRS_Records (aircraft_reg, reference_id, description, signed_off_by) 
            VALUES (?, ?, ?, ?)
        ''', (ac_reg, f"FAULT-{fault_id}", f"Cleared {fault['fault_type']}", digital_signature))
        
        # 8. Log to maintenance history
        log_maintenance_action(ac_reg, f"Resolved Fault: {fault['fault_type']}", digital_signature, conn=conn)
        
        # 9. Simulate digital twin sensor reset
        _update_sensor_readings(conn, fault)
        
        conn.commit()
        target_tail = comp['aircraft_id'] if comp else None
    
    return redirect(url_for('dashboard.dashboard', tail=target_tail))


def _update_sensor_readings(conn, fault):
    """Update sensor readings to simulate completed repair."""
    if "Overheat" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Thermocouple', 450.0)",
            (fault['component_id'],)
        )
    elif "Vibration" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Vibration Sensor', 1.2)",
            (fault['component_id'],)
        )
    elif "Leak" in fault['fault_type'] or "Pressure" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Pressure Sensor', 45.0)",
            (fault['component_id'],)
        )
