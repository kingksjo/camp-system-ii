"""
Tier 1 parsers: structured/tabular manuals (MMEL, IPC, WDM, CMM).

These follow standardized ATA iSpec 2200 table layouts, so a table
extraction + header-keyword-matching approach works without an LLM in the
loop - real headers vary in exact wording between manufacturers ("ATA
Chapter" vs "ATA Ch." vs "Chapter/Section"), so matching is done by keyword
membership rather than exact string equality.

Every parser returns a flat list of candidate rows:
    {'field_data': {...}, 'source_page': int, 'source_excerpt': str, 'confidence': float}
The caller (app/ingestion/runner.py) wraps these into PendingExtractions
rows - nothing here touches the database.
"""
import pdfplumber

# For each manual type: target field name -> list of header keywords that
# indicate a column maps to it (checked case-insensitively, substring match).
FIELD_KEYWORDS = {
    'MMEL': {
        'ata_chapter': ['ata', 'chapter'],
        'item_description': ['item', 'description', 'nomenclature', 'system/component'],
        'mmel_category': ['cat', 'category'],
        'max_deferral_days': ['interval', 'days', 'repair interval'],
        'remarks': ['remarks', 'exceptions', 'procedures', 'placard'],
    },
    'IPC': {
        'part_number': ['part no', 'part number', 'p/n', 'pn'],
        'nomenclature': ['nomenclature', 'description', 'name'],
        'ata_chapter': ['ata', 'chapter', 'fig'],
        'effectivity': ['effectivity', 'eff'],
    },
    'WDM': {
        'circuit_ref': ['circuit', 'ckt', 'diagram ref'],
        'component_ref': ['component', 'equipment'],
        'connector': ['connector', 'pin'],
        'bus': ['bus', 'source'],
        'wire_gauge': ['gauge', 'awg', 'wire size'],
        'description': ['description', 'function'],
    },
    'CMM': {
        'component_type': ['component', 'assembly'],
        'sensor_type': ['sensor', 'parameter', 'test point'],
        'min_threshold': ['min', 'lower limit'],
        'max_threshold': ['max', 'upper limit'],
        'unit': ['unit', 'uom'],
        'calibration_interval_days': ['calibration', 'interval'],
        'manufacturer': ['manufacturer', 'oem', 'vendor'],
    },
}

# Fields whose presence most indicates a genuinely useful row for that
# manual type - used to compute a confidence score.
REQUIRED_FIELDS = {
    'MMEL': ['ata_chapter', 'item_description', 'mmel_category'],
    'IPC': ['part_number', 'nomenclature'],
    'WDM': ['component_ref', 'connector'],
    'CMM': ['component_type', 'sensor_type'],
}


def _match_header(header_text, manual_type):
    """Map one table header cell to a target field name, or None."""
    if not header_text:
        return None
    header_lower = header_text.strip().lower()
    for field, keywords in FIELD_KEYWORDS[manual_type].items():
        if any(kw in header_lower for kw in keywords):
            return field
    return None


def _parse_number(value):
    if value is None:
        return None
    try:
        cleaned = str(value).strip().replace(',', '')
        return float(cleaned) if '.' in cleaned else int(cleaned)
    except (ValueError, TypeError):
        return None


NUMERIC_FIELDS = {'max_deferral_days', 'min_threshold', 'max_threshold', 'calibration_interval_days'}


def parse_tier1_document(file_path, manual_type):
    """
    Extract candidate rows from a Tier 1 structured manual.

    Returns list[dict]: each with field_data, source_page, source_excerpt, confidence.
    """
    if manual_type not in FIELD_KEYWORDS:
        raise ValueError(f"Not a Tier 1 manual type: {manual_type}")

    results = []
    required = REQUIRED_FIELDS[manual_type]

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue

                header_row = table[0]
                column_map = {i: _match_header(cell, manual_type) for i, cell in enumerate(header_row)}
                if not any(column_map.values()):
                    continue  # this table doesn't look like our manual's layout at all

                for row in table[1:]:
                    if not row or all(cell is None or str(cell).strip() == '' for cell in row):
                        continue

                    field_data = {}
                    for i, cell in enumerate(row):
                        field = column_map.get(i)
                        if not field or cell is None:
                            continue
                        value = str(cell).strip().replace('\n', ' ')
                        field_data[field] = _parse_number(value) if field in NUMERIC_FIELDS else value

                    if not field_data:
                        continue

                    matched_required = sum(1 for f in required if field_data.get(f))
                    confidence = round(matched_required / max(len(required), 1), 2)
                    if confidence == 0:
                        continue  # didn't capture anything that actually identifies this row

                    results.append({
                        'field_data': field_data,
                        'source_page': page_num,
                        'source_excerpt': ' | '.join(str(c) for c in row if c),
                        'confidence': confidence,
                    })

    return results
