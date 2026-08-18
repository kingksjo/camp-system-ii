# C.O.R.E. CAMP — Round 3: Sidebar & Navigation Reorganization

Customer-test feedback this round was about information architecture, not
bugs or new features - reducing sidebar clutter and consolidating related
pages. Summary of what changed and why.

## 1. Merged "Interactive Schedule" into "Hangar Schedule & Work Orders"

`templates/calendar.html` (the existing Hangar Schedule page) now has three
tabs instead of being one fixed view:
- **Weekly Grid** - the original page, completely unchanged.
- **Interactive Calendar** - the FullCalendar drag/drop view that used to
  live at its own `/schedule/fullcalendar` page.
- **Activity Log** - see #3 below.

`app/routes/calendar.py`'s `calendar()` view now also supplies the data the
other two tabs need. The FullCalendar JSON APIs
(`/api/schedule/events` etc.) didn't move or change at all - the merged
page's JS just calls them directly, same as before.

The old `/schedule/fullcalendar` URL still works - it 302-redirects to
`/calendar#interactive` so nothing bookmarked breaks.

## 2. New collapsible "Utilities" sidebar section

Five items that were cluttering the main sidebar list are now grouped under
one collapsible **Utilities** toggle (matches the sidebar's existing visual
style, persists open/closed via localStorage like the sidebar-collapse
button already did):

- HITL Telemetry Bridge
- **Airworthiness Limitations Monitor** (renamed from "CAMSIS-3 Grounding" -
  ALS/Airworthiness Limitations Section is the real aviation MRO term for a
  mandatory component life-limits document, which is exactly what this
  feature deterministically checks; the old name didn't mean anything to a
  new user). Route (`/camsis`) and internal module names are unchanged -
  only the visible label and page title changed.
- Ghost Data Console
- IoT Smart Tool Bridge
- Document Register

Net effect on the sidebar: 2 links removed (merged into Hangar Schedule, see
#1/#3), 5 links collapsed into 1 toggle - down from 10 top-level extension
links to 4 (Evidence Locker, Part Traceability, Environmental Stressors,
Utilities).

## 3. Kill switch merged into Hangar Schedule as a hybrid activity log

The old standalone "Calendar Kill Switch" page only ever showed its own
automated cancellations. It's now the **Activity Log** tab on the Hangar
Schedule page, and it's a genuine hybrid:
- Still runs the same background watcher and "Scan Now" action.
- Now also pulls in automatic 2-day schedule expirations
  (`ScheduleLifecycleLog`) and manual engineer sign-offs
  (`MaintenanceHistory` entries tagged "Hangar Check:") into one
  chronological history - see `kill_switch.get_hangar_activity_log()`.

The old `/killswitch` URL redirects to `/calendar#activity-log`.

## Files touched

**New:** none.
**Removed (now dead - merged into calendar.html):**
`templates/extensions/fullcalendar_schedule.html`,
`templates/extensions/kill_switch.html`.
**Edited:** `templates/calendar.html` (tabs added), `app/routes/calendar.py`
(feeds the new tabs), `app/camp_extensions/kill_switch.py` (added
`get_hangar_activity_log()`), `app/camp_extensions/routes_kill_switch.py`
and `app/camp_extensions/fullcalendar_schedule.py` (page routes now
redirect into the merged page; their JSON/action APIs are untouched),
`templates/extensions/camsis.html` (renamed heading),
`templates/base.html` (sidebar restructure), `static/style.css` (appended
nav-group/nav-subgroup rules only - nothing existing was changed).

---

# Round 3b: Component / Sensor Framework Revamp

Follow-up request this round: every component in the fleet - engine, fuel
tank, wing, whatever - was being given the exact same three sensors
(Thermocouple, Vibration, Pressure) regardless of whether that reading meant
anything physically on that part. That's also how orphan test-debris
components (`Wing_Structure_L`, `Engine_Aircraft_TEST_N1`,
`FuelTank_Main_5N_IAO`, a whole fake `Aircraft_TEST_N1` tail with no real
fleet aircraft, etc.) had been silently accumulating: a generic
`Engine_{aircraft_id}` component got auto-invented the first time anyone
loaded telemetry for an aircraft with none on record.

## 1. Clean-slate data reset

- Deleted every orphan/test `Components` row and its `SensorTelemetry`
  history, and removed the fake `Aircraft_TEST_N1` tail entirely.
- Re-seeded a clean, identical 7-component airframe template for **all
  four real aircraft** (5N-TAJ, 5N-MUM, 5N-IAO, 5N-BUK): LeftEngine,
  RightEngine, LeftFuelTank, RightFuelTank, CenterFuelTank, WingSystem ×2.
  (5N-BUK previously had *zero* components on record - it does now, same as
  the rest of the fleet.)

## 2. Two small registries replace the old flat sensor list (`app/routes/telemetry.py`)

- **`SENSOR_TYPE_REGISTRY`** - single source of truth per sensor type
  (baseline/noise, fault value, unit, min/max, threshold + direction, fault
  label, AMM reference). Nothing hardcodes a threshold anywhere else in the
  file anymore - `poll`, `history`, and fault injection/clearing all read
  from this one place.
- **`CATEGORY_SENSOR_MAP`** - which sensors a component *category* actually
  carries. `Components.component_type` values in the DB (LeftEngine,
  RightFuelTank, ...) are folded into a category (Engine, FuelTank, Wing,
  ...) by `_category_for()`, so seed data doesn't need to match this dict's
  keys exactly.

Result - each component now only reports sensors that make physical sense:

| Category | Sensors |
|---|---|
| Engine | Thermocouple (EGT), Vibration, Oil Pressure |
| FuelTank | Pressure, Fuel Quantity |
| Wing | Strain Gauge |
| *(unmapped/default)* | Vibration only |

Verified end-to-end with the test client: injecting `oil_pressure` faults
only the two Engine components, `fuel_quantity` only the three FuelTank
components, `overstrain` only the two Wing components - no cross-talk.

## 3. Adding a new physical system later (avionics LRU, APU, landing gear, hydraulics, ...)

No rewrite needed - three additive steps, same "one line, nothing else
changes" pattern already used by `CAMSISLimits`/`DEFAULT_LIMITS` in
`camsis.py` and `COMPONENT_TECHNICAL_FIELDS` in `maintenance_documents.py`:

1. New sensor type needed? Add one entry to `SENSOR_TYPE_REGISTRY`.
2. Add one entry to `CATEGORY_SENSOR_MAP` mapping the new category to the
   sensor type(s) it carries (and one line to `_category_for()`'s
   substring matches, and one to `CANONICAL_COMPONENT_TEMPLATE` if it
   should be seeded automatically for every aircraft).
3. *(Optional - only if it should feed the AI reasoner's fault inference)*
   add a matching `elif` branch in `app/ontology_reasoner.py`'s
   `_evaluate_contextual_thresholds` - it reads `sensor_type` as a plain
   string, so it's additive, not a rewrite.

`telemetry.html`, `api_telemetry_poll`, and `api_telemetry_history` all
iterate the registries generically - none of them need to change.

## 4. Two sensor types added this round as the first real test of the framework

- **Oil Pressure Sensor** (Engine) - `<25 PSI` → `Engine_Oil_Pressure_Low`.
- **Fuel Quantity Sensor** (FuelTank) - `<10%` → `Fuel_Quantity_Low`.
- **Strain Gauge** (Wing) - `>3.5 kµε` → `Wing_Structural_Overstrain`.

All three are wired all the way through: gauge cards, fault injection
dropdown, live/history tables, ontology reasoner thresholds, and the
fault-resolution reset (`app/routes/fault_resolution.py` now resets the
correct sensor back to baseline for every fault type, not just the
original three).

> **Correction (same day, confirmed fleet-reality mapping):** the initial
> pass above used a placeholder Fuel Tank mapping (Fuel Pressure + Fuel
> Quantity) and gave Wing only Strain Gauge. The confirmed mapping is:
> - **Engine:** EGT (Thermocouple) + Vibration + Oil Pressure - unchanged.
> - **Fuel Tank:** **Fuel Pressure + Fuel Temperature** (not Fuel
>   Quantity). `Pressure Sensor` was renamed to `Fuel Pressure Sensor`
>   (the old generic name was itself part of the "which sensor is for
>   what" ambiguity this whole revamp was meant to fix). `Fuel Quantity
>   Sensor` was removed and replaced with `Fuel Temperature Sensor`
>   (`<-37°C` → `Fuel_Temp_Freeze_Risk`, modeling Jet A freeze-point risk).
> - **Wing:** **Vibration + Strain Gauge** (not Strain Gauge alone).
>   Vibration Sensor is now genuinely shared by two categories, so
>   `ontology_reasoner.py`'s threshold branch became component-aware (new
>   `_component_category()` helper, ID-prefix based) - an Engine vibration
>   fault still reports `Vibration_Imbalance` / ATA_72, a Wing one now
>   correctly reports `Wing_Vibration_Excessive` / ATA_57, instead of both
>   being mislabeled as an engine issue.
>
> Verified with a direct unit test of `_evaluate_contextual_thresholds`:
> the same sensor reading on an Engine vs. a Wing component now correctly
> resolves to different fault labels and AMM references.

## 5. Consistency fixes so nothing still assumes only 3 sensors exist

- `app/camp_extensions/hitl_listener.py` had its own hardcoded
  `VALID_SENSOR_TYPES = {'Thermocouple', 'Vibration Sensor', 'Pressure
  Sensor'}` - a second, driftable copy of the sensor list. It now imports
  the keys from `SENSOR_TYPE_REGISTRY` directly, so a HIL rig packet for
  Oil Pressure/Fuel Quantity/Strain Gauge is no longer mislabeled
  `Accepted-UnknownSensorType`.
- `app/routes/fault_resolution.py`'s sensor-reset-on-resolve helper only
  had branches for the original 3 fault types; added Oil Pressure, Fuel
  Quantity, and Structural Overstrain so resolving *any* fault correctly
  resets its specific sensor to baseline.

## Files touched

**Edited:** `app/routes/telemetry.py` (full sensor framework rewrite),
`app/ontology_reasoner.py` (+2 threshold branches: Fuel Quantity, plus
Oil Pressure/Strain Gauge already present from the prior pass),
`app/routes/fault_resolution.py` (+3 reset branches),
`app/camp_extensions/hitl_listener.py` (sensor whitelist now imported, not
duplicated). **Database:** orphan `Components`/`SensorTelemetry` rows and
`Aircraft_TEST_N1` deleted; clean canonical component set re-seeded for all
4 real aircraft. **UI:** `templates/telemetry.html` unchanged in visual
style/layout - same colors, gauge-card design, table structure; only the
data driving it now varies correctly per aircraft's actual components.

---

# Round 3c: Login + Company Profile (Multi-Tenant-Ready Schema)

Scope agreed with head programmer: single-tenant login today (one company,
one shared fleet - identical behavior to before for existing data), but the
schema is laid down so a future multi-tenant rollout is additive, not a
second rewrite.

## 1. New tables (nothing existing touched)

- **`Companies`** - company name, hangar location, matched climate profile,
  corrosion category, ambient temp/humidity.
- **`Users`** - username/password (hashed with Werkzeug's
  `generate_password_hash`/`check_password_hash`, never stored in plain
  text), full name, role, `company_id`.
- **`AircraftDocuments`** - multi-document library per aircraft (manuals,
  registration, insurance, etc.), separate from the existing single
  `amm_pdf_path` field on `Aircraft`, which is untouched.

## 2. `company_id` added to the tables that need it, nothing else

Added via additive `ALTER TABLE ... ADD COLUMN` (same pattern already used
elsewhere in this codebase, e.g. `Components.csn`): `Aircraft`,
`Engineers`, `Personnel`, `MasterMEL`, `ToolCrib` - the tables with no
natural parent to scope through. Every existing row was defaulted to
`company_id = 1` (the seeded default company), so nothing that existed
before this change lost its data or broke.

Tables that already hang off `aircraft_id` (`Components`, `Schedule`,
`Faults`, `CRS_Records`, `MaintenanceHistory`, ...) were deliberately **not**
touched - a future multi-tenant rollout scopes those via a JOIN to
`Aircraft.company_id`, not a second column on ~20 more tables.

## 3. Login gate

A single `before_request` hook (`app/auth.py: register_auth_hooks`,
registered last in `create_app()` so it wraps every route including
extensions) redirects any unauthenticated request to `/login`, except
`/login`, `/register`, and static files. Verified with a test client: an
unauthenticated `GET /` returns `302 -> /login?next=/`; every existing page
(workspace, telemetry, calendar, MEL, personnel, environmental, HITL,
CAMSIS, ...) was re-tested after logging in and all returned `200`.

**Seeded default login (change immediately):** `admin` / `changeme123` -
printed as a startup warning the first time the app boots, same pattern as
the existing Pellet-timeout/ontology-fallback warnings.

## 4. Company Profile page (`/company-profile`)

- **Rename company.**
- **Set hangar location** by entering coordinates (or quick-picking one of
  8 built-in reference regions spanning West Africa, the Gulf, SE Asia,
  North America, Europe, Southern/North/East Africa) - matched to the
  *nearest* reference point by haversine distance, no paid geocoding API
  required. Match result + distance-from-reference shown immediately.
- **Fleet + document table**: every aircraft, its uploaded documents, and
  an inline upload form per aircraft (label, type, file) writing straight
  into `AircraftDocuments`.
- New companies land here immediately after registering
  (`onboarding=1` banner) - "at the beginning of their use of the
  software," per the original request.

## 5. Hangar location now genuinely feeds the corrosion model

Previously **every** aircraft silently used the same hardcoded environment
(`L7_WestAfrica_TropicalEnv`: 35°C/82%/C3) regardless of where it actually
operates. Now:

- `environmental_stressor.py: seed_default_context()` checks the owning
  company's matched hangar profile first, falling back to the old
  ontology default only if no hangar location has been set yet (so nothing
  changes for a company that hasn't configured one).
- `sync_company_environment()` re-applies the company's profile to
  *already-seeded* aircraft the moment the hangar location is set/changed,
  so existing fleet data isn't stuck with a stale value.
- The EGT heat-stressor threshold-tightening chain
  (`compute_adjusted_threshold`) is deliberately left pointing at the
  ontology's one documented environment individual - only the raw
  `corrosion_category`/`ambient_temp_c`/`humidity_pct` fields that feed
  `compute_corrosion_risk()` are overridden per company, since that's the
  actual "corrosion factor" this request was about.

Verified end-to-end: set a company's hangar to Gulf coordinates → matched
to `GulfCoast_AridMarine` (C4) → an existing aircraft's environment context
updated from C3→C4 immediately → `compute_corrosion_risk()` on a real
component scaled correctly with the new C4 weight (0.8 vs 0.6).

## 6. Files touched

**New:** `app/auth.py`, `app/routes/auth.py`, `templates/auth/auth_base.html`,
`templates/auth/login.html`, `templates/auth/register.html`,
`templates/auth/company_profile.html`.
**Edited:** `app/__init__.py` (+auth hook registration),
`app/routes/__init__.py` (+auth blueprint), `app/routes/workspace.py`
(`add_aircraft` now stamps `company_id`), `app/camp_extensions/environmental_stressor.py`
(+company hangar-location override, +sync function), `templates/base.html`
(+Company Profile nav link, +user/company/logout in top-nav - only shown
when logged in, layout otherwise unchanged).
**Database:** `Companies`/`Users`/`AircraftDocuments` created and seeded;
`company_id` column added to 5 existing tables, defaulted to the seeded
company so no existing data was affected.
