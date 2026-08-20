"""
Ingestion idempotency (DB-08): parsing an IngestedDocuments row twice must
not stack duplicate PendingExtractions rows, and concurrent parses must not
both run. Reviewed rows must survive a re-parse.
"""
import json

import pytest

from app import migrations as migrations_module
from app.config import Config
from app.database import get_db
from app.ingestion import runner
from app.ingestion.commit import approve_extraction

# Candidate batch shape mirrors tier1_parsers/tier3_parser output.
CANDIDATES = [
    {'field_data': {'ata_chapter': '21-00', 'item_description': 'PACK 1', 'mmel_category': 'A'},
     'source_page': 4, 'source_excerpt': '21-00 | PACK 1 | A', 'confidence': 1.0},
    {'field_data': {'ata_chapter': '21-00', 'item_description': 'PACK 2', 'mmel_category': 'B'},
     'source_page': 4, 'source_excerpt': '21-00 | PACK 2 | B', 'confidence': 1.0},
    {'field_data': {'ata_chapter': '21-10', 'item_description': 'ACM', 'mmel_category': 'C'},
     'source_page': 5, 'source_excerpt': '21-10 | ACM | C', 'confidence': 1.0},
]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "camp_ingestion_test.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(path))
    migrations_module.run_migrations()
    return path


def _insert_ingestion(company_id=1):
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO Aircraft (aircraft_id, registration, model, company_id) '
            'VALUES (?, ?, ?, ?)',
            ('TEST-AIRFRAME', 'TEST-AIRFRAME', 'Do328', company_id)
        )
        cur = conn.execute(
            'INSERT INTO AircraftDocuments (aircraft_id, company_id, doc_label, doc_type, file_path) '
            'VALUES (?, ?, ?, ?, ?)',
            ('TEST-AIRFRAME', company_id, 'MMEL test', 'MMEL', 'C:/nonexistent/mmel.pdf')
        )
        doc_id = cur.lastrowid
        cur = conn.execute(
            'INSERT INTO IngestedDocuments '
            '(doc_id, company_id, manual_type, tier, scope, classification_method) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (doc_id, company_id, 'MMEL', 1, 'single', 'user_selected')
        )
        conn.commit()
        return cur.lastrowid


def _pending_count(ingestion_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM PendingExtractions WHERE ingestion_id = ? AND status = 'Pending'",
            (ingestion_id,)
        ).fetchone()[0]


def _status(ingestion_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT status FROM IngestedDocuments WHERE ingestion_id = ?',
            (ingestion_id,)
        ).fetchone()['status']


def test_repeated_parse_does_not_duplicate_pending_rows(db_path, monkeypatch):
    monkeypatch.setattr(runner, 'parse_tier1_document', lambda *a, **k: CANDIDATES)
    ingestion_id = _insert_ingestion()

    first = runner.parse_ingested_document(ingestion_id, company_id=1)
    assert first == f'Ready for Review ({len(CANDIDATES)} rows)'
    assert _pending_count(ingestion_id) == len(CANDIDATES)

    second = runner.parse_ingested_document(ingestion_id, company_id=1)
    assert second == f'Ready for Review ({len(CANDIDATES)} rows)'
    assert _pending_count(ingestion_id) == len(CANDIDATES)


def test_parse_while_already_parsing_is_noop(db_path, monkeypatch):
    monkeypatch.setattr(runner, 'parse_tier1_document', lambda *a, **k: CANDIDATES)
    ingestion_id = _insert_ingestion()

    with get_db() as conn:
        conn.execute("UPDATE IngestedDocuments SET status = 'Parsing' WHERE ingestion_id = ?",
                     (ingestion_id,))
        conn.commit()

    result = runner.parse_ingested_document(ingestion_id, company_id=1)
    assert result == 'Already Parsing'
    assert _pending_count(ingestion_id) == 0


def test_reparse_preserves_reviewed_rows(db_path, monkeypatch):
    monkeypatch.setattr(runner, 'parse_tier1_document', lambda *a, **k: CANDIDATES)
    ingestion_id = _insert_ingestion()

    runner.parse_ingested_document(ingestion_id, company_id=1)

    with get_db() as conn:
        extraction_id = conn.execute(
            'SELECT extraction_id FROM PendingExtractions WHERE ingestion_id = ?',
            (ingestion_id,)
        ).fetchone()['extraction_id']

    ok, _ = approve_extraction(extraction_id, 'ENG-1', company_id=1)
    assert ok

    runner.parse_ingested_document(ingestion_id, company_id=1)

    with get_db() as conn:
        reviewed = conn.execute(
            "SELECT COUNT(*) FROM PendingExtractions WHERE ingestion_id = ? AND status != 'Pending'",
            (ingestion_id,)
        ).fetchone()[0]
    assert reviewed == 1
    assert _pending_count(ingestion_id) == len(CANDIDATES)


def test_parse_requires_company_ownership(db_path, monkeypatch):
    monkeypatch.setattr(runner, 'parse_tier1_document', lambda *a, **k: CANDIDATES)
    ingestion_id = _insert_ingestion(company_id=1)

    result = runner.parse_ingested_document(ingestion_id, company_id=999)
    assert result == 'Not Found'
    assert _pending_count(ingestion_id) == 0
    assert _status(ingestion_id) == 'Uploaded - Awaiting Parser'


def test_failed_parse_leaves_no_pending_rows(db_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('parser exploded')

    monkeypatch.setattr(runner, 'parse_tier1_document', boom)
    ingestion_id = _insert_ingestion()

    result = runner.parse_ingested_document(ingestion_id, company_id=1)
    assert result == 'Parsing failed: parser exploded'
    assert _pending_count(ingestion_id) == 0
    assert _status(ingestion_id) == 'Failed'