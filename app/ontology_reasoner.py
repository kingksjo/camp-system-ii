"""
Ontology-Based AI Reasoner for C.O.R.E. CAMP.
Handles MOA (Model-Oriented Architecture) fault detection using Pellet reasoner.

PERFORMANCE FIX (see run-diagnostics freeze report):
Previously, `run_fleet_analysis()` created a brand new OntologyReasoner
(reloading both OWL files from disk) on every call, and then invoked the
external Pellet/Java reasoner (`sync_reasoner_pellet`) once PER telemetry
reading in a loop - so a 3-component aircraft with 3 sensors each meant 9
separate JVM spawns for a single "Run Diagnostics" click. Each Pellet
invocation costs real JVM startup time, so that loop is what made the
button "freeze" - the browser was simply waiting on a single, very slow,
un-timed-out synchronous HTTP request.

This version:
  1. Caches a single OntologyReasoner instance (get_reasoner()) so the OWL
     files are parsed once per process, not once per click.
  2. Batches ALL of an aircraft's telemetry readings into ONE Pellet
     invocation per run_fleet_analysis() call instead of one per reading.
  3. Runs that one Pellet call on a watchdog thread with a hard timeout, so
     a misconfigured/missing Java+Pellet install can never hang the request
     forever - past the timeout we fall back to plain L3 threshold
     evaluation (which is exactly what already happens whenever the
     ontology-inferred fault list is empty).
"""
import uuid
import threading
from datetime import datetime
from owlready2 import get_ontology, sync_reasoner_pellet, destroy_entity, onto_path
from app.database import get_db
from app.config import Config

PELLET_TIMEOUT_SECONDS = 15


def _component_category(component_id):
    """Fold a canonical component_id (Engine_L_..., FuelTank_C_..., Wing_R_...)
    into a coarse category, purely by ID prefix - no DB lookup needed here.
    Mirrors app/routes/telemetry.py's _category_for(), used for the one
    sensor type (Vibration Sensor) shared across two categories, where the
    correct AMM reference/fault label depends on which component tripped it."""
    if not component_id:
        return 'Unknown'
    cid = component_id.lower()
    if cid.startswith('wing'):
        return 'Wing'
    if cid.startswith('engine'):
        return 'Engine'
    if cid.startswith('fueltank'):
        return 'FuelTank'
    return 'Unknown'


class OntologyReasoner:
    """Manages ontology loading and AI fault detection."""

    def __init__(self):
        """Initialize ontology paths and load base ontologies."""
        onto_path.append(Config.ONTOLOGY_PATH)
        self.base_onto = None
        self.moa_onto = None
        self._load_ontologies()

    def _load_ontologies(self):
        """Load base and MOA ontologies."""
        try:
            self.base_onto = get_ontology(Config.BASE_ONTOLOGY).load()
            self.moa_onto = get_ontology(Config.MOA_ONTOLOGY).load()
        except Exception as e:
            print(f"⚠️ Error loading ontologies: {e}")
            print("Continuing without full ontology support...")

    def _run_pellet_with_timeout(self, timeout=PELLET_TIMEOUT_SECONDS):
        """
        Run the Pellet reasoner on a watchdog thread so a hung/missing
        Java+Pellet install can never block the caller forever. Returns
        True if Pellet completed within the timeout, False otherwise (the
        caller should fall back to threshold-only evaluation in that case).
        """
        outcome = {'done': False, 'error': None}

        def _worker():
            try:
                sync_reasoner_pellet(infer_property_values=True, infer_data_property_values=True)
                outcome['done'] = True
            except Exception as e:
                outcome['error'] = e

        worker_thread = threading.Thread(target=_worker, daemon=True)
        worker_thread.start()
        worker_thread.join(timeout=timeout)

        if worker_thread.is_alive():
            print(f"⚠️ Pellet reasoner exceeded {timeout}s - continuing with threshold-only "
                  f"evaluation (the reasoner thread will keep running in the background).")
            return False
        if outcome['error']:
            print(f"⚠️ Pellet reasoner error: {outcome['error']}")
            return False
        return outcome['done']

    def analyze_batch(self, readings, aircraft_id):
        """
        Analyze MULTIPLE telemetry readings with a SINGLE Pellet invocation.

        Args:
            readings (list[dict]): each with 'component_id', 'sensor_type', 'reading_value'
            aircraft_id (str): Aircraft identifier

        Returns:
            list[dict]: one analysis result per reading, same shape as the
                        previous analyze_telemetry() return value.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run_id = uuid.uuid4().hex[:8]

        results = []
        for r in readings:
            results.append({
                'component_id': r['component_id'],
                'sensor_type': r['sensor_type'],
                'reading': r['reading_value'],
                'timestamp': timestamp,
                'fault_detected': None,
                'severity': 'Normal',
                'amm_reference': 'ATA_05 (General Limits)',
                'explanation': f"[{timestamp}] Ontology analysis complete: {r['sensor_type']} parameters nominal.",
                'action': 'Cleared for Flight',
            })

        if not self.base_onto or not self.moa_onto or not readings:
            return results

        test_entities = []
        inferred_by_index = {i: [] for i in range(len(readings))}

        try:
            with self.moa_onto:
                # Create one temporary test entity PER reading, all inside the
                # SAME reasoning context, so a single Pellet call covers the
                # whole aircraft instead of one call per reading.
                for i, r in enumerate(readings):
                    test_comp = self.base_onto.AircraftComponent(f"Comp_{r['component_id']}_{run_id}_{i}")
                    test_sensor = self.base_onto.SensorData(f"Sens_{r['component_id']}_{run_id}_{i}")
                    test_comp.hasSensorData = [test_sensor]
                    test_sensor.sensorValue = float(r['reading_value'])
                    test_entities.append((test_comp, test_sensor))

                print(f"🧠 Running Pellet Reasoner ONCE for {len(readings)} reading(s) on {aircraft_id}...")
                pellet_ok = self._run_pellet_with_timeout()

                if pellet_ok:
                    for i, (test_comp, _) in enumerate(test_entities):
                        inferred_by_index[i] = [
                            f.name if hasattr(f, 'name') else str(f) for f in test_comp.hasFault
                        ]

                for i, r in enumerate(readings):
                    fault_info = self._evaluate_contextual_thresholds(
                        r['sensor_type'], r['reading_value'], r['component_id'],
                        inferred_by_index.get(i, []), timestamp
                    )
                    results[i].update(fault_info)

                # Cleanup - only entities we could actually create/reason over
                for test_comp, test_sensor in test_entities:
                    try:
                        destroy_entity(test_sensor)
                        destroy_entity(test_comp)
                    except Exception:
                        pass

        except Exception as e:
            print(f"⚠️ Reasoner error: {e}")

        return results

    def analyze_telemetry(self, component_id, sensor_type, reading_value, aircraft_id):
        """Single-reading convenience wrapper kept for backward compatibility."""
        return self.analyze_batch(
            [{'component_id': component_id, 'sensor_type': sensor_type, 'reading_value': reading_value}],
            aircraft_id
        )[0]

    def _evaluate_contextual_thresholds(self, sensor_type, reading, component_id, inferred_faults, timestamp):
        """
        Evaluate sensor readings against L3_Behavioral thresholds.

        Args:
            sensor_type (str): Type of sensor
            reading (float): Sensor reading value
            component_id (str): Component ID
            inferred_faults (list): Faults inferred by reasoner
            timestamp (str): Current timestamp

        Returns:
            dict: Updated fault detection info
        """
        result = {}

        if sensor_type == 'Thermocouple' and (reading > 900.0 or "OverTemp" in str(inferred_faults)):
            result = {
                'fault_detected': 'Engine_Overheat_Critical',
                'severity': 'Critical',
                'amm_reference': 'ATA_77 (Engine Indicating)',
                'action': 'Grounded Airframe',
                'explanation': (
                    f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>900°C) "
                    f"from {sensor_type} reading of {reading}°C on {component_id}."
                )
            }

        elif sensor_type == 'Vibration Sensor' and (reading > 4.5 or "Vibration" in str(inferred_faults)):
            # Round-3: Vibration Sensor is now shared by Engine AND Wing
            # components (confirmed fleet-reality mapping), so the fault
            # label/AMM reference has to follow which component tripped it
            # rather than assuming Engine every time.
            if _component_category(component_id) == 'Wing':
                result = {
                    'fault_detected': 'Wing_Vibration_Excessive',
                    'severity': 'High',
                    'amm_reference': 'ATA_57 (Wings)',
                    'action': 'Grounded Airframe',
                    'explanation': (
                        f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>4.5) "
                        f"from {sensor_type} reading of {reading}g on {component_id}."
                    )
                }
            else:
                result = {
                    'fault_detected': 'Vibration_Imbalance',
                    'severity': 'High',
                    'amm_reference': 'ATA_72 (Engine)',
                    'action': 'Grounded Airframe',
                    'explanation': (
                        f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>4.5) "
                        f"from {sensor_type} reading of {reading}g on {component_id}."
                    )
                }

        elif sensor_type == 'Fuel Pressure Sensor' and (reading < 20.0 or "Leak" in str(inferred_faults)):
            result = {
                'fault_detected': 'Fuel_Leak_Detected',
                'severity': 'Critical',
                'amm_reference': 'ATA_28 (Fuel Systems)',
                'action': 'Grounded Airframe',
                'explanation': (
                    f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (<20.0) "
                    f"from {sensor_type} reading of {reading} PSI on {component_id}."
                )
            }

        # --- Round-3 sensor framework additions ---------------------------
        # Additive only: new sensor types introduced by the per-component
        # sensor map in app/routes/telemetry.py (SENSOR_TYPE_REGISTRY). Same
        # pattern as the branches above - copy this shape when wiring
        # up a future sensor type into AI fault inference.
        elif sensor_type == 'Oil Pressure Sensor' and (reading < 25.0 or "OilPressure" in str(inferred_faults)):
            result = {
                'fault_detected': 'Engine_Oil_Pressure_Low',
                'severity': 'Critical',
                'amm_reference': 'ATA_79 (Engine Oil)',
                'action': 'Grounded Airframe',
                'explanation': (
                    f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (<25.0) "
                    f"from {sensor_type} reading of {reading} PSI on {component_id}."
                )
            }

        elif sensor_type == 'Strain Gauge' and (reading > 3.5 or "Overstrain" in str(inferred_faults)):
            result = {
                'fault_detected': 'Wing_Structural_Overstrain',
                'severity': 'High',
                'amm_reference': 'ATA_57 (Wings)',
                'action': 'Grounded Airframe',
                'explanation': (
                    f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>3.5) "
                    f"from {sensor_type} reading of {reading}k\u03bc\u03b5 on {component_id}."
                )
            }

        elif sensor_type == 'Fuel Temperature Sensor' and (reading < -37.0 or "FuelTempLow" in str(inferred_faults)):
            result = {
                'fault_detected': 'Fuel_Temp_Freeze_Risk',
                'severity': 'Critical',
                'amm_reference': 'ATA_28 (Fuel Systems)',
                'action': 'Grounded Airframe',
                'explanation': (
                    f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (<-37.0) "
                    f"from {sensor_type} reading of {reading}\u00b0C on {component_id} - approaching fuel freeze point."
                )
            }

        return result


_reasoner_singleton = None
_reasoner_lock = threading.Lock()


def get_reasoner():
    """Return a process-wide cached OntologyReasoner so OWL files are parsed once, not per-click."""
    global _reasoner_singleton
    if _reasoner_singleton is None:
        with _reasoner_lock:
            if _reasoner_singleton is None:
                _reasoner_singleton = OntologyReasoner()
    return _reasoner_singleton


def run_fleet_analysis(aircraft_id):
    """
    Run full ontology analysis on aircraft's latest telemetry.

    Args:
        aircraft_id (str): Aircraft to analyze

    Returns:
        list: Analysis results for all components
    """
    reasoner = get_reasoner()
    results = []

    with get_db() as conn:
        # Fetch latest telemetry readings
        latest_telemetry = conn.execute('''
            SELECT t1.reading_value, t1.sensor_type, c.component_id 
            FROM SensorTelemetry t1 
            JOIN Components c ON t1.component_id = c.component_id 
            WHERE c.aircraft_id = ? 
              AND t1.recorded_at = (
                  SELECT MAX(t2.recorded_at) 
                  FROM SensorTelemetry t2 
                  WHERE t2.component_id = t1.component_id 
                    AND t2.sensor_type = t1.sensor_type
              )
        ''', (aircraft_id,)).fetchall()

        if not latest_telemetry:
            print(f"⚠️ No telemetry data found for {aircraft_id}")
            conn.execute(
                'INSERT INTO XAILogs (component_id, ai_decision, explanation_text) VALUES (?, ?, ?)',
                ('System', 'Standby', f"No telemetry data found for {aircraft_id} to analyze.")
            )
            conn.commit()
            return results

        readings = [
            {'component_id': t['component_id'], 'sensor_type': t['sensor_type'], 'reading_value': float(t['reading_value'])}
            for t in latest_telemetry
        ]

        # One Pellet invocation for the whole batch instead of one per reading.
        results = reasoner.analyze_batch(readings, aircraft_id)

        for analysis in results:
            # Log analysis result
            conn.execute(
                'INSERT INTO XAILogs (component_id, ai_decision, explanation_text) VALUES (?, ?, ?)',
                (analysis['component_id'], analysis['action'], analysis['explanation'])
            )

            # Create fault if detected
            if analysis['fault_detected']:
                existing = conn.execute(
                    'SELECT * FROM Faults WHERE component_id = ? AND fault_type = ? AND resolved = 0',
                    (analysis['component_id'], analysis['fault_detected'])
                ).fetchone()

                if not existing:
                    conn.execute(
                        'INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference) '
                        'VALUES (?, ?, ?, 0, ?)',
                        (analysis['component_id'], analysis['fault_detected'],
                         analysis['severity'], analysis['amm_reference'])
                    )

        conn.commit()

    return results
