"""
Digital Evidence Upload with geotagging + timestamp anchoring (Feature #11).

Completes the gap noted in the analysis: an upload capability already existed
(app/utils.py: save_upload_file) but nothing anchored *when* and *where* the
evidence was captured, or made the record tamper-evident. This module adds:

  1. Geotagging - GPS EXIF is read straight out of uploaded photos (Pillow is
     already a project dependency); if the file has none, the browser's
     Geolocation API supplies coordinates as a fallback (see the template).
  2. Timestamp anchoring - every record's hash is a function of its own
     bytes + capture time + the previous record's hash, so an evidence log
     forms a hash chain per aircraft: editing or backdating any past entry
     breaks every hash after it, and /evidence/verify proves that instantly.
"""
import hashlib
import os
import sqlite3
import uuid
from datetime import datetime

from app.database import get_db
from app.config import Config
from app.auth import get_current_company_id

GENESIS_HASH = "0" * 64


def ensure_evidence_schema():
    """Compatibility wrapper - DigitalEvidence is created by the versioned
    migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def _extract_gps_from_image(filepath):
    """Best-effort GPS EXIF extraction using Pillow. Returns (lat, lon) or None."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS

        img = Image.open(filepath)
        exif = img._getexif()
        if not exif:
            return None

        gps_info = {}
        for tag, value in exif.items():
            if TAGS.get(tag) == 'GPSInfo':
                for gps_tag in value:
                    gps_info[GPSTAGS.get(gps_tag, gps_tag)] = value[gps_tag]

        if 'GPSLatitude' not in gps_info or 'GPSLongitude' not in gps_info:
            return None

        def _to_decimal(coord, ref):
            d, m, s = [float(x) for x in coord]
            dec = d + m / 60.0 + s / 3600.0
            if ref in ('S', 'W'):
                dec = -dec
            return dec

        lat = _to_decimal(gps_info['GPSLatitude'], gps_info.get('GPSLatitudeRef', 'N'))
        lon = _to_decimal(gps_info['GPSLongitude'], gps_info.get('GPSLongitudeRef', 'E'))
        return round(lat, 6), round(lon, 6)
    except Exception:
        return None


def _get_last_hash(conn, aircraft_id, company_id):
    row = conn.execute(
        'SELECT sha256_hash FROM DigitalEvidence WHERE aircraft_id = ? AND company_id = ? '
        'ORDER BY chain_position DESC LIMIT 1',
        (aircraft_id, company_id)
    ).fetchone()
    return row['sha256_hash'] if row else GENESIS_HASH


def _get_chain_length(conn, aircraft_id, company_id):
    row = conn.execute(
        'SELECT COUNT(*) as cnt FROM DigitalEvidence WHERE aircraft_id = ? AND company_id = ?',
        (aircraft_id, company_id)
    ).fetchone()
    return row['cnt']


def store_evidence(file_storage, aircraft_id, fault_id, component_id, uploaded_by,
                    manual_lat=None, manual_lon=None, captured_at_client=None, notes="",
                    company_id=None):
    """
    Save an uploaded evidence file, geotag it, and append it to the aircraft's
    tamper-evident hash chain. Returns the new evidence record (dict).
    """
    if company_id is None:
        company_id = get_current_company_id()
    ensure_evidence_schema()

    if not file_storage or file_storage.filename == '':
        raise ValueError("No file provided.")

    with get_db() as conn:
        aircraft = conn.execute(
            'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?', (aircraft_id, company_id)
        ).fetchone()
        if not aircraft:
            raise ValueError("Unknown aircraft - evidence not stored.")
        if fault_id:
            fault = conn.execute(
                'SELECT 1 FROM Faults WHERE fault_id = ? AND company_id = ?', (fault_id, company_id)
            ).fetchone()
            if not fault:
                raise ValueError("Unknown fault - evidence not stored.")
        if component_id:
            component = conn.execute(
                'SELECT 1 FROM Components WHERE component_id = ? AND company_id = ?',
                (component_id, company_id)
            ).fetchone()
            if not component:
                raise ValueError("Unknown component - evidence not stored.")

    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    evidence_id = uuid.uuid4().hex
    ext = os.path.splitext(file_storage.filename)[1]
    stored_name = f"EVIDENCE_{evidence_id}{ext}"
    filepath = os.path.join(Config.UPLOAD_FOLDER, stored_name)
    file_storage.save(filepath)

    with open(filepath, 'rb') as f:
        file_bytes = f.read()
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    latitude, longitude, location_source = None, None, "None"
    gps = _extract_gps_from_image(filepath)
    if gps:
        latitude, longitude = gps
        location_source = "EXIF"
    elif manual_lat is not None and manual_lon is not None:
        latitude, longitude = float(manual_lat), float(manual_lon)
        location_source = "Device GPS (browser)"

    captured_at = captured_at_client or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with get_db() as conn:
        prev_hash = _get_last_hash(conn, aircraft_id, company_id)
        chain_position = _get_chain_length(conn, aircraft_id, company_id) + 1

        # The record hash anchors the file content to WHEN, WHERE, WHO, and
        # WHAT CAME BEFORE - changing any of those after the fact changes this hash.
        chain_payload = f"{prev_hash}|{file_sha256}|{captured_at}|{uploaded_by}|{latitude}|{longitude}"
        record_hash = hashlib.sha256(chain_payload.encode('utf-8')).hexdigest()

        for attempt in range(3):
            try:
                conn.execute('''
                    INSERT INTO DigitalEvidence
                        (evidence_id, aircraft_id, fault_id, component_id, file_path, original_filename,
                         sha256_hash, prev_hash, chain_position, latitude, longitude, location_source,
                         captured_at, uploaded_by, notes, company_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (evidence_id, aircraft_id, fault_id, component_id, filepath, file_storage.filename,
                      record_hash, prev_hash, chain_position, latitude, longitude, location_source,
                      captured_at, uploaded_by, notes, company_id))
                break
            except sqlite3.IntegrityError:
                if attempt >= 2:
                    raise
                # Two uploads claimed the same chain position - the unique
                # index (migration 008) guards the chain; recompute and retry.
                prev_hash = _get_last_hash(conn, aircraft_id, company_id)
                chain_position = _get_chain_length(conn, aircraft_id, company_id) + 1
                chain_payload = f"{prev_hash}|{file_sha256}|{captured_at}|{uploaded_by}|{latitude}|{longitude}"
                record_hash = hashlib.sha256(chain_payload.encode('utf-8')).hexdigest()
        conn.commit()

    return {
        'evidence_id': evidence_id, 'sha256_hash': record_hash, 'chain_position': chain_position,
        'latitude': latitude, 'longitude': longitude, 'location_source': location_source,
    }


def verify_chain(aircraft_id, company_id=None):
    """Recompute the hash chain for an aircraft and report the first break, if any."""
    if company_id is None:
        company_id = get_current_company_id()
    ensure_evidence_schema()
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM DigitalEvidence WHERE aircraft_id = ? AND company_id = ? '
            'ORDER BY chain_position ASC',
            (aircraft_id, company_id)
        ).fetchall()

    expected_prev = GENESIS_HASH
    for row in rows:
        if row['prev_hash'] != expected_prev:
            return {'valid': False, 'break_at': row['evidence_id'], 'chain_position': row['chain_position']}

        # Recompute using the on-disk file to prove content hasn't been swapped either
        recomputed_ok = True
        if os.path.exists(row['file_path']):
            with open(row['file_path'], 'rb') as f:
                current_sha256 = hashlib.sha256(f.read()).hexdigest()
            payload = f"{row['prev_hash']}|{current_sha256}|{row['captured_at']}|{row['uploaded_by']}|{row['latitude']}|{row['longitude']}"
            recomputed_ok = hashlib.sha256(payload.encode('utf-8')).hexdigest() == row['sha256_hash']

        if not recomputed_ok:
            return {'valid': False, 'break_at': row['evidence_id'], 'chain_position': row['chain_position'],
                    'reason': 'File content no longer matches its anchored hash'}

        expected_prev = row['sha256_hash']

    return {'valid': True, 'records_checked': len(rows)}
