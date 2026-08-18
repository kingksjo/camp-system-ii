# C.O.R.E. CAMP — Round 2: Bug Fixes & Feature Requests

This round was different from the extension pack: several of these are real
bugs in the base code, so (per your instruction this time) they're fixed
directly at the source rather than worked around. Only existing **UI** was
left alone; a few existing **backend** files were edited where that was the
only honest fix. Every touched file is listed below.

## 1. Run Diagnostics button freezing / long inference time

**Root cause (two compounding bugs):**
- `app/ontology_reasoner.py` invoked the external Pellet/Java reasoner
  **once per telemetry reading** in a loop. An aircraft with 3 components
  x 3 sensors meant 9 separate JVM spawns for one click.
- `templates/dashboard.html`'s diagnostics modal played a 7.4s cosmetic
  animation, then did a **blind, blocking full-page form submit** with no
  further feedback - so once the real (slow) work started, the tab looked
  exactly like it had frozen.

**Fix:**
- `app/ontology_reasoner.py`: batches all of an aircraft's readings into a
  **single** Pellet invocation per diagnostics run, and caches the loaded
  ontology across requests instead of re-parsing the OWL files every time.
  The Pellet call also now runs on a watchdog thread with a 15s timeout, so
  a broken/missing Java+Pellet install can never hang the server forever.
- `app/diagnostics_jobs.py` (new): runs the analysis on a background thread
  and tracks status in a new `DiagnosticJobs` table.
- `app/routes/reasoner.py`: `/run_reasoner/<id>` now returns a `job_id`
  immediately for AJAX callers; `/api/reasoner/status/<job_id>` is polled
  for completion. A synchronous fallback is kept for non-JS callers.
- `templates/dashboard.html`: only the `<script>` block changed - the
  modal, its animation, and all visible styling are pixel-identical. The
  real job now starts immediately (in parallel with the animation) and the
  page only navigates once it's actually confirmed done (capped at +20s so
  it can never hang indefinitely either way).

## 2. Time-bound schedule events (reminders + auto-removal)

New: `app/camp_extensions/schedule_lifecycle.py` + `routes_schedule_reminders.py`.
- A background watcher fires a reminder the moment a scheduled event's
  start time arrives - surfaced as a small toast (bottom-right, polled
  every 20s) added to the very end of `templates/base.html`. Nothing
  existing in base.html was changed, only appended.
- The same watcher auto-expires (`status = 'Expired-AutoRemoved'`) any
  event more than **2 days** past its end time with no sign-off. Signing
  off (existing calendar.py flow, or the new FullCalendar page) still
  removes it immediately, same as before - this just adds the "or after 2
  days" half that was missing.

## 3. Cross sign-off prevention (license-gated sign-offs)

New: `app/license_compliance.py` - a deterministic license-vs-task-type gate.
- `app/routes/fault_resolution.py`: the existing ontology-driven license
  check silently allowed everything through whenever the ontology couldn't
  produce an answer (missing ontology, unmapped ATA chapter). It now falls
  back to a deterministic ATA-chapter → license table instead of skipping
  the check.
- `app/routes/calendar.py`: `sign_off_schedule()` had **no license check at
  all** - any engineer_id could sign off any A/B/C-Check. It now requires
  the appropriate EASA Part-66 category (or FAA A&P) per check type.
- `app/routes/mel.py`: MEL clearance sign-off gets the same treatment when
  the deferral references a Master MEL item with a known ATA chapter.
- One engineer was added to the seed data (`EASA Part-66 C`, since none
  existed - without one, C-Checks could never be signed off at all under
  the new rule). Nothing else in `Engineers` was changed.

## 4. Maintenance document release (paper audit) + component framework

New: `app/camp_extensions/maintenance_documents.py` (PDF generation via
reportlab) + `routes_maintenance_documents.py` + `/maintenance-documents`
(the permanent document register/"record log").
- `templates/history.html` gained a **Document** column with a "PDF" button
  per row (CRS releases and maintenance-log/resolved-fault entries alike) -
  the only UI change for this feature, and it's additive (existing columns
  untouched).
- `app/routes/history.py`: the underlying query now exposes a stable
  `source_type`/`source_id` per row so each entry can be linked to its own
  document.
- **Component framework**: the PDF's "Component Technical Record" section
  is driven by `COMPONENT_TECHNICAL_FIELDS`, a dict keyed by component
  type. Adding support for a new aircraft component (APU, avionics LRU,
  etc.) to future documents is a one-line dict entry, not new code.

## 5. Master Minimum Equipment List (MMEL)

New `MasterMEL` table + CAMO workspace inputter.
- `templates/workspace.html`: new "MMEL Inputter" panel (matches the
  existing panel style exactly) where CAMO staff define MMEL items per
  aircraft **model** (item, ATA chapter, category, max deferral days,
  remarks) - same pattern as the existing directive/task panels.
- `app/routes/workspace.py`: `/add_mmel_item`, `/remove_mmel_item/<id>`.
- `app/routes/mel.py` + `templates/mel.html`: the deferral form gained a
  "Reference Master MEL Item" dropdown (filtered by the selected aircraft's
  model) that auto-fills the description/category from the master list.
  Manual/ad-hoc entries are still fully supported for anything not yet on
  the master list - nothing about the old flow was removed.

## 6. Pilot discrepancy (PIREP) not closing when its fault is resolved

**Root cause:** `app/routes/fault_resolution.py` ran
`UPDATE PilotReports SET status = 'Closed' WHERE id = ?` - but
`PilotReports`' primary key column is `report_id`, not `id`. Every call
raised `sqlite3.OperationalError`, silently swallowed by a bare
`except Exception: pass`, so the PIREP never actually closed.

**Fix:** one-line correction to `WHERE report_id = ?`. Verified end-to-end:
resolving a fault now flips its linked PIREP to `Closed` immediately.

## 7. Telemetry page not auto-selecting the dashboard's chosen aircraft

The telemetry route already supported `?tail=<aircraft_id>` - it just had
no link that used it. Fixed with a one-line change to the **Sensor
Telemetry** sidebar link in `templates/base.html`: it now carries over
`?tail=` from wherever you currently are (e.g. the dashboard), so picking
an aircraft on the dashboard and then clicking "Sensor Telemetry" lands on
that same aircraft automatically. Nothing else on either page changed.

## 8. Ability to schedule a flight from the hangar schedule / work order page

"Hangar Schedule & Work Orders" is `calendar.py`/`calendar.html` - left
completely untouched as asked. `Flight` was added as a selectable event
type on the FullCalendar page built earlier (`/schedule/fullcalendar`),
which reads and writes the exact same `Schedule` table. Verified: a flight
scheduled there shows up correctly on the original hangar schedule page
with zero changes to that page.

## Files touched this round (full list)

**New files:**
`app/diagnostics_jobs.py`, `app/license_compliance.py`,
`app/camp_extensions/schedule_lifecycle.py`,
`app/camp_extensions/routes_schedule_reminders.py`,
`app/camp_extensions/maintenance_documents.py`,
`app/camp_extensions/routes_maintenance_documents.py`,
`templates/extensions/maintenance_documents.html`

**Edited (backend logic - not UI):**
`app/ontology_reasoner.py`, `app/routes/reasoner.py`,
`app/routes/fault_resolution.py`, `app/routes/calendar.py` (logic only),
`app/routes/mel.py`, `app/routes/workspace.py` (logic only),
`app/routes/history.py` (query only), `app/camp_extensions/__init__.py`,
`app/camp_extensions/fullcalendar_schedule.py` (added Flight to color map)

**Edited (UI - only where the request explicitly required it):**
`templates/dashboard.html` (`<script>` block only, see #1),
`templates/base.html` (appended reminder toast + one nav href tweak, #2/#7),
`templates/history.html` (added Document column, #4),
`templates/workspace.html` (added MMEL panel, #5),
`templates/mel.html` (added MMEL reference selector, #5),
`templates/extensions/fullcalendar_schedule.html` (added Flight option, #8)

**Data:** one Category-C engineer added to `Engineers` (see #3).

**requirements.txt:** added `reportlab` (PDF generation, #4).
