"""
Authentication + Company (tenant) module for C.O.R.E. CAMP.

SCOPE (agreed with head programmer): single-tenant login today, schema
ready for multi-tenant rollout later without a rewrite.

- `Companies` / `Users` are new tables - nothing existing touched.
- Every real customer-owned table that has no natural parent gets a
  `company_id` column added (Aircraft, Engineers, Personnel, MasterMEL,
  ToolCrib) via additive ALTER TABLE, defaulted to the single seeded
  company (id=1) so none of the existing seed data breaks.
- Tables that already hang off `aircraft_id` (Components, Schedule,
  Faults, CRS_Records, MaintenanceHistory, ...) are NOT touched - they'll
  be scoped later via a JOIN to Aircraft.company_id when full multi-tenant
  rollout happens. That's the "additive, not a second rewrite" plan.
- A global `before_request` hook (registered in app/__init__.py) gates
  every existing route behind login, with an allowlist for
  login/register/static/health-check.

HANGAR LOCATION -> CLIMATE/CORROSION MATCHING
----------------------------------------------
No paid geocoding API required. `REFERENCE_CLIMATE_PROFILES` is a small,
hand-curated table of real-world reference points (lat/lon + ISO 9223
corrosion category + representative temp/humidity). A company's hangar
coordinates are matched to the *nearest* reference point (haversine
distance), same "known reference table + fallback" pattern already used by
app/camp_extensions/environmental_stressor.py's FALLBACK_ENVIRONMENTS. This
list is deliberately small and approximate - extend it the same "add one
line" way as every other registry in this codebase.
"""
import math
from flask import session, request, redirect, url_for, g
from app.database import get_db

DEFAULT_COMPANY_ID = 1
DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'changeme123'  # noqa: seeded default - see ROUND4_CHANGES.md, must be rotated after first login

# Single source of truth for the manual taxonomy discussed with the head
# programmer: which tier each document type belongs to (Tier 1 = structured/
# tabular, parseable without an LLM; Tier 2 = regulatory feed; Tier 3 =
# freeform/narrative, needs an LLM in the loop - see ROUND5 plan doc), plus
# display label/icon for the upload UI. `Administrative` types (Registration,
# Insurance) aren't part of the extraction pipeline at all - tier is None.
MANUAL_TYPES = {
    # Tier 1 - structured/tabular
    'MMEL': {'label': 'MMEL (Master Minimum Equipment List)', 'tier': 1},
    'IPC':  {'label': 'IPC (Illustrated Parts Catalog)', 'tier': 1},
    'WDM':  {'label': 'WDM (Wiring Diagram Manual)', 'tier': 1},
    'CMM':  {'label': 'CMM (Component Maintenance Manual)', 'tier': 1},
    # Tier 2 - regulatory feeds
    'AD':   {'label': 'AD (Airworthiness Directive)', 'tier': 2},
    'SB':   {'label': 'SB (Service Bulletin)', 'tier': 2},
    'ICA':  {'label': 'ICA (Instructions for Continued Airworthiness)', 'tier': 2},
    # Tier 3 - freeform/narrative, LLM-assisted
    'TSM_FIM': {'label': 'TSM / FIM (Trouble Shooting / Fault Isolation Manual)', 'tier': 3},
    'SSM':          {'label': 'SSM (System Schematic Manual)', 'tier': 3},
    'AMM':          {'label': 'AMM (Aircraft Maintenance Manual excerpt)', 'tier': 3},
    'OEM_Datasheet': {'label': 'OEM Component Datasheet', 'tier': 3},
    'Other_Manual': {'label': 'Other Technical Manual', 'tier': 3},
    # Administrative - not part of the extraction pipeline
    'Registration': {'label': 'Registration', 'tier': None},
    'Insurance':    {'label': 'Insurance', 'tier': None},
    'Other':        {'label': 'Other (non-technical)', 'tier': None},
}

TIER_LABELS = {
    1: 'Tier 1 - Structured',
    2: 'Tier 2 - Regulatory',
    3: 'Tier 3 - LLM-Assisted',
    None: 'Administrative',
}

# Additive company_id columns on existing tables are applied by migration
# 002 (app/migrations.py) - the single schema authority.

# Endpoints reachable without being logged in.
PUBLIC_ENDPOINTS = {'auth.login', 'auth.register', 'auth.do_login', 'auth.do_register', 'static'}

# --- Reference climate/corrosion points (approximate, extend freely) ------
REFERENCE_CLIMATE_PROFILES = {
    'WestAfrica_Tropical': {
        'label': 'West Africa (Tropical, Humid)', 'lat': 6.5244, 'lon': 3.3792,
        'corrosion_category': 'C3', 'ambient_temp_c': 35.0, 'humidity_pct': 82.0,
    },
    'GulfCoast_AridMarine': {
        'label': 'Persian Gulf (Hot, Marine-Arid)', 'lat': 25.2048, 'lon': 55.2708,
        'corrosion_category': 'C4', 'ambient_temp_c': 42.0, 'humidity_pct': 55.0,
    },
    'SEAsia_TropicalMarine': {
        'label': 'Southeast Asia (Tropical, Marine)', 'lat': 1.3521, 'lon': 103.8198,
        'corrosion_category': 'C4', 'ambient_temp_c': 31.0, 'humidity_pct': 84.0,
    },
    'NAmerica_HumidCoastal': {
        'label': 'North America (Humid Coastal)', 'lat': 25.7617, 'lon': -80.1918,
        'corrosion_category': 'C4', 'ambient_temp_c': 28.0, 'humidity_pct': 74.0,
    },
    'Europe_Temperate': {
        'label': 'Europe (Temperate, Inland)', 'lat': 50.1109, 'lon': 8.6821,
        'corrosion_category': 'C2', 'ambient_temp_c': 12.0, 'humidity_pct': 65.0,
    },
    'SouthernAfrica_HighveldArid': {
        'label': 'Southern Africa (High-Altitude, Dry)', 'lat': -26.2041, 'lon': 28.0473,
        'corrosion_category': 'C2', 'ambient_temp_c': 18.0, 'humidity_pct': 45.0,
    },
    'NorthAfrica_Arid': {
        'label': 'North Africa (Arid, Inland)', 'lat': 30.0444, 'lon': 31.2357,
        'corrosion_category': 'C2', 'ambient_temp_c': 26.0, 'humidity_pct': 40.0,
    },
    'EastAfrica_HighlandModerate': {
        'label': 'East Africa (Highland, Moderate)', 'lat': -1.2921, 'lon': 36.8219,
        'corrosion_category': 'C2', 'ambient_temp_c': 21.0, 'humidity_pct': 58.0,
    },
}


def ensure_auth_schema():
    """Compatibility wrapper - schema creation/seed data now lives in the
    versioned migrations (app/migrations.py). Kept so existing callers keep
    working; runs the central migration set, which is a no-op once current."""
    from app.migrations import run_migrations
    run_migrations()


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def match_climate_profile(lat, lon):
    """Nearest-reference-point match for a hangar's coordinates. Returns
    (profile_key, profile_dict, distance_km) - distance is surfaced to the
    UI so a company far from every reference point can see how approximate
    the match is, rather than silently trusting a bad fit."""
    best_key, best_dist = None, float('inf')
    for key, profile in REFERENCE_CLIMATE_PROFILES.items():
        dist = _haversine_km(lat, lon, profile['lat'], profile['lon'])
        if dist < best_dist:
            best_key, best_dist = key, dist
    return best_key, REFERENCE_CLIMATE_PROFILES[best_key], round(best_dist, 1)


def get_current_company_id():
    return session.get('company_id', DEFAULT_COMPANY_ID)


def get_current_company(conn=None):
    company_id = get_current_company_id()
    if conn is not None:
        return conn.execute('SELECT * FROM Companies WHERE company_id = ?', (company_id,)).fetchone()
    with get_db() as c:
        return c.execute('SELECT * FROM Companies WHERE company_id = ?', (company_id,)).fetchone()


def register_auth_hooks(app):
    """Global login gate. Registered once from app/__init__.py."""
    ensure_auth_schema()

    @app.before_request
    def _require_login():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.path))
        g.current_user_id = session.get('user_id')
        g.current_username = session.get('username')
        g.current_company_id = session.get('company_id', DEFAULT_COMPANY_ID)
        return None

    @app.context_processor
    def _inject_auth_context():
        """Makes current_company/current_username available to every template without every route passing them."""
        if not session.get('user_id'):
            return {}
        try:
            company = get_current_company()
        except Exception:
            company = None
        return {'current_company': company, 'current_username': session.get('username')}
