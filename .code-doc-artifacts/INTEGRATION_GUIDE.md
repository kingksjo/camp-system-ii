# C.O.R.E. CAMP — Extension Pack Integration Guide

This document explains what was added to close the 9 Partial/Not-Found items
from the gap analysis, and — critically — **exactly what was and wasn't
touched** in your existing codebase.

## What was added

Everything new lives in two places:

```
app/camp_extensions/        <- all new backend logic + Flask blueprints
templates/extensions/       <- all new page templates
```

| # | Feature                              | Module(s)                                                     | New page(s)                  |
|---|---------------------------------------|----------------------------------------------------------------|-------------------------------|
| 4 | HITL FlightGear UDP listener          | `hitl_listener.py`, `routes_hitl.py`                            | `/hitl`                       |
| 9 | Interactive Hangar Schedule (FullCalendar) | `fullcalendar_schedule.py`                                 | `/schedule/fullcalendar`      |
| 10| Calendar Kill Switch                  | `kill_switch.py`, `routes_kill_switch.py`                       | `/killswitch`                 |
| 11| Digital Evidence Upload (geotag + hash chain) | `digital_evidence.py`, `routes_evidence.py`             | `/evidence`                   |
| 12| CAMSIS-3 Data Grounding                | `camsis.py`, `routes_camsis.py`                                 | `/camsis`                     |
| 13| Ghost Data Elimination                 | `ghost_data.py`, `routes_ghost_data.py`                         | `/ghost-data`                 |
| 18| IoT Smart Tool (Bluetooth torque)       | `iot_tools.py`, `routes_iot_tools.py`                           | `/iot-tools`                  |
| 19| RFID/QR Part Serial Scanning            | `parts_traceability.py`, `routes_parts.py`                     | `/parts`                      |
| 20| Environmental Stressor Logic (Layer 7) | `environmental_stressor.py`, `routes_environmental.py`          | `/environmental`               |

`app/camp_extensions/__init__.py` is the single registration point
(`register_camp_extensions(app)`) that wires all nine blueprints in and
starts the one background thread (the kill-switch watcher) automatically.

## What was touched in the existing codebase (and why)

Only three files, all additive:

1. **`app/__init__.py`** — 3 lines added inside `create_app()`:
   ```python
   from app.camp_extensions import register_camp_extensions
   register_camp_extensions(app)
   ```
   This is the only functional integration point. Nothing above it in the
   file was changed.

2. **`templates/base.html`** — a new sidebar section (9 links + a divider)
   appended after the existing "AI Reasoner Logs" link. No existing nav item
   was changed, removed, or reordered.

3. **`requirements.txt`** — one line appended: `Pillow` (used, optionally,
   to read GPS EXIF data off uploaded evidence photos; every extension
   degrades gracefully if it's missing).

This guide documents the original extension integration. Since then, Phase 1
of the database audit established `app/migrations.py` as the schema authority:
extension tables and additive columns are now applied by ordered startup
migrations, while `app/database.py` remains connection-only. The extension
folder is therefore part of the application schema and cannot be removed
without also removing its migrations and routes.

The FullCalendar columns `Schedule.source` and `Schedule.related_reference`
are now owned by migration `004_camp_extension_schema`, so they exist on both
fresh and upgraded databases. Every existing read/write against `Schedule` in
`app/routes/calendar.py` remains supported.

## How each feature actually closes its gap

- **HITL (#4)**: a real UDP socket server (`hitl_listener.py`) that inserts
  straight into `SensorTelemetry` — the same table `telemetry.py`, the
  dashboard, and the ontology reasoner already read. Verified end-to-end: a
  UDP packet sent to the listener shows up as a live row immediately. A
  ready-to-use FlightGear generic-protocol XML descriptor is downloadable
  from the `/hitl` page.

- **FullCalendar (#9)**: `/schedule/fullcalendar` is a genuine FullCalendar.js
  (CDN) drag/drop/resize calendar reading and writing the same `Schedule`
  table the existing weekly-grid view uses — both views stay in sync.

- **Kill Switch (#10)**: the real gap was that `fault_resolution.py`'s
  CRS sign-off never reached the `Schedule` table at all (only the manual
  "sign off schedule" button in `calendar.py` did). `kill_switch.py` runs a
  background watcher (every 10s) that reads new `CRS_Records`, matches them
  to open `Schedule`/`MEL_Deferrals` rows for the same aircraft, and
  auto-cancels them — with a full audit trail at `/killswitch`.

- **Digital Evidence (#11)**: uploads are chained per-aircraft with SHA-256
  (`prev_hash` → `sha256_hash`), anchored to a capture timestamp, and
  geotagged from image EXIF data (Pillow) or the browser's Geolocation API.
  `/evidence` shows live chain-integrity verification.

- **CAMSIS-3 (#12)**: a deterministic (non-AI) limits engine that compares
  real `Components`/`Aircraft` data (`csn`/`max_csn`, flight hours) against
  a seeded regulatory limits table — independent of, and complementary to,
  the probabilistic ontology/CBR reasoning already in the app.

- **Ghost Data (#13)**: replaces the archived `clear_ghosts.py` (which
  blindly closed *every* PilotReport) with a real detector for four ghost
  categories — stale PIREPs, orphaned telemetry, orphaned faults, and
  frozen/stuck sensors — with dry-run scanning and an audit log.

- **IoT Smart Tool (#18)**: a real Web Bluetooth GATT client (browser-side,
  Chrome/Edge) for BLE torque wrenches, plus a plain HTTP ingestion endpoint
  for gateway devices, both checked against a seeded torque-spec table.

- **RFID/QR (#19)**: real client-side QR generation (`qrcode.js`) and camera
  scanning (`html5-qrcode`), plus first-class support for off-the-shelf
  RFID/barcode "keyboard wedge" readers via an always-focused input field.

- **Environmental Stressor / Layer 7 (#20)**: the ontology already defines a
  full Layer 7 (operating environments, stressors, `stressorModifiesFailureMode`)
  that nothing in `ontology_reasoner.py` used. `environmental_stressor.py`
  reads it (with a documented fallback to the exact values already committed
  in `camp_multi_ontology.owl`) and completes the property-chain hop:
  Environment → Stressor → modified L3 failure mode → tightened sensor
  threshold, visible per-aircraft at `/environmental`.

## Optional deeper fusion (not applied — your call)

If you want the *live* ontology reasoner itself to use the Layer-7-adjusted
threshold instead of its fixed `>900.0` in
`_evaluate_contextual_thresholds()`, the one-line change is:

```python
# in app/ontology_reasoner.py, inside _evaluate_contextual_thresholds:
from app.camp_extensions.environmental_stressor import compute_adjusted_threshold
threshold, _ = compute_adjusted_threshold(aircraft_id, sensor_type, 900.0)
if sensor_type == 'Thermocouple' and (reading > threshold or ...):
```
This wasn't applied automatically since it's the one change that touches a
file you asked not to modify — happy to wire it in if you'd like it live.

## Running it

Nothing changes about how you run the app:

```
python run.py
```

You'll see the existing startup banner plus one new line confirming the
nine extensions loaded.

## Next

You mentioned a few more things you'd like built and integrated after this.
Send them over (or attach the updated project) and they'll be added the
same way — as additional modules under `app/camp_extensions/`, wired
through the same single `register_camp_extensions()` hook, with the same
"nothing existing gets rewritten" guarantee.
