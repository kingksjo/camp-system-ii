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
from app.license_compliance import check_fault_signoff
from app.camp_extensions import imdf

bp = Blueprint('fault_resolution', __name__)


@bp.route('/resolve_fault/<int:fault_id>', methods=['POST'])
def resolve_fault(fault_id):
    """
    Resolve a fault with ontology compliance check and digital signature.
    
    Args:
        fault_id: ID of the fault to resolve
    """
    mechanic_id = request.form.get('mechanic_id')
    component_replaced = request.form.get('component_replaced') == '1'
    installed_part_serial = request.form.get('installed_part_serial') or None

    with get_db() as conn:
        fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (fault_id,)).fetchone()
        mechanic = conn.execute('SELECT * FROM Engineers WHERE emp_id = ?', (mechanic_id,)).fetchone()
        
        if not fault or not mechanic:
            return "Error: Fault or Mechanic not found.", 400

        # Integrated Maintenance Documentation Framework (IMDF) gate:
        # "The system should reject installation if mandatory traceability
        # information is missing." Evidence is always required; a verified,
        # EASA-Form-1-referenced replacement part is required only when the
        # engineer indicates an actual component swap took place.
        ready, missing = imdf.documentation_readiness(fault_id, component_replaced, installed_part_serial)
        if not ready:
            missing_html = "".join(f"<li>{item}</li>" for item in missing)
            return (
                f"<h1>DOCUMENTATION INCOMPLETE</h1>"
                f"<p>This Work Order cannot be released for signature until the following is on file:</p>"
                f"<ul>{missing_html}</ul>"
                f"<a href='/work-order/{fault_id}'>Return to Work Order Documentation</a>"
            ), 403
        
        # 1. AI Ontology Compliance Check
        required_license = "None"
        amm_chapter = (fault['amm_reference'] or '').split(" ")[0]
        try:
            onto_path.append(".")
            base_onto = get_ontology("camp.owl").load()
            onto = get_ontology("camp_multi_ontology.owl").load()
            
            with onto:
                if hasattr(onto, amm_chapter):
                    chapter_class = getattr(onto, amm_chapter)
                    if chapter_class is not None and hasattr(chapter_class, 'requiresLicense'):
                        if chapter_class.requiresLicense:
                            required_license = chapter_class.requiresLicense[0].name
        except Exception:
            # Ontology not available or error - continue to the deterministic fallback below
            pass
        
        # 2. Check license compliance - the ontology result takes precedence,
        # but if it couldn't produce a definitive answer (ontology unavailable,
        # or this ATA chapter has no requiresLicense assertion), fall back to
        # the deterministic ATA-chapter -> license table instead of silently
        # allowing the sign-off through (this was the cross sign-off gap).
        mechanic_license = mechanic['license_type']
        allowed, required_set = check_fault_signoff(
            mechanic_license, fault['amm_reference'],
            ontology_required_license=(required_license if required_license != "None" else None)
        )
        if not allowed:
            required_display = required_license if required_license != "None" else " or ".join(sorted(required_set))
            return (
                f"<h1>COMPLIANCE LOCKOUT</h1>"
                f"<p>Signing off on <b>{amm_chapter}</b> requires a <b>{required_display}</b> license. "
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
        # (bug fix: PilotReports' primary key column is `report_id`, not `id` -
        # the previous `WHERE id = ?` always raised sqlite3.OperationalError,
        # which the bare except below silently swallowed, so pilot
        # discrepancies never actually closed even once their fault was
        # resolved. This is what report #6 was describing.)
        if fault['amm_reference'].startswith("PIREP_ID_"):
            exact_pirep_id = fault['amm_reference'].split("_")[-1]
            try:
                conn.execute(
                    "UPDATE PilotReports SET status = 'Closed' WHERE report_id = ?",
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
        
        # 7. Generate Certificate of Release to Service (CRS) - upgraded per
        # the Integrated Maintenance Documentation Framework: it now
        # references the Work Order, the removed component (if any), the
        # verified installed replacement (if any), and the aircraft's
        # evidence chain, instead of being a bare sign-off line.
        work_order_number = imdf.get_work_order_number(fault_id, fault['detected_time'])
        ata_chapter = (fault['amm_reference'] or '').split(' ')[0]

        removed_part_serial = None
        if component_replaced:
            removed_row = conn.execute(
                "SELECT part_serial FROM PartRecords WHERE component_id = ? AND status = 'Removed' "
                "ORDER BY removed_date DESC LIMIT 1",
                (fault['component_id'],)
            ).fetchone()
            removed_part_serial = removed_row['part_serial'] if removed_row else None

            if installed_part_serial:
                conn.execute(
                    "UPDATE PartRecords SET status = 'In Service', installed_date = datetime('now','localtime') "
                    "WHERE part_serial = ?",
                    (installed_part_serial,)
                )
                if removed_part_serial:
                    conn.execute(
                        "UPDATE PartRecords SET replaced_by_serial = ? WHERE part_serial = ?",
                        (installed_part_serial, removed_part_serial)
                    )

        crs_cursor = conn.execute('''
            INSERT INTO CRS_Records
                (aircraft_reg, aircraft_id, reference_id, description, signed_off_by,
                 work_order_number, ata_chapter, component_replaced,
                 removed_part_serial, installed_part_serial, evidence_chain_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ac_reg, comp['aircraft_id'] if comp else None, f"FAULT-{fault_id}", f"Cleared {fault['fault_type']}", digital_signature,
              work_order_number, ata_chapter, 1 if component_replaced else 0,
              removed_part_serial, installed_part_serial, comp['aircraft_id'] if comp else None))
        crs_id = crs_cursor.lastrowid
        
        # 8. Log to maintenance history
        log_maintenance_action(ac_reg, f"Resolved Fault: {fault['fault_type']}", digital_signature, conn=conn)
        
        # 9. Simulate digital twin sensor reset
        _update_sensor_readings(conn, fault)
        
        conn.commit()
        target_tail = comp['aircraft_id'] if comp else None

    # Generated after commit (not inside the transaction above) so this
    # PDF-writer's own database connection can actually see the CRS row
    # it's reading.
    try:
        from app.camp_extensions.maintenance_documents import generate_document
        generate_document('crs', crs_id)
    except Exception as e:
        # Document generation should never block the sign-off itself
        print(f"⚠️ Could not generate CRS PDF: {e}")
    
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
    elif "Leak" in fault['fault_type'] or "Pressure" in fault['fault_type'] and "Oil" not in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Fuel Pressure Sensor', 45.0)",
            (fault['component_id'],)
        )
    elif "Oil" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Oil Pressure Sensor', 55.0)",
            (fault['component_id'],)
        )
    elif "Overstrain" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Strain Gauge', 1.0)",
            (fault['component_id'],)
        )
    elif "Fuel_Temp" in fault['fault_type'] or "FuelTemp" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Fuel Temperature Sensor', 15.0)",
            (fault['component_id'],)
        )
