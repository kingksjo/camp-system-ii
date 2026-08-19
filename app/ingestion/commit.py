"""
Phase 5: commit layer. This is the ONLY place an extracted row becomes real
data - it only runs when an engineer explicitly approves a PendingExtractions
row (see app/routes/ingestion.py: approve_extraction route). Nothing in
runner.py or the parsers ever calls this directly.
"""
import json
from datetime import datetime
from app.database import get_db

# Which columns of each target table an extraction is allowed to populate.
# Deliberately explicit (not "whatever keys field_data happens to have") so
# a parser bug can't write into a column it shouldn't touch.
COMMITTABLE_COLUMNS = {
    'MasterMEL': ['target_model', 'ata_chapter', 'item_description', 'mmel_category',
                  'max_deferral_days', 'remarks', 'company_id', 'source_document_id'],
    'PartsCatalog': ['part_number', 'nomenclature', 'ata_chapter', 'effectivity',
                      'target_model', 'company_id', 'source_document_id'],
    'WiringReferences': ['circuit_ref', 'component_ref', 'connector', 'bus', 'wire_gauge',
                          'description', 'target_model', 'company_id', 'source_document_id'],
    'ComponentSpecs': ['component_type', 'sensor_type', 'min_threshold', 'max_threshold', 'unit',
                        'calibration_interval_days', 'manufacturer', 'target_model', 'company_id',
                        'source_document_id'],
    'FaultIsolationRules': ['symptom', 'probable_cause', 'corrective_action', 'ata_chapter',
                             'target_model', 'company_id', 'source_document_id'],
    'SystemSchematics': ['system_name', 'ata_chapter', 'description', 'target_model',
                          'company_id', 'source_document_id'],
    'Directives': ['directive_number', 'description', 'ata_chapter', 'target_model',
                   'source_document_id'],
}


def _resolve_target_model(conn, ingestion, company_id):
    """A fleet-wide upload already knows its target_model; a single-tail
    upload needs it looked up from the aircraft it was attached to."""
    if ingestion['target_model']:
        return ingestion['target_model']
    doc = conn.execute(
        'SELECT aircraft_id FROM AircraftDocuments WHERE doc_id = ? AND company_id = ?',
        (ingestion['doc_id'], company_id),
    ).fetchone()
    if not doc:
        return None
    aircraft = conn.execute(
        'SELECT model FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
        (doc['aircraft_id'], company_id),
    ).fetchone()
    return aircraft['model'] if aircraft else None


def approve_extraction(extraction_id, reviewer, edited_field_data=None, company_id=None):
    """
    Approve one PendingExtractions row, optionally with reviewer edits, and
    commit it into its target table. Returns (ok: bool, message: str).

    Phase 5 (DB-01): the extraction must belong to ``company_id``; the
    caller derives it from the authenticated session - never from the row.
    """
    with get_db() as conn:
        extraction = conn.execute(
            'SELECT * FROM PendingExtractions WHERE extraction_id = ? AND company_id = ?',
            (extraction_id, company_id),
        ).fetchone()
        if not extraction:
            return False, "Extraction not found or not owned by your company."
        if extraction['status'] != 'Pending':
            return False, f"Already {extraction['status']}."

        ingestion = conn.execute(
            'SELECT * FROM IngestedDocuments WHERE ingestion_id = ? AND company_id = ?',
            (extraction['ingestion_id'], company_id),
        ).fetchone()
        if not ingestion:
            return False, "Source document not found or not owned by your company."

        field_data = edited_field_data or json.loads(extraction['field_data'])
        target_table = extraction['target_table']
        allowed_columns = COMMITTABLE_COLUMNS.get(target_table)
        if not allowed_columns:
            return False, f"No commit mapping for target table {target_table}."

        row = {k: v for k, v in field_data.items() if k in allowed_columns}
        row['company_id'] = ingestion['company_id'] if 'company_id' in allowed_columns else row.get('company_id')
        if 'target_model' in allowed_columns:
            row['target_model'] = field_data.get('target_model') or _resolve_target_model(conn, ingestion, company_id)
        if 'source_document_id' in allowed_columns:
            row['source_document_id'] = ingestion['doc_id']

        # Directives uses different field names than our generic extraction
        # schema (AD/SB/ICA all land here) - map onto its real columns.
        if target_table == 'Directives':
            row = {
                'directive_number': field_data.get('directive_number') or field_data.get('reference') or 'UNSPECIFIED',
                'description': field_data.get('description') or field_data.get('item_description'),
                'ata_chapter': field_data.get('ata_chapter'),
                'target_model': row.get('target_model'),
                'company_id': ingestion['company_id'],
                'source_document_id': ingestion['doc_id'],
            }

        row = {k: v for k, v in row.items() if v is not None}
        if not row:
            return False, "Nothing to commit - all fields empty."

        columns = ', '.join(row.keys())
        placeholders = ', '.join('?' for _ in row)
        conn.execute(
            f"INSERT INTO {target_table} ({columns}) VALUES ({placeholders})",
            tuple(row.values())
        )

        new_status = 'Approved' if not edited_field_data else 'Edited & Approved'
        # Conditional claim: only one approval may commit a given extraction.
        # A second (racing) request sees rowcount == 0 and rolls back the
        # insert above instead of duplicating the target row (DB-08/Phase 5).
        claimed = conn.execute(
            'UPDATE PendingExtractions SET status = ?, reviewed_by = ?, reviewed_at = ?, field_data = ? '
            'WHERE extraction_id = ? AND status = ?',
            (new_status, reviewer, datetime.now().isoformat(), json.dumps(field_data),
             extraction_id, 'Pending')
        )
        if claimed.rowcount == 0:
            raise RuntimeError("Extraction was already reviewed - no changes committed.")
        conn.execute(
            'INSERT INTO ExtractionAuditLog (extraction_id, action, changed_fields, actor, company_id) '
            'VALUES (?, ?, ?, ?, ?)',
            (extraction_id, new_status,
             json.dumps(edited_field_data) if edited_field_data else None, reviewer, company_id)
        )
        conn.commit()

    return True, f"Committed into {target_table}."


def reject_extraction(extraction_id, reviewer, company_id=None):
    with get_db() as conn:
        extraction = conn.execute(
            'SELECT * FROM PendingExtractions WHERE extraction_id = ? AND company_id = ?',
            (extraction_id, company_id),
        ).fetchone()
        if not extraction or extraction['status'] != 'Pending':
            return False, "Not found, already reviewed, or not owned by your company."

        conn.execute(
            "UPDATE PendingExtractions SET status = 'Rejected', reviewed_by = ?, reviewed_at = ? "
            "WHERE extraction_id = ? AND status = 'Pending'",
            (reviewer, datetime.now().isoformat(), extraction_id)
        )
        conn.execute(
            'INSERT INTO ExtractionAuditLog (extraction_id, action, actor, company_id) VALUES (?, ?, ?, ?)',
            (extraction_id, 'Rejected', reviewer, company_id)
        )
        conn.commit()

    return True, "Rejected."
