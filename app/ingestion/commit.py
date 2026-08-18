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


def _resolve_target_model(conn, ingestion):
    """A fleet-wide upload already knows its target_model; a single-tail
    upload needs it looked up from the aircraft it was attached to."""
    if ingestion['target_model']:
        return ingestion['target_model']
    doc = conn.execute('SELECT aircraft_id FROM AircraftDocuments WHERE doc_id = ?', (ingestion['doc_id'],)).fetchone()
    if not doc:
        return None
    aircraft = conn.execute('SELECT model FROM Aircraft WHERE aircraft_id = ?', (doc['aircraft_id'],)).fetchone()
    return aircraft['model'] if aircraft else None


def approve_extraction(extraction_id, reviewer, edited_field_data=None):
    """
    Approve one PendingExtractions row, optionally with reviewer edits, and
    commit it into its target table. Returns (ok: bool, message: str).
    """
    with get_db() as conn:
        extraction = conn.execute(
            'SELECT * FROM PendingExtractions WHERE extraction_id = ?', (extraction_id,)
        ).fetchone()
        if not extraction:
            return False, "Extraction not found."
        if extraction['status'] != 'Pending':
            return False, f"Already {extraction['status']}."

        ingestion = conn.execute(
            'SELECT * FROM IngestedDocuments WHERE ingestion_id = ?', (extraction['ingestion_id'],)
        ).fetchone()

        field_data = edited_field_data or json.loads(extraction['field_data'])
        target_table = extraction['target_table']
        allowed_columns = COMMITTABLE_COLUMNS.get(target_table)
        if not allowed_columns:
            return False, f"No commit mapping for target table {target_table}."

        row = {k: v for k, v in field_data.items() if k in allowed_columns}
        row['company_id'] = ingestion['company_id'] if 'company_id' in allowed_columns else row.get('company_id')
        if 'target_model' in allowed_columns:
            row['target_model'] = field_data.get('target_model') or _resolve_target_model(conn, ingestion)
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
        conn.execute(
            'UPDATE PendingExtractions SET status = ?, reviewed_by = ?, reviewed_at = ?, field_data = ? '
            'WHERE extraction_id = ?',
            (new_status, reviewer, datetime.now().isoformat(), json.dumps(field_data), extraction_id)
        )
        conn.execute(
            'INSERT INTO ExtractionAuditLog (extraction_id, action, changed_fields, actor) VALUES (?, ?, ?, ?)',
            (extraction_id, new_status,
             json.dumps(edited_field_data) if edited_field_data else None, reviewer)
        )
        conn.commit()

    return True, f"Committed into {target_table}."


def reject_extraction(extraction_id, reviewer):
    with get_db() as conn:
        extraction = conn.execute(
            'SELECT * FROM PendingExtractions WHERE extraction_id = ?', (extraction_id,)
        ).fetchone()
        if not extraction or extraction['status'] != 'Pending':
            return False, "Not found or already reviewed."

        conn.execute(
            "UPDATE PendingExtractions SET status = 'Rejected', reviewed_by = ?, reviewed_at = ? WHERE extraction_id = ?",
            (reviewer, datetime.now().isoformat(), extraction_id)
        )
        conn.execute(
            'INSERT INTO ExtractionAuditLog (extraction_id, action, actor) VALUES (?, ?, ?)',
            (extraction_id, 'Rejected', reviewer)
        )
        conn.commit()

    return True, "Rejected."
