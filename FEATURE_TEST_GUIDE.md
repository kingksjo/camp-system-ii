# C.O.R.E. CAMP Feature Test Guide

Use this guide after running the refactored application from `run.py` to verify that migrated and repaired features work end to end.

## 1. Environment setup

1. Open a terminal in the project root.
2. Install Python dependencies:

```powershell
pip install -r requirements.txt
```

3. Confirm Java is installed and available on `PATH` because Owlready2 uses the Pellet reasoner:

```powershell
java -version
```

4. Start the application:

```powershell
python run.py
```

5. Open:

```text
http://127.0.0.1:5000
```

## 2. Navigation smoke test

Visit each sidebar page and confirm it renders without a 500 error:

- **Fleet Dashboard:** `/`
- **CAMO Workspace:** `/workspace`
- **Pilot Discrepancies:** `/flight_log`
- **Smart Due List:** `/due_list`
- **Maintenance Log:** `/history`
- **Hangar Schedule:** `/calendar`
- **MEL Tracker:** `/mel`
- **Digital Tool Crib:** `/tool_crib`
- **Authorized Personnel:** `/personnel`
- **AI Reasoner Logs:** `/xai_reasoner`

Expected result: every page loads and all forms/buttons are visible.

## 3. Personnel and sign-off prerequisites

1. Go to `/personnel`.
2. Add at least one engineer with:
   - `emp_id`
   - full name
   - license type
   - license number
   - stamp number
3. Confirm the engineer appears in the roster.

Expected result: the engineer is available in sign-off dropdowns on dashboard, due list, calendar, MEL, and tool crib pages.

## 4. CAMO Workspace tests

### Add aircraft

1. Go to `/workspace`.
2. Add a new aircraft with registration, model, hours, and cycles.
3. Optionally upload a PDF AMM.

Expected result: aircraft appears in the fleet roster and dashboard aircraft filter.

### Update AMM

1. Select an existing aircraft.
2. Upload a new AMM PDF.
3. Return to the dashboard for that aircraft.

Expected result: dashboard shows an AMM document link when a path is stored.

### Add directive

1. Add an AD or SB for a target model.
2. Optionally attach a PDF.

Expected result: directive appears on the dashboard for aircraft of the selected model.

### Add maintenance task

1. Add a task with `task_id`, task name, interval hours and/or cycles, and target model.
2. Go to `/due_list`.

Expected result: task appears in the due list with `GOOD`, `DUE SOON`, `OVERDUE`, or `COMPLETED` status.

### SWRL rule editor

1. Use the Natural Language Rule Editor on `/workspace`.
2. Submit a rule name and plain-English rule body.

Expected result: the app shows `SWRL Rule Captured for Review`, stores the rule in `SWRLRules`, and adds an XAI log entry. This flow intentionally stores rules for review rather than directly mutating ontology files.

## 5. AI reasoner and XAI logs

### Run diagnostics with telemetry

1. Open the dashboard for an aircraft with telemetry.
2. Click `Run AI Diagnostics`.
3. Wait for the diagnostics modal to complete and redirect.
4. Open `/xai_reasoner`.

Expected result: XAI logs show AI decisions. If telemetry exceeds thresholds, unresolved faults appear on the dashboard.

Thresholds to verify:

- **Thermocouple:** reading above `900.0` creates `Engine_Overheat_Critical`.
- **Vibration Sensor:** reading above `4.5` creates `Vibration_Imbalance`.
- **Pressure Sensor:** reading below `20.0` creates `Fuel_Leak_Detected`.

### Run diagnostics without telemetry

1. Run diagnostics for an aircraft with no telemetry.
2. Open `/xai_reasoner`.

Expected result: an XAI log entry appears with `System`, `Standby`, and a message that no telemetry was found.

## 6. Fault resolution and CRS

1. Ensure the dashboard has at least one active fault.
2. Select an authorized AME from the fault resolution dropdown.
3. Click `Sign`.

Expected result:

- Fault is marked resolved.
- A CRS record is inserted into `CRS_Records`.
- A maintenance history entry is inserted into `MaintenanceHistory`.
- A sensor reset reading is inserted for overheat, vibration, leak, or pressure faults.
- If the fault came from a PIREP, the related `PilotReports` row is closed.
- `/history` shows both maintenance history and CRS records.

## 7. Pilot discrepancy / PIREP flow

1. Go to `/flight_log`.
2. Submit a new discrepancy for an aircraft.
3. Return to the dashboard for that aircraft.

Expected result:

- A `PilotReports` row is created.
- A matching unresolved `Faults` row is created with an `amm_reference` like `PIREP_ID_<id>`.
- Resolving that dashboard fault closes the PIREP.

## 8. Smart Due List flow

1. Go to `/due_list`.
2. Confirm maintenance tasks show one of:
   - `OVERDUE`
   - `DUE SOON`
   - `GOOD`
   - `COMPLETED`
3. For `OVERDUE` or `DUE SOON` rows, select an engineer and sign off.
4. Reload `/due_list` and open `/history`.

Expected result:

- The task becomes completed for that aircraft/task pair.
- A `MaintenanceHistory` row is created with `Completed <task_id>: <task_name>`.

## 9. Hangar schedule flow

1. Go to `/calendar`.
2. Schedule an A-Check, B-Check, or C-Check.
3. Confirm it appears under active work orders.
4. Select an engineer and release/sign off the work order.
5. Open `/history`.

Expected result:

- The schedule row changes to `Completed`.
- A maintenance history row is created with `Hangar Check: ...`.

## 10. MEL tracker flow

1. Go to `/mel`.
2. Create a Category B, C, or D deferral.
3. Confirm days remaining are calculated.
4. Select an engineer and sign off the clearance.
5. Open `/history`.

Expected result:

- Deferral status changes to `Resolved`.
- A maintenance history row is created with `Cleared MEL Deferral: ...`.

## 11. Digital Tool Crib flow

1. Go to `/tool_crib`.
2. Add a tool with or without a calibration date.
3. Check out the tool to an engineer.
4. Return/check in the tool.
5. Recalibrate the tool if it has a calibration date.
6. Quarantine the tool.
7. Remove the tool.

Expected result:

- Status transitions correctly between `Available`, `Checked Out`, and `Quarantined`.
- Calibration due labels display as valid, due soon, expired, or N/A.
- Filtering by category works.

## 12. Database verification queries

Use SQLite to inspect key records if needed:

```powershell
python -c "import sqlite3; c=sqlite3.connect('camp_system.db'); c.row_factory=sqlite3.Row; print([dict(r) for r in c.execute('SELECT * FROM XAILogs ORDER BY id DESC LIMIT 5')])"
```

Suggested tables to verify:

- **Faults:** unresolved/resolved fault state.
- **MaintenanceHistory:** sign-off logs.
- **CRS_Records:** fault release certificates.
- **XAILogs:** AI reasoner and SWRL editor events.
- **PilotReports:** PIREP open/closed status.
- **Schedule:** scheduled/completed work orders.
- **MEL_Deferrals:** active/resolved deferrals.
- **ToolCrib:** tool status and calibration data.
- **SWRLRules:** safely captured rule-editor submissions.

## 13. Known prerequisites and troubleshooting

- **Missing Flask or packages:** run `pip install -r requirements.txt`.
- **Pellet/Java errors:** install Java and confirm `java -version` works.
- **SQLite lock errors:** stop duplicate Flask servers and retry.
- **No reasoner faults created:** confirm telemetry exists and readings cross configured thresholds.
- **No engineers in dropdowns:** add personnel first from `/personnel`.
