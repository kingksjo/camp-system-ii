"""
Routes for Phases 2-5 of the document ingestion pipeline: triggering a
parse, and the human review queue where extracted rows get approved,
edited, or rejected before they become real data.
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.database import get_db
from app.ingestion.schema import ensure_ingestion_schema
from app.ingestion.runner import parse_ingested_document
from app.ingestion.commit import approve_extraction, reject_extraction
from app.ingestion.tier3_parser import is_tier3_configured

bp = Blueprint('ingestion', __name__)


@bp.route('/documents/parse/<int:ingestion_id>', methods=['POST'])
def parse_document(ingestion_id):
    """Manually trigger parsing for one uploaded document."""
    parse_ingested_document(ingestion_id, company_id=session.get('company_id'))
    return redirect(url_for('auth.company_profile'))


@bp.route('/documents/review')
def review_queue():
    """The Phase 4 review UI: every pending extraction, grouped by source document."""
    ensure_ingestion_schema()
    company_id = session.get('company_id')

    with get_db() as conn:
        documents = conn.execute('''
            SELECT idoc.ingestion_id, idoc.manual_type, idoc.tier, idoc.status, idoc.scope,
                   idoc.target_model, ad.doc_label, ad.aircraft_id, ad.uploaded_at
            FROM IngestedDocuments idoc
            JOIN AircraftDocuments ad ON ad.doc_id = idoc.doc_id
            WHERE idoc.company_id = ?
            ORDER BY ad.uploaded_at DESC
        ''', (company_id,)).fetchall()

        pending_rows = conn.execute('''
            SELECT * FROM PendingExtractions
            WHERE company_id = ? AND status = 'Pending'
            ORDER BY ingestion_id, extraction_id
        ''', (company_id,)).fetchall()

    extractions_by_ingestion = {}
    for row in pending_rows:
        item = dict(row)
        item['field_data'] = json.loads(row['field_data'])
        extractions_by_ingestion.setdefault(row['ingestion_id'], []).append(item)

    return render_template(
        'ingestion_review.html',
        documents=documents,
        extractions_by_ingestion=extractions_by_ingestion,
        tier3_configured=is_tier3_configured(),
    )


@bp.route('/documents/review/<int:extraction_id>/approve', methods=['POST'])
def approve(extraction_id):
    edited = {k[len('field_'):]: v for k, v in request.form.items() if k.startswith('field_') and v.strip()}
    reviewer = session.get('username', 'unknown')
    approve_extraction(
        extraction_id, reviewer,
        edited_field_data=edited or None,
        company_id=session.get('company_id'),
    )
    return redirect(url_for('ingestion.review_queue'))


@bp.route('/documents/review/<int:extraction_id>/reject', methods=['POST'])
def reject(extraction_id):
    reviewer = session.get('username', 'unknown')
    reject_extraction(extraction_id, reviewer, company_id=session.get('company_id'))
    return redirect(url_for('ingestion.review_queue'))
