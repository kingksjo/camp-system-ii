"""
RFID/QR Part Serial Scanning (Feature #19).

Two real, working scan paths are supported (no hardware SDK required):

  1. Camera QR scanning in the browser via the html5-qrcode library (CDN) -
     genuinely decodes a QR code through the device camera, client-side.
  2. RFID / barcode "keyboard wedge" readers - the overwhelming majority of
     commercial RFID and barcode readers (including EASA Form 1 barcode
     labels) simply emulate a USB/Bluetooth keyboard: they "type" the tag
     ID into whatever text field has focus and press Enter. The scan page
     keeps a hidden input permanently focused for exactly this reason, so
     plugging in an off-the-shelf reader works immediately with zero
     additional integration code.

Both paths hit the same /api/parts/scan endpoint and are logged identically.
"""
import uuid
from datetime import datetime
from app.database import get_db
from app.auth import get_current_company_id


def ensure_parts_schema():
    """Compatibility wrapper - parts tables are created by the versioned
    migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def register_part(part_name, ata_chapter, component_id, aircraft_id, easa_form1_ref,
                   manufactured_date, part_serial=None, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_parts_schema()
    part_serial = part_serial or f"PN-{uuid.uuid4().hex[:10].upper()}"
    with get_db() as conn:
        if component_id:
            component = conn.execute(
                'SELECT 1 FROM Components WHERE component_id = ? AND company_id = ?',
                (component_id, company_id)
            ).fetchone()
            if not component:
                raise ValueError("Unknown component - part not registered.")
        if aircraft_id:
            aircraft = conn.execute(
                'SELECT 1 FROM Aircraft WHERE aircraft_id = ? AND company_id = ?',
                (aircraft_id, company_id)
            ).fetchone()
            if not aircraft:
                raise ValueError("Unknown aircraft - part not registered.")

        conn.execute('''
            INSERT INTO PartRecords
                (part_serial, part_name, ata_chapter, component_id, aircraft_id, easa_form1_ref,
                 manufactured_date, installed_date, company_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (part_serial, part_name, ata_chapter, component_id, aircraft_id, easa_form1_ref,
              manufactured_date, datetime.now().strftime('%Y-%m-%d'), company_id))
        conn.commit()
    return part_serial


def scan_part(part_serial, scan_type, scanned_by, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_parts_schema()
    with get_db() as conn:
        part = conn.execute(
            'SELECT * FROM PartRecords WHERE part_serial = ? AND company_id = ?',
            (part_serial, company_id)
        ).fetchone()
        result = 'Found' if part else 'Unknown Serial'
        conn.execute(
            'INSERT INTO PartScanLog (part_serial, scan_type, scanned_by, result, company_id) '
            'VALUES (?, ?, ?, ?, ?)',
            (part_serial, scan_type, scanned_by, result, company_id)
        )
        conn.commit()
    return {'part': dict(part) if part else None, 'result': result}
