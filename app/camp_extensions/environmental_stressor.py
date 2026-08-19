"""
Environmental Stressor Logic - Layer 7 (Feature #20).

camp_multi_ontology.owl already defines a full Layer 7: OperatingEnvironment
individuals (e.g. L7_WestAfrica_TropicalEnv: 35C ambient, 82% humidity, ISO
9223 corrosion category C3), EnvironmentalStressor individuals (e.g.
L7_HeatStressor_Nigeria, a HighTemperatureStressor), and the object property
L7_stressorModifiesFailureMode linking a stressor straight to an L3
behavioural failure mode. None of that reaches app/ontology_reasoner.py,
which only ever evaluates flat L3 thresholds (>900C, >4.5g, <20 PSI) with no
environmental context at all.

This module is the missing property-chain hop:

    L7_OperatingEnvironment --[stressor]--> L7_EnvironmentalStressor
                                                  |
                                     [stressorModifiesFailureMode]
                                                  v
                                          L3_FailureMode  ==>  tightened threshold

It reads the ontology when available (owlready2, same pattern as
ontology_reasoner.py) and falls back to the exact values already committed
in camp_multi_ontology.owl if the reasoner/ontology can't be loaded, so the
feature degrades gracefully exactly like the rest of the system does.
"""
from datetime import datetime
from app.database import get_db
from app.config import Config
from app.auth import get_current_company_id

# Values as committed in camp_multi_ontology.owl's Layer 7 section - used both
# as the fallback if the ontology can't be loaded, and as the seed default
# for every aircraft (this system's fleet operates in Nigeria/West Africa,
# matching the ontology's only worked example, L7_NCAA_Jurisdiction).
FALLBACK_ENVIRONMENTS = {
    'L7_WestAfrica_TropicalEnv': {
        'class': 'L7_TropicalEnvironment', 'ambient_temp_c': 35.0,
        'humidity_pct': 82.0, 'corrosion_category': 'C3',
    }
}
FALLBACK_STRESSORS = {
    'L7_HeatStressor_Nigeria': {
        'class': 'L7_HighTemperatureStressor', 'intensity': 'High',
        'modifies_failure_mode': 'L3_FM_EGT_Exceedance',
    }
}

# Deterministic threshold tightening applied per stressor intensity - the
# hotter/more humid the environment, the smaller the safe margin before the
# AI reasoner should flag the same physical reading as a fault.
INTENSITY_TIGHTENING_PCT = {'High': 0.05, 'Medium': 0.03, 'Low': 0.0}

# Which sensor types are governed by which L3 failure mode, so a stressor
# that "modifiesFailureMode" L3_FM_EGT_Exceedance is known to apply to
# Thermocouple readings (mirrors the mapping already used in ontology_reasoner.py).
FAILURE_MODE_TO_SENSOR = {
    'L3_FM_EGT_Exceedance': 'Thermocouple',
}

# ISO 9223 corrosion categories (C1-CX) mapped to an annual risk weight,
# used to score how hard a given environment is on airframe/gear components.
CORROSION_CATEGORY_WEIGHT = {'C1': 0.2, 'C2': 0.4, 'C3': 0.6, 'C4': 0.8, 'C5': 1.0, 'CX': 1.3}


def ensure_environmental_schema():
    """Compatibility wrapper - environment tables are created by the
    versioned migrations (app/migrations.py, migration 004)."""
    from app.migrations import run_migrations
    run_migrations()


def _company_climate_override(aircraft_id, company_id=None):
    """Round-3: if the aircraft's owning company has set a real hangar
    location (app/auth.py Company Profile), prefer that company's matched
    ambient_temp_c/humidity_pct/corrosion_category over the hardcoded
    West-Africa fallback. Returns None if no company/hangar location is set
    yet, so seed_default_context() falls back to the ontology default
    exactly as before - this is additive, not a behavior change for
    companies that haven't set a hangar location.

    Note: `environment_individual` itself is deliberately left pointing at
    the ontology's 'L7_WestAfrica_TropicalEnv' individual regardless, since
    that's the only environment wired into the stressor->threshold-tightening
    chain in compute_adjusted_threshold() below (the ontology only documents
    one worked stressor example). Only the raw corrosion_category/
    ambient_temp_c/humidity_pct fields - what actually drives
    compute_corrosion_risk() - are overridden per company."""
    if company_id is None:
        company_id = get_current_company_id()
    with get_db() as conn:
        row = conn.execute('''
            SELECT c.corrosion_category, c.ambient_temp_c, c.humidity_pct, c.hangar_location_name
            FROM Aircraft a
            JOIN Companies c ON a.company_id = c.company_id
            WHERE a.aircraft_id = ? AND a.company_id = ? AND c.climate_profile_key IS NOT NULL
        ''', (aircraft_id, company_id)).fetchone()
    return dict(row) if row else None


def sync_company_environment(company_id):
    """Called right after a company saves/updates its hangar location, so
    aircraft that already had a seeded context (created before the hangar
    location was set) pick up the change immediately instead of only new
    aircraft benefiting from it."""
    ensure_environmental_schema()
    with get_db() as conn:
        company = conn.execute('SELECT * FROM Companies WHERE company_id = ?', (company_id,)).fetchone()
        if not company or not company['climate_profile_key']:
            return
        aircraft_ids = [r['aircraft_id'] for r in conn.execute(
            'SELECT aircraft_id FROM Aircraft WHERE company_id = ?', (company_id,)
        ).fetchall()]
        for aid in aircraft_ids:
            conn.execute('''
                UPDATE AircraftEnvironmentContext
                SET corrosion_category = ?, ambient_temp_c = ?, humidity_pct = ?, updated_at = CURRENT_TIMESTAMP
                WHERE aircraft_id = ? AND company_id = ?
            ''', (company['corrosion_category'], company['ambient_temp_c'], company['humidity_pct'], aid, company_id))
        conn.commit()


def seed_default_context(aircraft_id, company_id=None):
    """Give every aircraft a sensible L7 default (editable later) the first time it's seen."""
    if company_id is None:
        company_id = get_current_company_id()
    ensure_environmental_schema()
    with get_db() as conn:
        existing = conn.execute(
            'SELECT 1 FROM AircraftEnvironmentContext WHERE aircraft_id = ? AND company_id = ?', (aircraft_id, company_id)
        ).fetchone()
        if not existing:
            env = FALLBACK_ENVIRONMENTS['L7_WestAfrica_TropicalEnv']
            override = _company_climate_override(aircraft_id, company_id=company_id)
            ambient_temp_c = override['ambient_temp_c'] if override else env['ambient_temp_c']
            humidity_pct = override['humidity_pct'] if override else env['humidity_pct']
            corrosion_category = override['corrosion_category'] if override else env['corrosion_category']
            conn.execute('''
                INSERT INTO AircraftEnvironmentContext
                    (aircraft_id, environment_individual, ambient_temp_c, humidity_pct, corrosion_category, company_id)
                VALUES (?, 'L7_WestAfrica_TropicalEnv', ?, ?, ?, ?)
            ''', (aircraft_id, ambient_temp_c, humidity_pct, corrosion_category, company_id))
            conn.commit()


def load_l7_from_ontology():
    """
    Attempt to read Layer 7 individuals straight out of camp_multi_ontology.owl.
    Returns (environments, stressors) dicts shaped like the FALLBACK_* constants
    above. Falls back to those constants on any failure - the ontology file may
    not always be reachable, exactly as app/ontology_reasoner.py already assumes.
    """
    try:
        from owlready2 import get_ontology, onto_path
        onto_path.append(Config.ONTOLOGY_PATH)
        onto = get_ontology(Config.MOA_ONTOLOGY).load()

        environments, stressors = {}, {}
        for individual in onto.individuals():
            classes = [c.name for c in individual.is_a if hasattr(c, 'name')]
            if any(c.endswith('Environment') for c in classes):
                environments[individual.name] = {
                    'class': classes[0] if classes else 'L7_OperatingEnvironment',
                    'ambient_temp_c': getattr(individual, 'L7_ambientTemperature_C', [None])[0],
                    'humidity_pct': getattr(individual, 'L7_humidity_pct', [None])[0],
                    'corrosion_category': getattr(individual, 'L7_corrosionCategory', [None])[0],
                }
            if any(c.endswith('Stressor') for c in classes):
                modifies = getattr(individual, 'L7_stressorModifiesFailureMode', [])
                stressors[individual.name] = {
                    'class': classes[0] if classes else 'L7_EnvironmentalStressor',
                    'intensity': getattr(individual, 'L7_stressorIntensity', ['Medium'])[0],
                    'modifies_failure_mode': modifies[0].name if modifies else None,
                }

        if environments and stressors:
            return environments, stressors
    except Exception as e:
        print(f"⚠️ Layer 7 ontology load failed, using fallback values: {e}")

    return FALLBACK_ENVIRONMENTS, FALLBACK_STRESSORS


def get_aircraft_context(aircraft_id, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    ensure_environmental_schema()
    seed_default_context(aircraft_id, company_id=company_id)
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM AircraftEnvironmentContext WHERE aircraft_id = ? AND company_id = ?', (aircraft_id, company_id)
        ).fetchone()


def compute_adjusted_threshold(aircraft_id, sensor_type, base_threshold, log_result=True, company_id=None):
    """
    The property-chain hop the gap analysis was missing: walk
    Environment -> Stressor -> modifiesFailureMode -> sensor_type, and
    tighten base_threshold accordingly. Returns (adjusted_threshold, active_stressor_name_or_None).
    """
    if company_id is None:
        company_id = get_current_company_id()
    environments, stressors = load_l7_from_ontology()
    ctx = get_aircraft_context(aircraft_id, company_id=company_id)
    env_key = ctx['environment_individual'] if ctx else 'L7_WestAfrica_TropicalEnv'

    adjusted = base_threshold
    active_stressor = None

    for stressor_name, stressor in stressors.items():
        failure_mode = stressor.get('modifies_failure_mode')
        governed_sensor = FAILURE_MODE_TO_SENSOR.get(failure_mode)
        if governed_sensor == sensor_type and env_key in environments:
            tightening = INTENSITY_TIGHTENING_PCT.get(stressor.get('intensity', 'Medium'), 0.0)
            adjusted = round(base_threshold * (1 - tightening), 2)
            active_stressor = stressor_name
            break

    if log_result:
        with get_db() as conn:
            conn.execute('''
                INSERT INTO EnvironmentalRiskLog (aircraft_id, sensor_type, stressor, base_threshold, adjusted_threshold, company_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (aircraft_id, sensor_type, active_stressor, base_threshold, adjusted, company_id))
            conn.execute(
                'INSERT INTO XAILogs (component_id, ai_decision, explanation_text, company_id) VALUES (?, ?, ?, ?)',
                (aircraft_id, 'Layer7-Environmental-Adjustment',
                 f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Environment {env_key}: "
                 f"{sensor_type} threshold {base_threshold} -> {adjusted} "
                 f"(stressor: {active_stressor or 'none active'}).", company_id)
            )
            conn.commit()

    return adjusted, active_stressor


def compute_corrosion_risk(component_id, aircraft_id, company_id=None):
    """Deterministic corrosion risk score (0-100) combining L7 environment with component exposure."""
    if company_id is None:
        company_id = get_current_company_id()
    ctx = get_aircraft_context(aircraft_id, company_id=company_id)
    category = ctx['corrosion_category'] if ctx else 'C3'
    weight = CORROSION_CATEGORY_WEIGHT.get(category, 0.6)

    with get_db() as conn:
        comp = conn.execute(
            'SELECT * FROM Components WHERE component_id = ? AND company_id = ?', (component_id, company_id)
        ).fetchone()

    if not comp:
        return 0.0

    csn = comp['csn'] or 0
    max_csn = comp['max_csn'] or 5000
    exposure_fraction = min(1.0, csn / max_csn) if max_csn else 0.0
    risk_score = round(weight * exposure_fraction * 100, 1)
    return risk_score


def fleet_environmental_summary(company_id=None):
    """Used by the dashboard page: environment context + corrosion risk for every aircraft/component."""
    if company_id is None:
        company_id = get_current_company_id()
    ensure_environmental_schema()
    with get_db() as conn:
        fleet = conn.execute(
            'SELECT * FROM Aircraft WHERE company_id = ?', (company_id,)
        ).fetchall()

    summary = []
    for plane in fleet:
        ctx = get_aircraft_context(plane['aircraft_id'], company_id=company_id)
        with get_db() as conn:
            components = conn.execute(
                'SELECT * FROM Components WHERE aircraft_id = ? AND company_id = ?', (plane['aircraft_id'], company_id)
            ).fetchall()

        comp_rows = []
        for comp in components:
            risk = compute_corrosion_risk(comp['component_id'], plane['aircraft_id'], company_id=company_id)
            comp_rows.append({'component_id': comp['component_id'], 'component_type': comp['component_type'], 'risk_score': risk})

        adjusted_egt, stressor = compute_adjusted_threshold(
            plane['aircraft_id'], 'Thermocouple', 900.0, log_result=False, company_id=company_id
        )

        summary.append({
            'aircraft_id': plane['aircraft_id'], 'registration': plane['registration'],
            'context': dict(ctx) if ctx else {}, 'components': comp_rows,
            'adjusted_egt_threshold': adjusted_egt, 'active_stressor': stressor,
        })

    return summary
