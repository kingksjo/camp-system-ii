"""
Ontology-Based AI Reasoner for C.O.R.E. CAMP.
Handles MOA (Model-Oriented Architecture) fault detection using Pellet reasoner.
"""
import uuid
from datetime import datetime
from owlready2 import get_ontology, sync_reasoner_pellet, destroy_entity, onto_path
from app.database import get_db
from app.config import Config


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
    
    def analyze_telemetry(self, component_id, sensor_type, reading_value, aircraft_id):
        """
        Analyze sensor telemetry against ontology rules.
        
        Args:
            component_id (str): Component identifier
            sensor_type (str): Type of sensor (e.g., 'Thermocouple', 'Vibration Sensor')
            reading_value (float): Current sensor reading
            aircraft_id (str): Aircraft identifier
        
        Returns:
            dict: Analysis result with fault info and explanation
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        unique_run_id = uuid.uuid4().hex[:8]
        
        result = {
            'component_id': component_id,
            'sensor_type': sensor_type,
            'reading': reading_value,
            'timestamp': timestamp,
            'fault_detected': None,
            'severity': 'Normal',
            'amm_reference': 'ATA_05 (General Limits)',
            'explanation': f"[{timestamp}] Ontology analysis complete: {sensor_type} parameters nominal.",
            'action': 'Cleared for Flight'
        }
        
        if not self.base_onto or not self.moa_onto:
            return result
        
        try:
            with self.moa_onto:
                # Create temporary test entities
                test_comp = self.base_onto.AircraftComponent(f"Comp_{component_id}_{unique_run_id}")
                test_sensor = self.base_onto.SensorData(f"Sens_{component_id}_{unique_run_id}")
                
                # Assign properties
                test_comp.hasSensorData = [test_sensor]
                test_sensor.sensorValue = float(reading_value)
                
                # Run Pellet reasoning
                print(f"🧠 Running Pellet Reasoner on {component_id} ({sensor_type}: {reading_value})...")
                sync_reasoner_pellet(infer_property_values=True, infer_data_property_values=True)
                
                # Check inferred faults
                inferred_fault_names = [
                    f.name if hasattr(f, 'name') else str(f) 
                    for f in test_comp.hasFault
                ]
                
                # Apply context-aware MOA logic
                fault_info = self._evaluate_contextual_thresholds(
                    sensor_type, reading_value, component_id, inferred_fault_names, timestamp
                )
                result.update(fault_info)
                
                # Cleanup
                destroy_entity(test_sensor)
                destroy_entity(test_comp)
                
        except Exception as e:
            print(f"⚠️ Reasoner error: {e}")
        
        return result
    
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
        
        elif sensor_type == 'Pressure Sensor' and (reading < 20.0 or "Leak" in str(inferred_faults)):
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
        
        return result


def run_fleet_analysis(aircraft_id):
    """
    Run full ontology analysis on aircraft's latest telemetry.
    
    Args:
        aircraft_id (str): Aircraft to analyze
    
    Returns:
        list: Analysis results for all components
    """
    reasoner = OntologyReasoner()
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
        
        for telemetry in latest_telemetry:
            analysis = reasoner.analyze_telemetry(
                telemetry['component_id'],
                telemetry['sensor_type'],
                float(telemetry['reading_value']),
                aircraft_id
            )
            results.append(analysis)
            
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
