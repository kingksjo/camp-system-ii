"""
Orchestrates Phase 2/3 parsing for one IngestedDocuments row: picks the
right parser by tier, runs it, and writes every candidate row into
PendingExtractions for human review. Never writes to a live/reference
table directly - see app/ingestion/commit.py for that, which only runs on
explicit engineer approval.
"""
import json
from app.database import get_db
from app.ingestion.schema import ensure_ingestion_schema, TARGET_TABLE_BY_MANUAL_TYPE
from app.ingestion.tier1_parsers import parse_tier1_document, FIELD_KEYWORDS as TIER1_TYPES
from app.ingestion.tier3_parser import parse_tier3_document, Tier3NotConfigured, FIELD_SCHEMA as TIER3_TYPES


def parse_ingested_document(ingestion_id, company_id=None):
    """
    Run the appropriate parser for one IngestedDocuments row and populate
    PendingExtractions. Returns a status string for the caller to show.

    Phase 5 (DB-01): the ingestion row must belong to ``company_id`` - the
    caller derives it from the authenticated session, never from the URL.
    """
    ensure_ingestion_schema()

    with get_db() as conn:
        ingestion = conn.execute(
            'SELECT * FROM IngestedDocuments WHERE ingestion_id = ? AND company_id = ?',
            (ingestion_id, company_id),
        ).fetchone()
        if not ingestion:
            return 'Not Found'

        doc = conn.execute(
            'SELECT * FROM AircraftDocuments WHERE doc_id = ? AND company_id = ?',
            (ingestion['doc_id'], company_id)
        ).fetchone()
        if not doc:
            return 'Not Found'

        manual_type = ingestion['manual_type']
        conn.execute(
            "UPDATE IngestedDocuments SET status = 'Parsing' WHERE ingestion_id = ?", (ingestion_id,)
        )
        conn.commit()

    try:
        if manual_type in TIER1_TYPES:
            candidates = parse_tier1_document(doc['file_path'], manual_type)
            parser_used = 'tier1_table_extraction'
        elif manual_type in TIER3_TYPES:
            candidates = parse_tier3_document(doc['file_path'], manual_type)
            parser_used = 'tier3_llm_extraction'
        else:
            _set_status(ingestion_id, 'No Parser For This Type')
            return 'No Parser For This Type'
    except Tier3NotConfigured as e:
        _set_status(ingestion_id, 'Tier 3 Not Configured')
        return str(e)
    except Exception as e:
        _set_status(ingestion_id, 'Failed')
        return f"Parsing failed: {e}"

    target_table = TARGET_TABLE_BY_MANUAL_TYPE.get(manual_type)
    with get_db() as conn:
        for candidate in candidates:
            conn.execute(
                'INSERT INTO PendingExtractions '
                '(ingestion_id, company_id, target_table, field_data, source_page, source_excerpt, confidence) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (ingestion_id, ingestion['company_id'], target_table,
                 json.dumps(candidate['field_data']), candidate.get('source_page'),
                 candidate.get('source_excerpt'), candidate.get('confidence'))
            )
        conn.execute(
            'UPDATE IngestedDocuments SET status = ?, parser_used = ? WHERE ingestion_id = ?',
            (f'Ready for Review ({len(candidates)} rows)', parser_used, ingestion_id)
        )
        conn.commit()

    return f'Ready for Review ({len(candidates)} rows)'


def _set_status(ingestion_id, status):
    with get_db() as conn:
        conn.execute('UPDATE IngestedDocuments SET status = ? WHERE ingestion_id = ?', (status, ingestion_id))
        conn.commit()
