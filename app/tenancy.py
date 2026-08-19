"""
Tenancy (company isolation) helpers for C.O.R.E. CAMP.

Phase 5 of the database audit (DB-01/DB-13 - see
DATABASE_AUDIT_GUIDELINES.md). Every operational table now carries a
company_id column (migration 010); these helpers are the single place that
resolves "which company is this request for" and "is this row owned by the
current company", so route code never has to guess a company id or hand-roll
an ownership filter.

Rules of thumb for callers:

- Request handlers: use the default company resolution (session / g), which
  `current_company_id()` returns.
- Background threads (watchers, listeners, job workers): there is no Flask
  request, so pass an explicit `company_id=...` argument and never call the
  session-based default - see each module's loop that iterates companies.
- Read a row you will mutate? Use `require_owned(...)` (aborts 404) or
  `find_owned(...)` (returns None) before touching it.
- Never trust a company_id that arrives in a form body or URL.
"""
from flask import session, g, abort

from app.auth import DEFAULT_COMPANY_ID

# Tables that have a company_id column and are the canonical "owning" rows.
# Anything else either links back to one of these or is reference data that
# is shared/global (still stamped with a company_id of 1 today).


def current_company_id():
    """Company id for the current request. Falls back to the seeded company.

    Priority: the before_request hook's `g.current_company_id`, then the
    session value, then the single seeded company (legacy/safety default).
    Falls back to the seeded company outside an app/request context (tests,
    background threads) rather than raising.
    """
    try:
        if hasattr(g, 'current_company_id'):
            return g.current_company_id
        return session.get('company_id', DEFAULT_COMPANY_ID)
    except RuntimeError:
        return DEFAULT_COMPANY_ID


def company_id_or_default(company_id):
    """Normalize an explicit company id, falling back to the request's."""
    return company_id if company_id is not None else current_company_id()


def _owned(conn, table, where_sql, params):
    return conn.execute(
        f'SELECT * FROM {table} WHERE {where_sql}', params
    ).fetchone()


def find_owned(conn, table, pk_col, pk_value, company_id=None):
    """Fetch a row owned by the (default or explicit) company, or None."""
    return _owned(
        conn, table, f'{pk_col} = ? AND company_id = ?',
        (pk_value, company_id_or_default(company_id)),
    )


def require_owned(conn, table, pk_col, pk_value, company_id=None):
    """Like find_owned but aborts 404 when the row is missing or foreign."""
    row = find_owned(conn, table, pk_col, pk_value, company_id)
    if row is None:
        abort(404)
    return row


def owned_aircraft(conn, aircraft_id, company_id=None):
    return find_owned(conn, 'Aircraft', 'aircraft_id', aircraft_id, company_id)


def require_aircraft(conn, aircraft_id, company_id=None):
    return require_owned(conn, 'Aircraft', 'aircraft_id', aircraft_id, company_id)


def owned_component(conn, component_id, company_id=None):
    return find_owned(conn, 'Components', 'component_id', component_id, company_id)


def require_component(conn, component_id, company_id=None):
    return require_owned(conn, 'Components', 'component_id', component_id, company_id)


def owned_fault(conn, fault_id, company_id=None):
    return find_owned(conn, 'Faults', 'fault_id', fault_id, company_id)


def require_fault(conn, fault_id, company_id=None):
    return require_owned(conn, 'Faults', 'fault_id', fault_id, company_id)


def owned_schedule(conn, record_id, company_id=None):
    """Schedule uses rowid/event_id as its record id."""
    cid = company_id_or_default(company_id)
    return conn.execute(
        'SELECT rowid AS record_id, s.* FROM Schedule s '
        'WHERE s.event_id = ? AND s.company_id = ?',
        (record_id, cid),
    ).fetchone()


def require_schedule(conn, record_id, company_id=None):
    row = owned_schedule(conn, record_id, company_id)
    if row is None:
        abort(404)
    return row


def owned_mel_deferral(conn, deferral_id, company_id=None):
    return find_owned(conn, 'MEL_Deferrals', 'deferral_id', deferral_id, company_id)


def require_mel_deferral(conn, deferral_id, company_id=None):
    return require_owned(conn, 'MEL_Deferrals', 'deferral_id', deferral_id, company_id)


def owned_mmel(conn, mmel_id, company_id=None):
    return find_owned(conn, 'MasterMEL', 'mmel_id', mmel_id, company_id)


def require_mmel(conn, mmel_id, company_id=None):
    return require_owned(conn, 'MasterMEL', 'mmel_id', mmel_id, company_id)


def owned_engineer(conn, emp_id, company_id=None):
    return find_owned(conn, 'Engineers', 'emp_id', emp_id, company_id)


def require_engineer(conn, emp_id, company_id=None):
    return require_owned(conn, 'Engineers', 'emp_id', emp_id, company_id)


def owned_tool(conn, tool_id, company_id=None):
    return find_owned(conn, 'ToolCrib', 'tool_id', tool_id, company_id)


def require_tool(conn, tool_id, company_id=None):
    return require_owned(conn, 'ToolCrib', 'tool_id', tool_id, company_id)


def owned_part(conn, part_serial, company_id=None):
    return find_owned(conn, 'PartRecords', 'part_serial', part_serial, company_id)


def require_part(conn, part_serial, company_id=None):
    return require_owned(conn, 'PartRecords', 'part_serial', part_serial, company_id)


def owned_pending_extraction(conn, extraction_id, company_id=None):
    return find_owned(conn, 'PendingExtractions', 'extraction_id', extraction_id, company_id)


def require_pending_extraction(conn, extraction_id, company_id=None):
    return require_owned(conn, 'PendingExtractions', 'extraction_id', extraction_id, company_id)


def owned_ingestion(conn, ingestion_id, company_id=None):
    return find_owned(conn, 'IngestedDocuments', 'ingestion_id', ingestion_id, company_id)


def require_ingestion(conn, ingestion_id, company_id=None):
    return require_owned(conn, 'IngestedDocuments', 'ingestion_id', ingestion_id, company_id)