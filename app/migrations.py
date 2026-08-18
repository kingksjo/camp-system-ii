"""
Versioned schema migrations - the single schema authority for C.O.R.E. CAMP.

Phase 1 of the database audit (see DATABASE_AUDIT_GUIDELINES.md):

- every CREATE / ALTER and every seed row now lives in this module;
- run_migrations() applies pending migrations once, in order, and records
  each one in the ``schema_migrations`` table;
- migrations run at application startup (app/__init__.py) and the common
  request-time path is a read-only no-op once the schema is current;
- unexpected errors fail loudly - no more swallowed OperationalError;
- only the known "duplicate column name" condition is tolerated, inside the
  guarded _add_column() helper used by additive ALTERs;
- the Round-3 destructive component/sensor revamp is retired. The marker
  table is still created so remaining tooling finds it, but migrations
  never delete or rewrite operational data.

Each migration function runs inside one transaction against the connection
passed in by the runner - it must NOT open its own connection (SQLite
write-lock ordering makes that a deadlock risk).
"""
import sqlite3
import time

from werkzeug.security import generate_password_hash

from app.config import Config
from app.auth import (
    DEFAULT_COMPANY_ID,
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_ADMIN_PASSWORD,
    REFERENCE_CLIMATE_PROFILES,
)

# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


def _connect():
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=Config.DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # autocommit; transactions managed explicitly
    return conn


def _begin_immediate(conn):
    """Acquire the SQLite write lock with a short retry window for busy cases."""
    for attempt in range(3):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise


def _ensure_migrations_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _applied_versions(conn):
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def run_migrations():
    """Apply every pending migration once. Idempotent; raises on any failure."""
    conn = _connect()
    try:
        _ensure_migrations_table(conn)
        if _applied_versions(conn) >= {m["version"] for m in MIGRATIONS}:
            return  # fast path: schema is already current (read-only)

        _begin_immediate(conn)
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        for migration in MIGRATIONS:
            if migration["version"] in applied:
                continue
            migration["apply"](conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration["version"], migration["name"]),
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _add_column(conn, table, column, ddl):
    """Additive ALTER - the ONLY tolerated failure is an existing column."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


# ---------------------------------------------------------------------------
# Migration 1: core operational schema
# ---------------------------------------------------------------------------

_CORE_TABLES = [
    # Aircraft fleet register.
    """CREATE TABLE IF NOT EXISTS Aircraft (
        aircraft_id TEXT PRIMARY KEY,
        registration TEXT,
        model TEXT,
        total_flight_hours REAL,
        total_cycles INTEGER,
        amm_pdf_path TEXT DEFAULT '',
        company_id INTEGER DEFAULT 1
    )""",
    # Installed components, one row per physical component.
    """CREATE TABLE IF NOT EXISTS Components (
        component_id TEXT PRIMARY KEY,
        aircraft_id TEXT,
        component_type TEXT,
        serial_number TEXT,
        total_flight_hours REAL,
        total_flight_cycles INTEGER,
        time_since_overhaul REAL,
        csn INTEGER DEFAULT 0,
        max_csn INTEGER DEFAULT 5000,
        FOREIGN KEY (aircraft_id) REFERENCES Aircraft(aircraft_id)
    )""",
    # Sensor readings ingested from telemetry / HITL rigs.
    """CREATE TABLE IF NOT EXISTS SensorTelemetry (
        telemetry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id TEXT,
        sensor_type TEXT,
        reading_value REAL,
        recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (component_id) REFERENCES Components(component_id)
    )""",
    # Faults detected by the ontology reasoner / diagnostics.
    """CREATE TABLE IF NOT EXISTS Faults (
        fault_id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id TEXT,
        telemetry_id INTEGER,
        fault_type TEXT,
        severity TEXT,
        detected_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        resolved BOOLEAN DEFAULT 0,
        amm_reference TEXT DEFAULT "Pending AI Review",
        resolved_by TEXT,
        resolved_date TEXT,
        FOREIGN KEY (component_id) REFERENCES Components(component_id),
        FOREIGN KEY (telemetry_id) REFERENCES SensorTelemetry(telemetry_id)
    )""",
    # Hangar schedule events (calendar.py / fullcalendar_schedule.py).
    """CREATE TABLE IF NOT EXISTS Schedule (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT,
        event_type TEXT,
        title TEXT,
        start_time DATETIME,
        end_time DATETIME,
        color TEXT,
        status TEXT DEFAULT 'Scheduled'
    )""",
    # MEL (Minimum Equipment List) deferrals.
    """CREATE TABLE IF NOT EXISTS MEL_Deferrals (
        deferral_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT,
        item_description TEXT,
        mel_category TEXT,
        date_deferred DATETIME,
        status TEXT DEFAULT 'Active',
        mmel_id INTEGER
    )""",
    # Maintenance completion history (signed-off work).
    """CREATE TABLE IF NOT EXISTS MaintenanceHistory (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_reg TEXT,
        task_description TEXT,
        signed_off_by TEXT,
        sign_off_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        xai_reference TEXT,
        completion_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Task library with intervals.
    """CREATE TABLE IF NOT EXISTS MaintenanceTasks (
        task_id TEXT PRIMARY KEY,
        task_name TEXT,
        task_category TEXT,
        interval_hours REAL,
        interval_cycles INTEGER,
        interval_months INTEGER,
        target_model TEXT DEFAULT 'ALL'
    )""",
    # Per-instance maintenance records (with digital signatures).
    """CREATE TABLE IF NOT EXISTS MaintenanceRecords (
        record_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        component_id TEXT,
        fault_id INTEGER,
        performed_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        mechanic_id TEXT,
        digital_signature TEXT,
        status TEXT,
        FOREIGN KEY (task_id) REFERENCES MaintenanceTasks(task_id),
        FOREIGN KEY (component_id) REFERENCES Components(component_id),
        FOREIGN KEY (fault_id) REFERENCES Faults(fault_id)
    )""",
    # AD / SB / ICA directive register.
    """CREATE TABLE IF NOT EXISTS Directives (
        directive_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_model TEXT,
        doc_type TEXT,
        ref_number TEXT,
        description TEXT,
        status TEXT DEFAULT 'Open',
        date_issued DATETIME DEFAULT CURRENT_TIMESTAMP,
        pdf_path TEXT DEFAULT ''
    )""",
    # Licensed engineers.
    """CREATE TABLE IF NOT EXISTS Engineers (
        emp_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        license_type TEXT,
        stamp_number TEXT,
        status TEXT DEFAULT 'Active',
        license_number TEXT DEFAULT 'PENDING',
        company_id INTEGER DEFAULT 1
    )""",
    # Company personnel register.
    """CREATE TABLE IF NOT EXISTS Personnel (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        license_type TEXT NOT NULL,
        company_id INTEGER DEFAULT 1
    )""",
    # Tool crib inventory.
    """CREATE TABLE IF NOT EXISTS ToolCrib (
        tool_id TEXT PRIMARY KEY,
        tool_name TEXT,
        category TEXT,
        calibration_due DATE,
        status TEXT DEFAULT 'Available',
        checked_out_to TEXT DEFAULT '',
        company_id INTEGER DEFAULT 1
    )""",
    # Legal sign-offs against faults.
    """CREATE TABLE IF NOT EXISTS LegalSignOffs (
        signoff_id INTEGER PRIMARY KEY AUTOINCREMENT,
        fault_id INTEGER,
        engineer_name TEXT,
        license_number TEXT,
        signoff_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (fault_id) REFERENCES Faults(fault_id)
    )""",
    # Pilot discrepancy reports.
    """CREATE TABLE IF NOT EXISTS PilotReports (
        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT,
        reported_by TEXT,
        discrepancy_text TEXT,
        date_submitted DATETIME DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Open',
        FOREIGN KEY (aircraft_id) REFERENCES Aircraft(aircraft_id)
    )""",
    # XAI decision audit log (also used by the IoT/environmental extensions).
    """CREATE TABLE IF NOT EXISTS XAILogs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        fault_id INTEGER,
        component_id TEXT,
        sensor_reading REAL,
        rule_triggered TEXT,
        human_explanation TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        ai_decision TEXT DEFAULT '',
        explanation_text TEXT DEFAULT '',
        FOREIGN KEY (fault_id) REFERENCES Faults(fault_id)
    )""",
    # Certificate of Release to Service records.
    """CREATE TABLE IF NOT EXISTS CRS_Records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_reg TEXT,
        reference_id TEXT,
        description TEXT,
        signed_off_by TEXT,
        release_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # SWRL rule register used by the ontology reasoner.
    """CREATE TABLE IF NOT EXISTS SWRLRules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name TEXT NOT NULL,
        rule_body TEXT NOT NULL,
        status TEXT DEFAULT 'Pending Review',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Background AI diagnostics jobs (app/diagnostics_jobs.py).
    """CREATE TABLE IF NOT EXISTS DiagnosticJobs (
        job_id TEXT PRIMARY KEY,
        aircraft_id TEXT,
        status TEXT DEFAULT 'Running',
        fault_count INTEGER,
        error_message TEXT,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME
    )""",
]

# Additive columns that older databases may be missing. New databases get
# them straight from the CREATE statements above; the guarded ALTER is the
# upgrade path for databases created before the column existed.
_CORE_LEGACY_COLUMNS = [
    ('MEL_Deferrals', 'mmel_id', 'INTEGER'),
    ('Directives', 'pdf_path', "TEXT DEFAULT ''"),
]


def _migration_001_core(conn):
    for stmt in _CORE_TABLES:
        conn.execute(stmt)
    for table, column, ddl in _CORE_LEGACY_COLUMNS:
        _add_column(conn, table, column, ddl)

    # Retired marker: the Round-3 component/sensor revamp (which deleted all
    # SensorTelemetry and Components rows from every connection) no longer
    # runs. The marker table is preserved so existing tooling keeps working,
    # and the note documents why it must stay empty.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ComponentRevampMarker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    marker_count = conn.execute("SELECT COUNT(*) AS cnt FROM ComponentRevampMarker").fetchone()["cnt"]
    if marker_count == 0:
        conn.execute(
            "INSERT INTO ComponentRevampMarker (note) VALUES (?)",
            ("Revamp retired (Phase 1 database audit): destructive component/sensor purge no longer runs.",),
        )


# ---------------------------------------------------------------------------
# Migration 2: authentication / company schema + seed data
# ---------------------------------------------------------------------------

_AUTH_TABLES = [
    """CREATE TABLE IF NOT EXISTS Companies (
        company_id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        hangar_location_name TEXT,
        hangar_latitude REAL,
        hangar_longitude REAL,
        climate_profile_key TEXT,
        corrosion_category TEXT,
        ambient_temp_c REAL,
        humidity_pct REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS Users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT DEFAULT 'Admin',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS AircraftDocuments (
        doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT NOT NULL,
        company_id INTEGER,
        doc_label TEXT,
        doc_type TEXT DEFAULT 'Manual',
        file_path TEXT NOT NULL,
        uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS IngestedDocuments (
        ingestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL,
        company_id INTEGER,
        manual_type TEXT NOT NULL,
        tier INTEGER NOT NULL,
        scope TEXT DEFAULT 'single',
        target_model TEXT,
        classification_method TEXT DEFAULT 'user_selected',
        status TEXT DEFAULT 'Uploaded - Awaiting Parser',
        parser_used TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (doc_id) REFERENCES AircraftDocuments(doc_id)
    )""",
]

# Additive company ownership columns on tables that predate the auth module.
_AUTH_COMPANY_COLUMNS = ['Aircraft', 'Engineers', 'Personnel', 'ToolCrib']


def _migration_002_auth_company(conn):
    for stmt in _AUTH_TABLES:
        conn.execute(stmt)
    for table in _AUTH_COMPANY_COLUMNS:
        _add_column(conn, table, 'company_id', f'INTEGER DEFAULT {DEFAULT_COMPANY_ID}')

    existing = conn.execute("SELECT COUNT(*) AS cnt FROM Companies").fetchone()["cnt"]
    if existing == 0:
        profile = REFERENCE_CLIMATE_PROFILES['WestAfrica_Tropical']
        conn.execute(
            "INSERT INTO Companies (company_id, company_name, hangar_location_name, hangar_latitude, "
            "hangar_longitude, climate_profile_key, corrosion_category, ambient_temp_c, humidity_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (DEFAULT_COMPANY_ID, 'Default Company (rename me)', profile['label'], profile['lat'], profile['lon'],
             'WestAfrica_Tropical', profile['corrosion_category'], profile['ambient_temp_c'], profile['humidity_pct']),
        )

    existing_users = conn.execute("SELECT COUNT(*) AS cnt FROM Users").fetchone()["cnt"]
    if existing_users == 0:
        conn.execute(
            "INSERT INTO Users (company_id, username, password_hash, full_name, role) VALUES (?, ?, ?, ?, ?)",
            (DEFAULT_COMPANY_ID, DEFAULT_ADMIN_USERNAME, generate_password_hash(DEFAULT_ADMIN_PASSWORD),
             'Default Administrator', 'Admin'),
        )
        print(f"⚠️  Seeded default login: username='{DEFAULT_ADMIN_USERNAME}' "
              f"password='{DEFAULT_ADMIN_PASSWORD}' - change this immediately (Company Profile page).")


# ---------------------------------------------------------------------------
# Migration 3: document ingestion pipeline schema
# ---------------------------------------------------------------------------

_INGESTION_TABLES = [
    # Master Minimum Equipment List register (target for approved MMEL extractions).
    """CREATE TABLE IF NOT EXISTS MasterMEL (
        mmel_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_model TEXT,
        ata_chapter TEXT,
        item_description TEXT,
        mmel_category TEXT,
        max_deferral_days INTEGER,
        remarks TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        company_id INTEGER DEFAULT 1
    )""",
    # Review queue + audit trail for extracted document data.
    """CREATE TABLE IF NOT EXISTS PendingExtractions (
        extraction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ingestion_id INTEGER NOT NULL,
        company_id INTEGER,
        target_table TEXT NOT NULL,
        field_data TEXT NOT NULL,
        source_page INTEGER,
        source_excerpt TEXT,
        confidence REAL,
        status TEXT DEFAULT 'Pending',
        reviewed_by TEXT,
        reviewed_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS ExtractionAuditLog (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        extraction_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        changed_fields TEXT,
        actor TEXT,
        at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Reference tables fed by approved extractions.
    """CREATE TABLE IF NOT EXISTS PartsCatalog (
        catalog_id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_number TEXT NOT NULL,
        nomenclature TEXT,
        ata_chapter TEXT,
        effectivity TEXT,
        target_model TEXT,
        company_id INTEGER,
        source_document_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS WiringReferences (
        wiring_id INTEGER PRIMARY KEY AUTOINCREMENT,
        circuit_ref TEXT,
        component_ref TEXT,
        connector TEXT,
        bus TEXT,
        wire_gauge TEXT,
        description TEXT,
        target_model TEXT,
        company_id INTEGER,
        source_document_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS ComponentSpecs (
        spec_id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_type TEXT,
        sensor_type TEXT,
        min_threshold REAL,
        max_threshold REAL,
        unit TEXT,
        calibration_interval_days INTEGER,
        manufacturer TEXT,
        target_model TEXT,
        company_id INTEGER,
        source_document_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS FaultIsolationRules (
        rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
        symptom TEXT,
        probable_cause TEXT,
        corrective_action TEXT,
        ata_chapter TEXT,
        target_model TEXT,
        company_id INTEGER,
        source_document_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS SystemSchematics (
        schematic_id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_name TEXT,
        ata_chapter TEXT,
        description TEXT,
        target_model TEXT,
        company_id INTEGER,
        source_document_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
]

# Additive columns so committed extractions can point back to their source document.
_INGESTION_LEGACY_COLUMNS = [
    ('MasterMEL', 'company_id', f'INTEGER DEFAULT {DEFAULT_COMPANY_ID}'),
    ('MasterMEL', 'source_document_id', 'INTEGER'),
    ('Directives', 'source_document_id', 'INTEGER'),
]


def _migration_003_ingestion(conn):
    for stmt in _INGESTION_TABLES:
        conn.execute(stmt)
    for table, column, ddl in _INGESTION_LEGACY_COLUMNS:
        _add_column(conn, table, column, ddl)


# ---------------------------------------------------------------------------
# Migration 4: camp extension schema
# ---------------------------------------------------------------------------

_EXTENSION_TABLES = [
    # HITL UDP telemetry bridge.
    """CREATE TABLE IF NOT EXISTS HITLPacketLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_payload TEXT,
        component_id TEXT,
        sensor_type TEXT,
        reading_value REAL,
        status TEXT DEFAULT 'Accepted',
        received_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS HITLListenerConfig (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        port INTEGER DEFAULT 5599,
        default_aircraft_id TEXT,
        last_started DATETIME,
        last_stopped DATETIME
    )""",
    # Calendar kill switch (CRS-triggered auto-cancellations).
    """CREATE TABLE IF NOT EXISTS KillSwitchProcessedCRS (
        crs_id INTEGER PRIMARY KEY,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS KillSwitchLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crs_id INTEGER,
        aircraft_reg TEXT,
        target_table TEXT,
        target_record_id TEXT,
        action_taken TEXT,
        reason TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Schedule lifecycle (reminders + 2-day auto-expiry).
    """CREATE TABLE IF NOT EXISTS ScheduleReminders (
        record_id INTEGER PRIMARY KEY,
        title TEXT,
        aircraft_id TEXT,
        start_time TEXT,
        fired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        acknowledged INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS ScheduleLifecycleLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id INTEGER,
        title TEXT,
        action TEXT,
        reason TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Digital evidence locker (tamper-evident hash chain).
    """CREATE TABLE IF NOT EXISTS DigitalEvidence (
        evidence_id TEXT PRIMARY KEY,
        aircraft_id TEXT,
        fault_id INTEGER,
        component_id TEXT,
        file_path TEXT,
        original_filename TEXT,
        sha256_hash TEXT,
        prev_hash TEXT,
        chain_position INTEGER,
        latitude REAL,
        longitude REAL,
        location_source TEXT,
        captured_at DATETIME,
        uploaded_by TEXT,
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Parts traceability (RFID/QR scanning).
    """CREATE TABLE IF NOT EXISTS PartRecords (
        part_serial TEXT PRIMARY KEY,
        part_name TEXT,
        ata_chapter TEXT,
        component_id TEXT,
        aircraft_id TEXT,
        easa_form1_ref TEXT,
        manufactured_date TEXT,
        installed_date TEXT,
        status TEXT DEFAULT 'In Service',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS PartScanLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_serial TEXT,
        scan_type TEXT,
        scanned_by TEXT,
        result TEXT,
        scanned_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Ghost data elimination audit log.
    """CREATE TABLE IF NOT EXISTS GhostDataLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        target_table TEXT,
        target_ref TEXT,
        detail TEXT,
        action TEXT,
        detected_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Layer-7 environmental stressor context + risk log.
    """CREATE TABLE IF NOT EXISTS AircraftEnvironmentContext (
        aircraft_id TEXT PRIMARY KEY,
        environment_individual TEXT DEFAULT 'L7_WestAfrica_TropicalEnv',
        ambient_temp_c REAL,
        humidity_pct REAL,
        corrosion_category TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS EnvironmentalRiskLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT,
        component_id TEXT,
        sensor_type TEXT,
        stressor TEXT,
        base_threshold REAL,
        adjusted_threshold REAL,
        corrosion_risk_score REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # CAMSIS-3 deterministic life-limit grounding.
    """CREATE TABLE IF NOT EXISTS CAMSISLimits (
        limit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_type TEXT NOT NULL,
        limit_category TEXT NOT NULL,
        limit_value REAL NOT NULL,
        unit TEXT,
        authority_ref TEXT,
        revision TEXT DEFAULT 'CAMSIS-3'
    )""",
    """CREATE TABLE IF NOT EXISTS CAMSISGroundingLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id TEXT,
        limit_id INTEGER,
        limit_category TEXT,
        used_value REAL,
        limit_value REAL,
        remaining REAL,
        margin_pct REAL,
        status TEXT,
        computed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # IoT smart tool integration.
    """CREATE TABLE IF NOT EXISTS TorqueSpecs (
        spec_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ata_chapter TEXT,
        fastener_description TEXT,
        min_torque REAL,
        max_torque REAL,
        unit TEXT DEFAULT 'Nm'
    )""",
    """CREATE TABLE IF NOT EXISTS IoTToolReadings (
        reading_id TEXT PRIMARY KEY,
        tool_id TEXT,
        task_id TEXT,
        component_id TEXT,
        torque_value REAL,
        unit TEXT DEFAULT 'Nm',
        spec_id INTEGER,
        in_spec INTEGER,
        device_name TEXT,
        ingestion_source TEXT,
        received_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Maintenance document register (paper-audit trail).
    """CREATE TABLE IF NOT EXISTS MaintenanceDocuments (
        document_id TEXT PRIMARY KEY,
        source_type TEXT,
        source_id TEXT,
        aircraft_reg TEXT,
        file_path TEXT,
        document_hash TEXT,
        generated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
]

# Additive columns owned by the extensions (upgrade path for old databases).
_EXTENSION_LEGACY_COLUMNS = [
    # FullCalendar interactive schedule tagging.
    ('Schedule', 'source', "TEXT DEFAULT 'legacy'"),
    ('Schedule', 'related_reference', 'TEXT'),
    # IMDF (Integrated Maintenance Documentation Framework) enrichment.
    ('PartRecords', 'removal_reason', 'TEXT'),
    ('PartRecords', 'condition_assessment', 'TEXT'),
    ('PartRecords', 'fault_code', 'TEXT'),
    ('PartRecords', 'position_on_aircraft', 'TEXT'),
    ('PartRecords', 'flight_hours_at_removal', 'REAL'),
    ('PartRecords', 'flight_cycles_at_removal', 'INTEGER'),
    ('PartRecords', 'removed_date', 'TEXT'),
    ('PartRecords', 'replaced_by_serial', 'TEXT'),
    ('CRS_Records', 'work_order_number', 'TEXT'),
    ('CRS_Records', 'ata_chapter', 'TEXT'),
    ('CRS_Records', 'component_replaced', 'BOOLEAN DEFAULT 0'),
    ('CRS_Records', 'removed_part_serial', 'TEXT'),
    ('CRS_Records', 'installed_part_serial', 'TEXT'),
    ('CRS_Records', 'evidence_chain_ref', 'TEXT'),
]

# Seed data for the extension reference tables (moved here from the lazy
# ensure_* functions so a fresh database is complete at startup).
CAMSIS_DEFAULT_LIMITS = [
    ('Engine', 'Hours', 3000.0, 'FH', 'CAMSIS-3 §5.1 Engine TBO'),
    ('Landing_Gear', 'Cycles', 20000.0, 'Cycles', 'CAMSIS-3 §5.2 LG Overhaul'),
    ('HIL-Rig', 'Hours', 999999.0, 'FH', 'CAMSIS-3 (test rig - unlimited)'),
]

TORQUE_DEFAULT_SPECS = [
    ('ATA_28', 'Fuel line B-nut fitting', 20.0, 25.0, 'Nm'),
    ('ATA_32', 'Landing gear axle nut', 180.0, 220.0, 'Nm'),
    ('ATA_72', 'Engine mount bolt', 95.0, 110.0, 'Nm'),
]


def _migration_004_extensions(conn):
    for stmt in _EXTENSION_TABLES:
        conn.execute(stmt)
    for table, column, ddl in _EXTENSION_LEGACY_COLUMNS:
        _add_column(conn, table, column, ddl)

    hitl_config = conn.execute("SELECT id FROM HITLListenerConfig WHERE id = 1").fetchone()
    if not hitl_config:
        conn.execute("INSERT INTO HITLListenerConfig (id, port) VALUES (1, 5599)")

    limits = conn.execute("SELECT COUNT(*) AS cnt FROM CAMSISLimits").fetchone()["cnt"]
    if limits == 0:
        conn.executemany(
            "INSERT INTO CAMSISLimits (component_type, limit_category, limit_value, unit, authority_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            CAMSIS_DEFAULT_LIMITS,
        )

    specs = conn.execute("SELECT COUNT(*) AS cnt FROM TorqueSpecs").fetchone()["cnt"]
    if specs == 0:
        conn.executemany(
            "INSERT INTO TorqueSpecs (ata_chapter, fastener_description, min_torque, max_torque, unit) "
            "VALUES (?, ?, ?, ?, ?)",
            TORQUE_DEFAULT_SPECS,
        )


# ---------------------------------------------------------------------------
# Ordered migration register
# ---------------------------------------------------------------------------

MIGRATIONS = [
    {'version': 1, 'name': '001_core_operational_schema', 'apply': _migration_001_core},
    {'version': 2, 'name': '002_auth_company_schema', 'apply': _migration_002_auth_company},
    {'version': 3, 'name': '003_ingestion_pipeline_schema', 'apply': _migration_003_ingestion},
    {'version': 4, 'name': '004_camp_extension_schema', 'apply': _migration_004_extensions},
]
