"""
CAMSIS-3 Data Grounding (Feature #12).

The ontology reasoner (app/ontology_reasoner.py) and the CBR engine
(app/cbr_engine.py) are both probabilistic / inferential: Pellet infers
faults, TF-IDF finds "similar enough" historical cases. Neither of them
checks a component against the one thing regulators actually require: a
hard, deterministic life limit. CAMSIS ("Continuing Airworthiness
Maintenance Schedule / Interval System", 3rd-generation ruleset used here)
is that deterministic ground-truth layer - it never infers anything, it only
compares real numbers already sitting in the Components/Aircraft tables
against a fixed limits table and reports Compliant / Due Soon / Overdue.

This is deliberately independent of and complementary to the AI reasoner:
its job is to "ground" whatever the AI concludes against numbers a regulator
would accept without a model in the loop at all.
"""
from datetime import datetime
from app.database import get_db
from app.auth import get_current_company_id

DUE_SOON_MARGIN_PCT = 10.0


def ensure_camsis_schema():
    """Compatibility wrapper - CAMSIS tables + seed limits are created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def _classify(remaining, limit_value):
    if remaining <= 0:
        return 'Overdue'
    margin_pct = (remaining / limit_value) * 100.0
    if margin_pct <= DUE_SOON_MARGIN_PCT:
        return 'Due Soon'
    return 'Compliant'


def _limit_for(conn, component_type, category):
    row = conn.execute(
        'SELECT * FROM CAMSISLimits WHERE component_type = ? AND limit_category = ? '
        'ORDER BY limit_id DESC LIMIT 1',
        (component_type, category)
    ).fetchone()
    if row:
        return row
    # Fall back to a generic default rather than skipping the component entirely -
    # deterministic grounding still needs *some* number to compare against.
    return conn.execute(
        'SELECT * FROM CAMSISLimits WHERE component_type = "Engine" AND limit_category = ? LIMIT 1',
        (category,)
    ).fetchone()


def compute_grounding(aircraft_id=None, company_id=None):
    """
    Deterministically evaluate every component (optionally scoped to one
    aircraft) against CAMSISLimits. Returns a list of grounded results and
    logs each computation to CAMSISGroundingLog for audit.
    """
    if company_id is None:
        company_id = get_current_company_id()
    ensure_camsis_schema()
    results = []

    with get_db() as conn:
        query = 'SELECT * FROM Components WHERE company_id = ?'
        params = (company_id,)
        if aircraft_id:
            query += ' AND aircraft_id = ?'
            params = (company_id, aircraft_id)
        components = conn.execute(query, params).fetchall()

        for comp in components:
            comp_type = comp['component_type'] or 'Engine'

            # --- Cycles grounding: uses the csn/max_csn columns already in the schema ---
            csn = comp['csn'] if comp['csn'] is not None else 0
            max_csn = comp['max_csn'] if comp['max_csn'] else None
            if max_csn:
                remaining_cycles = max_csn - csn
                status = _classify(remaining_cycles, max_csn)
                margin_pct = round((remaining_cycles / max_csn) * 100.0, 1)
                conn.execute(
                    'INSERT INTO CAMSISGroundingLog (component_id, limit_category, used_value, limit_value, remaining, margin_pct, status, company_id) '
                    'VALUES (?, "Cycles", ?, ?, ?, ?, ?, ?)',
                    (comp['component_id'], csn, max_csn, remaining_cycles, margin_pct, status, company_id)
                )
                results.append({
                    'component_id': comp['component_id'], 'component_type': comp_type,
                    'category': 'Cycles', 'used_value': csn, 'limit_value': max_csn,
                    'remaining': remaining_cycles, 'margin_pct': margin_pct, 'status': status,
                    'authority_ref': 'On-record component life limit (csn/max_csn)',
                })

            # --- Hours grounding: uses component (or aircraft) total flight hours vs CAMSISLimits ---
            hours_limit_row = _limit_for(conn, comp_type, 'Hours')
            if hours_limit_row:
                used_hours = comp['total_flight_hours']
                if used_hours is None:
                    ac = conn.execute(
                        'SELECT total_flight_hours FROM Aircraft WHERE aircraft_id = ? AND company_id = ?', (comp['aircraft_id'], company_id)
                    ).fetchone()
                    used_hours = ac['total_flight_hours'] if ac else 0.0
                limit_value = hours_limit_row['limit_value']
                remaining_hours = limit_value - used_hours
                status = _classify(remaining_hours, limit_value)
                margin_pct = round((remaining_hours / limit_value) * 100.0, 1)
                conn.execute(
                    'INSERT INTO CAMSISGroundingLog (component_id, limit_id, limit_category, used_value, limit_value, remaining, margin_pct, status, company_id) '
                    'VALUES (?, ?, "Hours", ?, ?, ?, ?, ?, ?)',
                    (comp['component_id'], hours_limit_row['limit_id'], used_hours, limit_value,
                     remaining_hours, margin_pct, status, company_id)
                )
                results.append({
                    'component_id': comp['component_id'], 'component_type': comp_type,
                    'category': 'Hours', 'used_value': used_hours, 'limit_value': limit_value,
                    'remaining': remaining_hours, 'margin_pct': margin_pct, 'status': status,
                    'authority_ref': hours_limit_row['authority_ref'],
                })

        conn.commit()

    return results
