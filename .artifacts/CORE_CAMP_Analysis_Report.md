# C.O.R.E. CAMP - Codebase Architecture & Feature Analysis Report

Based on the provided `app.py` and project context, **C.O.R.E. CAMP** (Continuous Ontology Reasoning Engine) is an advanced, AI-driven Aircraft Maintenance Management (AMM) platform. It moves beyond standard CRUD-based management by deeply integrating **Semantic Web technologies (Ontologies)** and **Machine Learning (NLP)** to automate fault detection, enforce compliance, and intelligently assist aircraft engineers.

Below is a detailed breakdown of the system's functional components and how they are implemented within the codebase.

---

### 1. The XAI Ontology Reasoner (Digital Twin Engine)
This is the "brain" of the system. It simulates a digital twin by streaming sensor telemetry through a Semantic Web Reasoner to deduce aircraft faults automatically.

*   **How it works in code:**
    *   The `/run_reasoner/<aircraft_id>` route pulls the latest telemetry data (e.g., Temperature, Vibration) for a specific aircraft from the SQLite database.
    *   It uses `owlready2` to load an ontology stack (`camp.owl` and `camp_multi_ontology.owl`). 
    *   **Dynamic Entity Instantiation:** For each sensor reading, it dynamically creates temporary individuals (e.g., `AircraftComponent` and `SensorData`) within the ontology, assigns the sensor values via data properties, and attaches unique IDs to avoid IRI collisions.
    *   **Inference:** It triggers `sync_reasoner_pellet()`. The Pellet reasoner evaluates the sensor values against pre-defined SWRL (Semantic Web Rule Language) rules baked into the `.owl` files.
    *   **Actionable Output:** If the reasoner infers a fault class (e.g., `OverTemp`), the Python logic catches it, classifies the severity (e.g., `Engine_Overheat_Critical`), opens a system Fault, and logs an Explainable AI (XAI) rationale detailing *why* the fault was triggered based on threshold evaluations. Finally, it cleanly destroys the temporary ontology entities to prevent memory leaks.

### 2. Case-Based Reasoning (CBR) Semantic Engine
When a fault is detected, this system acts as an intelligent assistant for the mechanic. It searches historical maintenance logs to find precedent—how similar faults were resolved in the past.

*   **How it works in code:**
    *   Implemented primarily in the `retrieve_similar_cases()` function.
    *   It queries the `MaintenanceHistory` table for all past tasks on a specific aircraft.
    *   **NLP Vectorization:** It uses `scikit-learn`'s `TfidfVectorizer` to convert text descriptions of past faults and the current fault into mathematical vectors, stripping out common stop words.
    *   **Similarity Scoring:** It computes the `cosine_similarity` between the current fault vector and all historical vectors. 
    *   It filters results above a defined threshold (default 30%) and returns the top 3 highest-scoring historical cases, injecting them directly into the frontend Dashboard so mechanics have immediate reference material.

### 3. Ontology-Driven Compliance & Fault Resolution
The system doesn't just log that a fault was fixed; it cryptographically (conceptually via digital signatures) signs off the fix while enforcing legal compliance directly via the ontology.

*   **How it works in code:**
    *   Triggered via the `/resolve_fault/<int:fault_id>` route.
    *   **License Enforcement:** It parses the AMM reference chapter (e.g., "ATA_72") and queries the ontology to find the `requiresLicense` property for that specific chapter. It cross-references this with the mechanic's profile. If an airframe mechanic tries to sign off an avionics task, the system throws a `403 COMPLIANCE LOCKOUT`.
    *   **Digital Signatures & CRS:** Upon successful compliance, it generates a digital signature combining the mechanic's name, license, and physical stamp number. This signature is permanently written into a new Certificate of Release to Service (CRS) record and the global `MaintenanceHistory`.
    *   **Sensor Reset:** To complete the digital twin loop, resolving a fault injects "nominal" baseline telemetry back into the `SensorTelemetry` table, effectively simulating that the hardware has been repaired and is running normally again.

### 4. Smart Maintenance Tracking (Due List)
This module tracks routine, scheduled maintenance tasks (e.g., inspections that occur every 500 hours) against the live telemetry of the fleet.

*   **How it works in code:**
    *   Located in the `/due_list` route.
    *   It iterates through every aircraft in the fleet and cross-references them against all `MaintenanceTasks`.
    *   It checks the `MaintenanceHistory` to see if a task is already "COMPLETED".
    *   **Projection Math:** If a task requires a 500-hour interval, it calculates the next due point using modulo arithmetic: `(((current_hrs // interval) + 1) * interval) - current_hrs`.
    *   It dynamically assigns status tags ("OVERDUE", "DUE SOON", or "GOOD") based on whether the remaining time/cycles dip below specific thresholds.

### 5. Self-Healing Database Architecture
The application ensures that its SQLite backend is always structurally up-to-date without requiring users to run manual migration scripts or use tools like Alembic.

*   **How it works in code:**
    *   The `get_db_connection()` function is called upon almost every request.
    *   Instead of just returning a connection, it executes a series of `ALTER TABLE ... ADD COLUMN` and `CREATE TABLE IF NOT EXISTS` commands wrapped in `try...except sqlite3.OperationalError` blocks.
    *   If a column or table doesn't exist, it is instantly created. If it already exists, the error is quietly caught and ignored, keeping the connection logic incredibly robust and portable.

### 6. Sub-Systems (MEL, Tool Crib, and PIREPs)
The system rounds out standard CAMO (Continuing Airworthiness Management Organization) requirements with dedicated modules:

*   **Minimum Equipment List (MEL):** The `/mel` route manages deferred maintenance. It categorizes defects (A, B, C, D) and uses `datetime` calculations to track how many days a deferral has left before the aircraft is legally grounded.
*   **Digital Tool Crib:** The `/tool_crib` routes track precision equipment. It uses date mathematics to flag tools whose `calibration_due` date has expired, allowing tools to be "Checked Out", "Available", or "Quarantined".
*   **Pilot Reports (PIREP):** The `/flight_log` allows flight crews to report snags. When a PIREP is submitted, the system automatically translates this into an open `Fault` in the database, mapping it to a virtual `Airframe` component so mechanics immediately see it on their dashboard.

### Summary
C.O.R.E. CAMP is a highly intelligent, tightly-coupled Flask application. Its standout architectural decision is offloading rigid logic (like compliance and threshold rules) into an OWL Ontology rather than hardcoding it into Python `if/else` statements. This, combined with the scikit-learn CBR engine, creates a platform that actively thinks, prevents human error, and learns from its own history.