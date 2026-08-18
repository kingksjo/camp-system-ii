"""
Tier 3 parser: freeform/narrative manuals (TSM/FIM, SSM, OEM datasheets).

Unlike Tier 1, there's no standard table layout to key off of, so this
calls the Anthropic API with a schema-constrained extraction prompt.

CREDENTIAL POLICY (agreed with head programmer): this uses YOUR Anthropic
API key and YOUR billing, read from the ANTHROPIC_API_KEY environment
variable of the machine running this Flask app. There is no bundled/shared
key - if it isn't set, Tier 3 parsing is simply unavailable and the
ingestion UI says so plainly, rather than silently failing or using
something else.

Every extracted row gets a LOWER confidence ceiling than Tier 1
(TIER3_MAX_CONFIDENCE) regardless of what the model reports, since
narrative extraction is inherently less certain than reading a table cell -
this is enforced here, not left to prompt-following.
"""
import os
import json
import urllib.request
import urllib.error
import pdfplumber

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
TIER3_MAX_CONFIDENCE = 0.6  # LLM-extracted rows never outrank a Tier 1 table read

# Target field schema per Tier 3 manual type - kept in lockstep with the
# reference tables in app/ingestion/schema.py.
FIELD_SCHEMA = {
    'TSM_FIM': {
        'table': 'FaultIsolationRules',
        'fields': ['symptom', 'probable_cause', 'corrective_action', 'ata_chapter'],
        'description': 'fault symptom -> probable cause -> corrective action entries',
    },
    'SSM': {
        'table': 'SystemSchematics',
        'fields': ['system_name', 'ata_chapter', 'description'],
        'description': 'system schematic sections (hydraulic, fuel, electrical, pneumatic, etc.)',
    },
    'OEM_Datasheet': {
        'table': 'ComponentSpecs',
        'fields': ['component_type', 'sensor_type', 'min_threshold', 'max_threshold',
                   'unit', 'calibration_interval_days', 'manufacturer'],
        'description': 'component/sensor operating specifications and tolerances',
    },
}


class Tier3NotConfigured(Exception):
    """Raised when ANTHROPIC_API_KEY isn't set - caller should surface this
    as a clear UI state, not a crash."""
    pass


def is_tier3_configured():
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def _extract_text_by_page(file_path, max_pages=40):
    """pdfplumber text extraction, capped so a huge manual doesn't blow the
    context window - Tier 3 documents are expected to be excerpts, not
    entire 2000-page manuals; a real deployment would chunk further."""
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages[:max_pages], start=1):
            text = page.extract_text() or ''
            if text.strip():
                pages.append((page_num, text))
    return pages


def _build_prompt(manual_type, pages):
    schema = FIELD_SCHEMA[manual_type]
    fields_desc = ", ".join(schema['fields'])
    body = "\n\n".join(f"[PAGE {n}]\n{text}" for n, text in pages)

    return (
        f"You are extracting {schema['description']} from an aircraft maintenance manual excerpt.\n"
        f"Extract every distinct entry you can find. Return ONLY a JSON array, no prose, no markdown fences.\n"
        f"Each array item must be an object with exactly these keys: {fields_desc}, source_page, confidence.\n"
        f"- source_page: the [PAGE n] number the entry came from.\n"
        f"- confidence: your own 0.0-1.0 estimate of how certain this extraction is.\n"
        f"- Use null for any field genuinely not present in the text - never invent or guess a value.\n"
        f"- If nothing extractable is found, return an empty array [].\n\n"
        f"DOCUMENT TEXT:\n{body}"
    )


def parse_tier3_document(file_path, manual_type):
    """
    Extract candidate rows from a Tier 3 freeform manual via the Anthropic API.

    Returns list[dict] in the same shape as tier1_parsers.parse_tier1_document.
    Raises Tier3NotConfigured if no API key is available.
    """
    if manual_type not in FIELD_SCHEMA:
        raise ValueError(f"Not a Tier 3 manual type: {manual_type}")

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise Tier3NotConfigured(
            "ANTHROPIC_API_KEY is not set on this server. Tier 3 (LLM-assisted) parsing is "
            "unavailable until it is - see ROUND5_PLAN_document_ingestion.md, Open Decisions."
        )

    pages = _extract_text_by_page(file_path)
    if not pages:
        return []

    prompt = _build_prompt(manual_type, pages)
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode('utf-8')

    request = urllib.request.Request(
        ANTHROPIC_API_URL, data=payload, method='POST',
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error ({e.code}): {e.read().decode('utf-8', errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Anthropic API: {e.reason}")

    return _parse_llm_response(response_data, manual_type)


def _parse_llm_response(response_data, manual_type):
    """Split out from parse_tier3_document so it's unit-testable without a
    real network call - feed it a mocked response_data dict directly."""
    schema = FIELD_SCHEMA[manual_type]
    text_blocks = [b['text'] for b in response_data.get('content', []) if b.get('type') == 'text']
    raw_text = "\n".join(text_blocks).strip()

    # Models occasionally wrap JSON in a fence despite instructions not to.
    if raw_text.startswith('```'):
        raw_text = raw_text.strip('`')
        if raw_text.lower().startswith('json'):
            raw_text = raw_text[4:]

    try:
        items = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        field_data = {f: item.get(f) for f in schema['fields'] if item.get(f) not in (None, '')}
        if not field_data:
            continue

        confidence = item.get('confidence')
        try:
            confidence = min(float(confidence), TIER3_MAX_CONFIDENCE) if confidence is not None else TIER3_MAX_CONFIDENCE * 0.5
        except (TypeError, ValueError):
            confidence = TIER3_MAX_CONFIDENCE * 0.5

        results.append({
            'field_data': field_data,
            'source_page': item.get('source_page'),
            'source_excerpt': None,  # narrative extraction has no single cell to quote
            'confidence': round(confidence, 2),
        })

    return results
