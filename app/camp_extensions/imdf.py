"""
Integrated Maintenance Documentation Framework (IMDF).

Implements the framework described in the head-programmer's systems
analysis: the Evidence Locker, Parts Traceability, and CRS/Maintenance Log
modules already existed but operated independently. This module makes them
stages of one continuous, auditable process anchored on a single Work
Order - which in this codebase is the Fault row the AI ontology reasoner
creates (see app/ontology_reasoner.py: run_fleet_analysis()). That is the
"work order gotten after an inference by the AI" referred to in the
framework doc; app/routes/fault_resolution.py is Stage 7/8 (sign-off + CRS
+ permanent record) and already existed, so this module supplies Stages
1-6 (authorization header, evidence activation, removal/installation
documentation, traceability verification) and enriches Stage 7 (CRS).

Nothing here duplicates the underlying Evidence Locker / Parts Traceability
engines (app/camp_extensions/digital_evidence.py,
app/camp_extensions/parts_traceability.py) - it composes them.
"""
from datetime import datetime
from app.database import get_db
from app.camp_extensions import digital_evidence as evidence_engine
from app.camp_extensions import parts_traceability as parts_engine


def ensure_imdf_schema():
    """Compatibility wrapper - the IMDF additive columns are applied by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def get_work_order_number(fault_id, detected_time=None):
    """
    Deterministic Work Order number in the WO-<year>-<seq> format from the
    framework doc's example (WO-2026-0154) - no new column required, since
    it's fully derivable from the fault's own id/detected_time.
    """
    year = (detected_time or str(datetime.now().year))[:4]
    if not year.isdigit():
        year = str(datetime.now().year)
    return f"WO-{year}-{int(fault_id):04d}"


def get_work_order_context(fault_id):
    """
    Assemble everything the merged Work Order Documentation page needs:
    the authorization header (Stage 1), the aircraft's evidence chain
    scoped to this fault (Stage 2/3/6), and any parts already
    removed/registered against this component (Stage 4/5).
    """
    ensure_imdf_schema()
    evidence_engine.ensure_evidence_schema()
    parts_engine.ensure_parts_schema()

    with get_db() as conn:
        fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (fault_id,)).fetchone()
        if not fault:
            return None

        component = conn.execute(
            'SELECT * FROM Components WHERE component_id = ?', (fault['component_id'],)
        ).fetchone()
        aircraft_id = component['aircraft_id'] if component else None
        aircraft = conn.execute(
            'SELECT * FROM Aircraft WHERE aircraft_id = ?', (aircraft_id,)
        ).fetchone() if aircraft_id else None

        ata_chapter = (fault['amm_reference'] or '').split(' ')[0]

        evidence_records = conn.execute(
            'SELECT * FROM DigitalEvidence WHERE fault_id = ? ORDER BY chain_position DESC',
            (fault_id,)
        ).fetchall()

        removed_parts = conn.execute(
            "SELECT * FROM PartRecords WHERE component_id = ? AND status = 'Removed' "
            "ORDER BY removed_date DESC",
            (fault['component_id'],)
        ).fetchall()

        # Candidate replacement parts: anything registered for this
        # component/aircraft that ISN'T one of the ones just removed.
        removed_serials = [p['part_serial'] for p in removed_parts]
        placeholders = ','.join('?' * len(removed_serials)) if removed_serials else None
        if placeholders:
            installed_candidates = conn.execute(
                f"SELECT * FROM PartRecords WHERE component_id = ? AND status = 'In Service' "
                f"AND part_serial NOT IN ({placeholders}) ORDER BY created_at DESC",
                (fault['component_id'], *removed_serials)
            ).fetchall()
        else:
            installed_candidates = conn.execute(
                "SELECT * FROM PartRecords WHERE component_id = ? AND status = 'In Service' "
                "ORDER BY created_at DESC",
                (fault['component_id'],)
            ).fetchall()

    verification = evidence_engine.verify_chain(aircraft_id) if aircraft_id else None

    return {
        'fault': fault,
        'component': component,
        'aircraft': aircraft,
        'ata_chapter': ata_chapter,
        'work_order_number': get_work_order_number(fault['fault_id'], fault['detected_time']),
        'evidence_records': evidence_records,
        'evidence_verification': verification,
        'removed_parts': removed_parts,
        'installed_candidates': installed_candidates,
    }


def mark_part_removed(part_serial, removal_reason, condition_assessment, fault_code,
                       flight_hours=None, flight_cycles=None, position_on_aircraft=None):
    """Stage 3: document a removed component against its existing PartRecords entry."""
    ensure_imdf_schema()
    with get_db() as conn:
        conn.execute('''
            UPDATE PartRecords
            SET status = 'Removed', removal_reason = ?, condition_assessment = ?,
                fault_code = ?, flight_hours_at_removal = ?, flight_cycles_at_removal = ?,
                position_on_aircraft = COALESCE(?, position_on_aircraft),
                removed_date = datetime('now', 'localtime')
            WHERE part_serial = ?
        ''', (removal_reason, condition_assessment, fault_code, flight_hours, flight_cycles,
              position_on_aircraft, part_serial))
        conn.commit()


def documentation_readiness(fault_id, component_replaced, installed_part_serial=None):
    """
    Stage 4 gate: "The system should reject installation if mandatory
    traceability information is missing." Evidence is always required for
    any sign-off; a verified replacement part is only required when the
    engineer indicates an actual component swap occurred (not every
    maintenance action - e.g. a reset or inspection - involves one).

    Returns (ready: bool, missing: list[str]).
    """
    ensure_imdf_schema()
    missing = []

    with get_db() as conn:
        evidence_count = conn.execute(
            'SELECT COUNT(*) as c FROM DigitalEvidence WHERE fault_id = ?', (fault_id,)
        ).fetchone()['c']
        if evidence_count == 0:
            missing.append("At least one piece of evidence (photo/document) documenting the work performed")

        if component_replaced:
            if not installed_part_serial:
                missing.append("A verified replacement part (register or scan it in the Parts Traceability panel)")
            else:
                part = conn.execute(
                    'SELECT * FROM PartRecords WHERE part_serial = ?', (installed_part_serial,)
                ).fetchone()
                if not part:
                    missing.append(f"Replacement part {installed_part_serial} is not a registered part record")
                elif not part['easa_form1_ref']:
                    missing.append(
                        f"Replacement part {installed_part_serial} has no Certificate of Conformity / "
                        f"EASA Form 1 reference on file"
                    )

    return (len(missing) == 0), missing
