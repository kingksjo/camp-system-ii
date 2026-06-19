















Here's a full breakdown of how the "Live Sensor Telemetry" functionality works across the codebase:

---

## Overview

Despite the "Live" label in the UI, the telemetry system is **not a real-time streaming feed**. It's a **database-backed digital twin simulation** — sensor readings are stored in a `SensorTelemetry` SQLite table, displayed on the dashboard, and analyzed on-demand by the Pellet ontology reasoner.

---

## 1. Data Storage — `SensorTelemetry` Table

The table stores individual sensor readings with columns: `component_id`, `sensor_type`, `reading_value`, and `recorded_at` (auto-timestamped). It's **not created in code** — it was part of the initial database schema (likely seeded via a setup script in `archives/`).

Three sensor types are used throughout:
- **Thermocouple** — measures temperature (°C)
- **Vibration Sensor** — measures vibration (g-force)
- **Pressure Sensor** — measures fuel pressure (PSI)

---

## 2. Dashboard Display

The dashboard queries the latest 10 readings for the selected aircraft and renders them in a table:

```@c:\Users\Kamiye\Desktop\camp-system-ii\app\routes\dashboard.py:60-67
        # Get latest telemetry
        telemetry = conn.execute('''
            SELECT t.sensor_type, t.reading_value, t.recorded_at, c.component_id 
            FROM SensorTelemetry t 
            JOIN Components c ON t.component_id = c.component_id 
            WHERE c.aircraft_id = ? 
            ORDER BY t.recorded_at DESC LIMIT 10
        ''', (selected_tail,)).fetchall()
```

The template at `@c:\Users\Kamiye\Desktop\camp-system-ii\templates\dashboard.html:83-91` renders these in a table, with a simple conditional: readings above 900 are highlighted in red (`text-danger`), otherwise green (`text-success`).

---

## 3. Ontology Reasoner Analysis (Fault Detection)

When the user triggers "Run Reasoner" for an aircraft, the system:

1. **Fetches the single most recent reading per sensor per component** using a correlated subquery (`MAX(recorded_at)` grouped by `component_id` + `sensor_type`).

2. **Creates temporary OWL entities** in the ontology — an `AircraftComponent` and a `SensorData` instance — and assigns the reading value to `sensorValue`.

3. **Runs the Pellet reasoner** (`sync_reasoner_pellet`) to infer faults via SWRL rules in the ontology.

4. **Applies context-aware threshold checks** — three hardcoded rules that combine the Pellet inference with explicit numeric thresholds:

| Sensor Type | Threshold | Fault Detected | Severity | AMM Reference |
|---|---|---|---|---|
| Thermocouple | > 900.0°C | `Engine_Overheat_Critical` | Critical | ATA_77 |
| Vibration Sensor | > 4.5g | `Vibration_Imbalance` | High | ATA_72 |
| Pressure Sensor | < 20.0 PSI | `Fuel_Leak_Detected` | Critical | ATA_28 |

This logic exists in two places (duplicated):
- `@c:\Users\Kamiye\Desktop\camp-system-ii\app.py:654-670` (legacy monolithic route)
- `@c:\Users\Kamiye\Desktop\camp-system-ii\app\ontology_reasoner.py:97-149` (refactored `OntologyReasoner` class)

5. **Logs the result** to the `XAILogs` table (explainable AI audit trail) and **creates a `Faults` record** if a fault is detected and no unresolved fault of the same type already exists.

---

## 4. Digital Twin Sensor Reset (Fault Resolution)

When a fault is resolved, the system simulates the repair by injecting **nominal baseline readings** back into `SensorTelemetry`:

```@c:\Users\Kamiye\Desktop\camp-system-ii\app\routes\fault_resolution.py:108-124
def _update_sensor_readings(conn, fault):
    """Update sensor readings to simulate completed repair."""
    if "Overheat" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Thermocouple', 450.0)",
            (fault['component_id'],)
        )
    elif "Vibration" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Vibration Sensor', 1.2)",
            (fault['component_id'],)
        )
    elif "Leak" in fault['fault_type'] or "Pressure" in fault['fault_type']:
        conn.execute(
            "INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Pressure Sensor', 45.0)",
            (fault['component_id'],)
        )
```

The baseline values (450°C, 1.2g, 45 PSI) are all well within safe thresholds, so the next reasoner run will clear the aircraft for flight.

---

## 5. The Diagnostic Animation (Frontend)

The dashboard includes a JavaScript-powered diagnostic sequence at `@c:\Users\Kamiye\Desktop\camp-system-ii\templates\dashboard.html:537-548` that visually simulates the steps: "Fetching telemetry → Loading ontology → Running Pellet → Evaluating thresholds." This is a **UI animation only** — it runs on a `setTimeout` chain and doesn't make actual API calls.

---

## Summary

The telemetry system is a **closed-loop digital twin simulation**:

1. **Read** — Dashboard displays the latest sensor readings from the DB
2. **Analyze** — Reasoner checks readings against ontology SWRL rules + hardcoded thresholds
3. **Detect** — Faults are created in the `Faults` table if thresholds are breached
4. **Resolve** — When a fault is fixed, nominal readings are injected back into the DB
5. **Repeat** — Next reasoner run sees the repaired readings and clears the aircraft

There is **no live data ingestion** — no random generation, no external API, no periodic polling. All telemetry data is either pre-seeded or written during fault resolution.