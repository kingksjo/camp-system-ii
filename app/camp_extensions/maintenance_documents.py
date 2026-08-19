"""
Maintenance Document Release + Component Framework (Feature request #4).

Generates an actual, downloadable PDF "Certificate of Maintenance
Completion" for any completed CRS release or maintenance log entry, and
keeps a permanent record log (MaintenanceDocuments table) of every document
ever issued - the paper-audit trail the request asked for.

COMPONENT FRAMEWORK
-------------------
The system currently only really populates telemetry-bearing components
(Engine, Landing_Gear/HIL-Rig). So that adding a new physical component
category (APU, avionics LRU, hydraulic pump, etc.) to these documents is a
one-line data change rather than new code, the "Component Technical Record"
section of the document is driven entirely by the COMPONENT_TECHNICAL_FIELDS
registry below: each entry maps a component_type string to the list of
Components columns that are meaningful for it. Unknown/future component
types automatically fall back to `_default` instead of being skipped, so a
document is never silently missing this section.

To add a new component type: add one line to COMPONENT_TECHNICAL_FIELDS.
Nothing else in this file (or the route/template) needs to change.
"""
import hashlib
import os
import sqlite3
import uuid
from datetime import datetime

from app.database import get_db

# Absolute path, matching how app/__init__.py itself computes the project's
# static folder (root_dir = parent of the app/ package) - send_file()
# resolves relative paths against the Flask app package's root_path
# (.../app), not the project root, so a plain relative 'static/...' path
# breaks. Using an absolute path sidesteps that mismatch entirely.
_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DOCS_FOLDER = os.path.join(_ROOT_DIR, 'static', 'generated_docs')

# --- Extensible per-component-category framework -------------------------
COMPONENT_TECHNICAL_FIELDS = {
    'Engine': [
        ('csn', 'Cycles Since New'), ('max_csn', 'Cycle Life Limit'),
        ('total_flight_hours', 'Flight Hours (Component)'),
    ],
    'Landing_Gear': [
        ('csn', 'Cycles Since New'), ('max_csn', 'Overhaul Life Limit (Cycles)'),
    ],
    'HIL-Rig': [
        ('component_type', 'Rig / Bench Category'),
    ],
    # Add new aircraft component categories here, e.g.:
    # 'APU':      [('csn', 'Cycles Since New'), ('max_csn', 'APU Life Limit')],
    # 'Avionics': [('component_type', 'LRU Category')],
    '_default': [
        ('component_type', 'Component Category'),
    ],
}


def ensure_documents_schema():
    """Compatibility wrapper - MaintenanceDocuments is created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()
    os.makedirs(DOCS_FOLDER, exist_ok=True)


def _component_technical_rows(component_row):
    """Build the (label, value) rows for a component using the extensible framework above."""
    if not component_row:
        return [("Component", "Not linked to a specific component")]

    comp_type = component_row['component_type'] if 'component_type' in component_row.keys() else None
    field_spec = COMPONENT_TECHNICAL_FIELDS.get(comp_type, COMPONENT_TECHNICAL_FIELDS['_default'])

    rows = [("Component ID", component_row['component_id'])]
    for col, label in field_spec:
        try:
            value = component_row[col]
        except (IndexError, KeyError):
            value = None
        rows.append((label, str(value) if value is not None else "N/A"))
    return rows


def _fetch_source_record(conn, source_type, source_id):
    """Look up the record + (optional) linked component for a CRS or maintenance-log entry."""
    component_row = None

    if source_type == 'crs':
        record = conn.execute('SELECT * FROM CRS_Records WHERE id = ?', (source_id,)).fetchone()
        if not record:
            return None, None
        header = {
            'title': 'CERTIFICATE OF RELEASE TO SERVICE',
            'aircraft_reg': record['aircraft_reg'],
            'reference_id': record['reference_id'],
            'description': record['description'],
            'signed_off_by': record['signed_off_by'],
            'date': record['release_date'],
        }
        # IMDF upgrade: surface the Work Order number and removed/installed
        # component records directly on the CRS, instead of the CRS being a
        # bare sign-off line disconnected from what was actually done.
        try:
            record_keys = record.keys()
        except Exception:
            record_keys = []
        if 'work_order_number' in record_keys and record['work_order_number']:
            header['work_order_number'] = record['work_order_number']
        if 'ata_chapter' in record_keys and record['ata_chapter']:
            header['ata_chapter'] = record['ata_chapter']

        removed_part, installed_part = None, None
        if 'removed_part_serial' in record_keys and record['removed_part_serial']:
            removed_part = conn.execute(
                'SELECT * FROM PartRecords WHERE part_serial = ?', (record['removed_part_serial'],)
            ).fetchone()
        if 'installed_part_serial' in record_keys and record['installed_part_serial']:
            installed_part = conn.execute(
                'SELECT * FROM PartRecords WHERE part_serial = ?', (record['installed_part_serial'],)
            ).fetchone()
        if removed_part or installed_part:
            header['removed_part'] = removed_part
            header['installed_part'] = installed_part

        # Best-effort: CRS reference_id is often "FAULT-<fault_id>" - pull the component if so
        if record['reference_id'] and record['reference_id'].startswith('FAULT-'):
            fault_id = record['reference_id'].split('-')[-1]
            fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (fault_id,)).fetchone()
            if fault:
                component_row = conn.execute(
                    'SELECT * FROM Components WHERE component_id = ?', (fault['component_id'],)
                ).fetchone()
        return header, component_row

    elif source_type == 'maintenance_log':
        record = conn.execute('SELECT * FROM MaintenanceHistory WHERE log_id = ?', (source_id,)).fetchone()
        if not record:
            return None, None
        header = {
            'title': 'RECORD OF MAINTENANCE COMPLETION',
            'aircraft_reg': record['aircraft_reg'],
            'reference_id': f"LOG-{record['log_id']}",
            'description': record['task_description'],
            'signed_off_by': record['signed_off_by'],
            'date': record['completion_date'] or record['sign_off_date'],
        }
        return header, None

    elif source_type == 'fault':
        fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (source_id,)).fetchone()
        if not fault:
            return None, None
        component_row = conn.execute(
            'SELECT * FROM Components WHERE component_id = ?', (fault['component_id'],)
        ).fetchone()
        aircraft_reg = component_row['aircraft_id'].replace('Aircraft_', '') if component_row else 'Unknown'
        header = {
            'title': 'RECORD OF MAINTENANCE COMPLETION',
            'aircraft_reg': aircraft_reg,
            'reference_id': f"FAULT-{fault['fault_id']}",
            'description': f"Resolved Fault: {fault['fault_type']} ({fault['amm_reference']})",
            'signed_off_by': fault['resolved_by'],
            'date': fault['resolved_date'],
        }
        return header, component_row

    return None, None


def generate_document(source_type, source_id):
    """
    Generate (or return the already-generated) PDF for a CRS/maintenance-log
    record, log it into MaintenanceDocuments for the paper-audit trail, and
    return {'file_path', 'document_id', 'already_existed'}.
    """
    ensure_documents_schema()

    with get_db() as conn:
        existing = conn.execute(
            'SELECT * FROM MaintenanceDocuments WHERE source_type = ? AND source_id = ?',
            (source_type, str(source_id))
        ).fetchone()
        if existing and os.path.exists(existing['file_path']):
            return {'file_path': existing['file_path'], 'document_id': existing['document_id'], 'already_existed': True}

        header, component_row = _fetch_source_record(conn, source_type, source_id)
        if not header:
            raise ValueError(f"No {source_type} record found for id={source_id}")

    document_id = uuid.uuid4().hex[:12].upper()
    filename = f"CAMP-{source_type.upper()}-{source_id}-{document_id}.pdf"
    file_path = os.path.join(DOCS_FOLDER, filename)

    _render_pdf(file_path, header, component_row, document_id)

    with open(file_path, 'rb') as f:
        document_hash = hashlib.sha256(f.read()).hexdigest()

    with get_db() as conn:
        try:
            conn.execute('''
                INSERT INTO MaintenanceDocuments (document_id, source_type, source_id, aircraft_reg, file_path, document_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (document_id, source_type, str(source_id), header['aircraft_reg'], file_path, document_hash))
            conn.commit()
        except sqlite3.IntegrityError:
            # Lost the race to a concurrent generate_document() for the same
            # source - the unique index (migration 008) is the source of
            # truth. Remove the orphan PDF we just wrote and hand back the
            # winner's record.
            try:
                os.remove(file_path)
            except OSError:
                pass
            existing = conn.execute(
                'SELECT * FROM MaintenanceDocuments WHERE source_type = ? AND source_id = ?',
                (source_type, str(source_id))
            ).fetchone()
            if existing and os.path.exists(existing['file_path']):
                return {'file_path': existing['file_path'], 'document_id': existing['document_id'], 'already_existed': True}
            raise

    return {'file_path': file_path, 'document_id': document_id, 'already_existed': False}


def _render_pdf(file_path, header, component_row, document_id):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    except ImportError:
        raise RuntimeError(
            "The 'reportlab' package is required to generate maintenance documents. "
            "Install it with: pip install reportlab"
        )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CampTitle', parent=styles['Title'], fontSize=16, spaceAfter=4)
    sub_style = ParagraphStyle('CampSub', parent=styles['Normal'], textColor=colors.HexColor('#475569'), fontSize=9)
    section_style = ParagraphStyle('CampSection', parent=styles['Heading2'], fontSize=11,
                                    textColor=colors.HexColor('#0f172a'), spaceBefore=14, spaceAfter=6)

    doc = SimpleDocTemplate(file_path, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = []

    story.append(Paragraph("C.O.R.E. CAMP &mdash; Continuous Ontology Reasoning Engine", sub_style))
    story.append(Paragraph(header['title'], title_style))
    story.append(HRFlowable(width="100%", color=colors.HexColor('#0f172a'), thickness=1))
    story.append(Spacer(1, 12))

    info_table_rows = [
        ["Aircraft Registration:", header['aircraft_reg'], "Reference:", header['reference_id']],
        ["Date:", str(header['date']).split('.')[0], "Document ID:", document_id],
    ]
    if header.get('work_order_number'):
        info_table_rows.insert(0, ["Work Order:", header['work_order_number'], "ATA Chapter:", header.get('ata_chapter', '—')])

    info_table = Table(info_table_rows, colWidths=[1.3 * inch, 2 * inch, 1.1 * inch, 1.8 * inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)

    story.append(Paragraph("Maintenance Action Performed", section_style))
    story.append(Paragraph(header['description'], styles['Normal']))

    # IMDF upgrade: removed/installed component record, when this CRS
    # involved an actual part swap (Stage 7 of the Integrated Maintenance
    # Documentation Framework).
    if header.get('removed_part') or header.get('installed_part'):
        story.append(Paragraph("Component Replacement Record", section_style))
        removed = header.get('removed_part')
        installed = header.get('installed_part')
        replacement_rows = [["", "Removed Component", "Installed Component"]]
        replacement_rows.append([
            "Part Number / Serial",
            removed['part_serial'] if removed else "—",
            installed['part_serial'] if installed else "—",
        ])
        replacement_rows.append([
            "Description",
            removed['part_name'] if removed else "—",
            installed['part_name'] if installed else "—",
        ])
        replacement_rows.append([
            "Reason / Certificate",
            (removed['removal_reason'] if removed else "—"),
            (installed['easa_form1_ref'] if installed else "—"),
        ])
        replacement_table = Table(replacement_rows, colWidths=[1.6 * inch, 2.3 * inch, 2.3 * inch])
        replacement_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(replacement_table)

    story.append(Paragraph("Component Technical Record", section_style))
    comp_rows = _component_technical_rows(component_row)
    comp_table = Table([[label, value] for label, value in comp_rows], colWidths=[2.6 * inch, 3.6 * inch])
    comp_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(comp_table)

    story.append(Paragraph("Certifying Signature", section_style))
    story.append(Paragraph(f"<b>{header['signed_off_by']}</b>", styles['Normal']))
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="40%", color=colors.HexColor('#94a3b8'), thickness=0.75))
    story.append(Paragraph("Certifying Engineer Signature &amp; Stamp", sub_style))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", color=colors.HexColor('#cbd5e1'), thickness=0.5))
    story.append(Paragraph(
        f"This document was system-generated by C.O.R.E. CAMP for paper-audit purposes. "
        f"Document integrity hash is recorded in the MaintenanceDocuments register (Document ID {document_id}).",
        sub_style
    ))

    doc.build(story)


def list_documents():
    ensure_documents_schema()
    with get_db() as conn:
        return conn.execute('SELECT * FROM MaintenanceDocuments ORDER BY generated_at DESC').fetchall()
