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
import json
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
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {Config.DB_BUSY_TIMEOUT_MS}")
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
        applied_now = []
        for migration in MIGRATIONS:
            if migration["version"] in applied:
                continue
            migration["apply"](conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration["version"], migration["name"]),
            )
            applied_now.append(migration)
        conn.execute("COMMIT")
        # Optional post-commit step - runs OUTSIDE the migration transaction
        # (e.g. PRAGMA journal_mode = WAL, which SQLite refuses inside one).
        for migration in applied_now:
            post_commit = migration.get("post_commit")
            if post_commit is not None:
                post_commit(conn)
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
# Migration 5: Phase 2A - repair referential-integrity orphans
# ---------------------------------------------------------------------------
# Phase 2A of the database audit (see DATABASE_AUDIT_GUIDELINES.md). Before
# foreign keys are enforced on every connection, the known orphan rows are
# repaired:
#
# - Faults.component_id values pointing at deleted components are archived to
#   FKRepairAudit and set to NULL (history is preserved, not deleted);
# - Faults.telemetry_id values pointing at deleted telemetry rows are archived
#   and set to NULL;
# - Schedule rows referencing deleted aircraft (logical orphans - Schedule has
#   no FK yet) are archived in full and removed, so later FK additions start
#   from a clean slate.
#
# The audit table keeps the original values so any future forensic review can
# see exactly what was broken and when it was repaired.

_FK_REPAIR_AUDIT_TABLE = """
    CREATE TABLE IF NOT EXISTS FKRepairAudit (
        repair_id INTEGER PRIMARY KEY AUTOINCREMENT,
        repaired_table TEXT NOT NULL,
        repaired_row_id TEXT NOT NULL,
        repaired_column TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        reason TEXT,
        repaired_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""


def _migration_005_fk_repair(conn):
    conn.execute(_FK_REPAIR_AUDIT_TABLE)

    orphan_components = conn.execute(
        """
        SELECT f.fault_id, f.component_id
        FROM Faults f
        LEFT JOIN Components c ON f.component_id = c.component_id
        WHERE f.component_id IS NOT NULL AND c.component_id IS NULL
        """
    ).fetchall()
    for row in orphan_components:
        conn.execute(
            "INSERT INTO FKRepairAudit (repaired_table, repaired_row_id, repaired_column, old_value, new_value, reason) "
            "VALUES ('Faults', ?, 'component_id', ?, NULL, ?)",
            (row["fault_id"], row["component_id"],
             "Orphaned component reference - component no longer exists in Components"),
        )
        conn.execute("UPDATE Faults SET component_id = NULL WHERE fault_id = ?", (row["fault_id"],))

    orphan_telemetry = conn.execute(
        """
        SELECT f.fault_id, f.telemetry_id
        FROM Faults f
        LEFT JOIN SensorTelemetry t ON f.telemetry_id = t.telemetry_id
        WHERE f.telemetry_id IS NOT NULL AND t.telemetry_id IS NULL
        """
    ).fetchall()
    for row in orphan_telemetry:
        conn.execute(
            "INSERT INTO FKRepairAudit (repaired_table, repaired_row_id, repaired_column, old_value, new_value, reason) "
            "VALUES ('Faults', ?, 'telemetry_id', ?, NULL, ?)",
            (row["fault_id"], row["telemetry_id"],
             "Orphaned telemetry reference - telemetry row no longer exists in SensorTelemetry"),
        )
        conn.execute("UPDATE Faults SET telemetry_id = NULL WHERE fault_id = ?", (row["fault_id"],))

    orphan_schedules = conn.execute(
        """
        SELECT s.event_id, s.aircraft_id, s.event_type, s.title, s.start_time, s.end_time, s.color, s.status
        FROM Schedule s
        LEFT JOIN Aircraft a ON s.aircraft_id = a.aircraft_id
        WHERE s.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL
        """
    ).fetchall()
    for row in orphan_schedules:
        conn.execute(
            "INSERT INTO FKRepairAudit (repaired_table, repaired_row_id, repaired_column, old_value, new_value, reason) "
            "VALUES ('Schedule', ?, 'aircraft_id', ?, 'DELETED', ?)",
            (row["event_id"], json.dumps(dict(row), sort_keys=True),
             "Orphaned schedule row - aircraft no longer exists in Aircraft; row archived then removed"),
        )
        conn.execute("DELETE FROM Schedule WHERE event_id = ?", (row["event_id"],))


# ---------------------------------------------------------------------------
# Migration 6: Phase 2B - add missing foreign keys (DB-06)
# ---------------------------------------------------------------------------
# Phase 2B of the database audit (see DATABASE_AUDIT_GUIDELINES.md). SQLite
# cannot add a FOREIGN KEY with ALTER TABLE, so each table is rebuilt:
# create a new table with the same columns plus FK clauses, copy the rows,
# drop the old table, rename the new one, and preserve the AUTOINCREMENT
# sequence.
#
# Delete policy (product decision, confirmed 2026-08-18):
# - Schedule.aircraft_id and MEL_Deferrals.aircraft_id use ON DELETE RESTRICT:
#   aircraft deletion is blocked while active schedule/MEL records exist.
# - Every audit/history reference (evidence, parts, environmental risk,
#   CAMSIS grounding, IoT readings, mmel_id, replacement serial) uses
#   ON DELETE SET NULL so historical records survive parent retirement.

_REBUILD_PREFLIGHT = [
    ("Schedule.aircraft_id",
     "SELECT COUNT(*) FROM Schedule s LEFT JOIN Aircraft a ON s.aircraft_id = a.aircraft_id "
     "WHERE s.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL"),
    ("MEL_Deferrals.aircraft_id",
     "SELECT COUNT(*) FROM MEL_Deferrals m LEFT JOIN Aircraft a ON m.aircraft_id = a.aircraft_id "
     "WHERE m.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL"),
    ("MEL_Deferrals.mmel_id",
     "SELECT COUNT(*) FROM MEL_Deferrals m LEFT JOIN MasterMEL l ON m.mmel_id = l.mmel_id "
     "WHERE m.mmel_id IS NOT NULL AND l.mmel_id IS NULL"),
    ("DigitalEvidence.aircraft_id",
     "SELECT COUNT(*) FROM DigitalEvidence d LEFT JOIN Aircraft a ON d.aircraft_id = a.aircraft_id "
     "WHERE d.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL"),
    ("DigitalEvidence.fault_id",
     "SELECT COUNT(*) FROM DigitalEvidence d LEFT JOIN Faults f ON d.fault_id = f.fault_id "
     "WHERE d.fault_id IS NOT NULL AND f.fault_id IS NULL"),
    ("DigitalEvidence.component_id",
     "SELECT COUNT(*) FROM DigitalEvidence d LEFT JOIN Components c ON d.component_id = c.component_id "
     "WHERE d.component_id IS NOT NULL AND c.component_id IS NULL"),
    ("PartRecords.component_id",
     "SELECT COUNT(*) FROM PartRecords p LEFT JOIN Components c ON p.component_id = c.component_id "
     "WHERE p.component_id IS NOT NULL AND c.component_id IS NULL"),
    ("PartRecords.aircraft_id",
     "SELECT COUNT(*) FROM PartRecords p LEFT JOIN Aircraft a ON p.aircraft_id = a.aircraft_id "
     "WHERE p.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL"),
    ("PartRecords.replaced_by_serial",
     "SELECT COUNT(*) FROM PartRecords p LEFT JOIN PartRecords p2 ON p.replaced_by_serial = p2.part_serial "
     "WHERE p.replaced_by_serial IS NOT NULL AND p2.part_serial IS NULL"),
    ("EnvironmentalRiskLog.aircraft_id",
     "SELECT COUNT(*) FROM EnvironmentalRiskLog e LEFT JOIN Aircraft a ON e.aircraft_id = a.aircraft_id "
     "WHERE e.aircraft_id IS NOT NULL AND a.aircraft_id IS NULL"),
    ("EnvironmentalRiskLog.component_id",
     "SELECT COUNT(*) FROM EnvironmentalRiskLog e LEFT JOIN Components c ON e.component_id = c.component_id "
     "WHERE e.component_id IS NOT NULL AND c.component_id IS NULL"),
    ("CAMSISGroundingLog.component_id",
     "SELECT COUNT(*) FROM CAMSISGroundingLog g LEFT JOIN Components c ON g.component_id = c.component_id "
     "WHERE g.component_id IS NOT NULL AND c.component_id IS NULL"),
    ("CAMSISGroundingLog.limit_id",
     "SELECT COUNT(*) FROM CAMSISGroundingLog g LEFT JOIN CAMSISLimits l ON g.limit_id = l.limit_id "
     "WHERE g.limit_id IS NOT NULL AND l.limit_id IS NULL"),
    ("IoTToolReadings.tool_id",
     "SELECT COUNT(*) FROM IoTToolReadings r LEFT JOIN ToolCrib t ON r.tool_id = t.tool_id "
     "WHERE r.tool_id IS NOT NULL AND t.tool_id IS NULL"),
    ("IoTToolReadings.task_id",
     "SELECT COUNT(*) FROM IoTToolReadings r LEFT JOIN MaintenanceTasks t ON r.task_id = t.task_id "
     "WHERE r.task_id IS NOT NULL AND t.task_id IS NULL"),
    ("IoTToolReadings.component_id",
     "SELECT COUNT(*) FROM IoTToolReadings r LEFT JOIN Components c ON r.component_id = c.component_id "
     "WHERE r.component_id IS NOT NULL AND c.component_id IS NULL"),
    ("IoTToolReadings.spec_id",
     "SELECT COUNT(*) FROM IoTToolReadings r LEFT JOIN TorqueSpecs s ON r.spec_id = s.spec_id "
     "WHERE r.spec_id IS NOT NULL AND s.spec_id IS NULL"),
]

# Replacement DDL - column names/order must match the live tables exactly.
# Only the FOREIGN KEY / ON DELETE clauses are new.
_REBUILD_DDL = [
    """CREATE TABLE Schedule (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT REFERENCES Aircraft(aircraft_id) ON DELETE RESTRICT,
        event_type TEXT,
        title TEXT,
        start_time DATETIME,
        end_time DATETIME,
        color TEXT,
        status TEXT DEFAULT 'Scheduled',
        source TEXT DEFAULT 'legacy',
        related_reference TEXT
    )""",
    """CREATE TABLE MEL_Deferrals (
        deferral_id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT REFERENCES Aircraft(aircraft_id) ON DELETE RESTRICT,
        item_description TEXT,
        mel_category TEXT,
        date_deferred DATETIME,
        status TEXT DEFAULT 'Active',
        mmel_id INTEGER REFERENCES MasterMEL(mmel_id) ON DELETE SET NULL
    )""",
    """CREATE TABLE DigitalEvidence (
        evidence_id TEXT PRIMARY KEY,
        aircraft_id TEXT REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL,
        fault_id INTEGER REFERENCES Faults(fault_id) ON DELETE SET NULL,
        component_id TEXT REFERENCES Components(component_id) ON DELETE SET NULL,
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
    """CREATE TABLE PartRecords (
        part_serial TEXT PRIMARY KEY,
        part_name TEXT,
        ata_chapter TEXT,
        component_id TEXT REFERENCES Components(component_id) ON DELETE SET NULL,
        aircraft_id TEXT REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL,
        easa_form1_ref TEXT,
        manufactured_date TEXT,
        installed_date TEXT,
        status TEXT DEFAULT 'In Service',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        removal_reason TEXT,
        condition_assessment TEXT,
        fault_code TEXT,
        position_on_aircraft TEXT,
        flight_hours_at_removal REAL,
        flight_cycles_at_removal INTEGER,
        removed_date TEXT,
        replaced_by_serial TEXT REFERENCES PartRecords(part_serial) ON DELETE SET NULL
    )""",
    """CREATE TABLE EnvironmentalRiskLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        aircraft_id TEXT REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL,
        component_id TEXT REFERENCES Components(component_id) ON DELETE SET NULL,
        sensor_type TEXT,
        stressor TEXT,
        base_threshold REAL,
        adjusted_threshold REAL,
        corrosion_risk_score REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE CAMSISGroundingLog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id TEXT REFERENCES Components(component_id) ON DELETE SET NULL,
        limit_id INTEGER REFERENCES CAMSISLimits(limit_id) ON DELETE SET NULL,
        limit_category TEXT,
        used_value REAL,
        limit_value REAL,
        remaining REAL,
        margin_pct REAL,
        status TEXT,
        computed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IoTToolReadings (
        reading_id TEXT PRIMARY KEY,
        tool_id TEXT REFERENCES ToolCrib(tool_id) ON DELETE SET NULL,
        task_id TEXT REFERENCES MaintenanceTasks(task_id) ON DELETE SET NULL,
        component_id TEXT REFERENCES Components(component_id) ON DELETE SET NULL,
        torque_value REAL,
        unit TEXT DEFAULT 'Nm',
        spec_id INTEGER REFERENCES TorqueSpecs(spec_id) ON DELETE SET NULL,
        in_spec INTEGER,
        device_name TEXT,
        ingestion_source TEXT,
        received_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
]


def _rebuild_table_with_fk(conn, table, ddl):
    """Copy table rows into a new FK-constrained table, then swap them."""
    new_name = f"{table}__new"
    seq = None
    try:
        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
        ).fetchone()
    except sqlite3.OperationalError:
        pass

    conn.execute(ddl.replace(f"CREATE TABLE {table} (", f"CREATE TABLE {new_name} (", 1))
    conn.execute(f"INSERT INTO {new_name} SELECT * FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {new_name} RENAME TO {table}")
    if seq is not None:
        conn.execute(
            "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)",
            (table, seq["seq"]),
        )


def _migration_006_missing_foreign_keys(conn):
    for label, sql in _REBUILD_PREFLIGHT:
        count = conn.execute(sql).fetchone()[0]
        if count:
            raise RuntimeError(
                f"Migration 006 aborted: {count} orphan row(s) in {label} - "
                "repair the data before enforcing this foreign key"
            )

    for table in ('Schedule', 'MEL_Deferrals', 'DigitalEvidence', 'PartRecords',
                  'EnvironmentalRiskLog', 'CAMSISGroundingLog', 'IoTToolReadings'):
        ddl = next(d for d in _REBUILD_DDL if d.startswith(f"CREATE TABLE {table} ("))
        _rebuild_table_with_fk(conn, table, ddl)


# ---------------------------------------------------------------------------
# Migration 7: query indexes (Phase 2C, DB-07) + WAL journal mode (DB-10)
# ---------------------------------------------------------------------------
# Every index below exists because a real code path filters/sorts on those
# columns (verified with EXPLAIN QUERY PLAN in Phase 2C, 2026-08-19):
#
#   Schedule(start_time)                     fullcalendar + dashboard ORDER BY
#   Schedule(aircraft_id, status)            kill switch open-event lookup
#   SensorTelemetry(component_id, sensor_type, recorded_at)
#                                            telemetry pages, ontology reasoner
#                                            MAX(recorded_at), ghost-data dedup
#   Faults(component_id, resolved, fault_type)
#                                            reasoner active-fault lookup
#   DigitalEvidence(aircraft_id, chain_position)     evidence chain reads
#   DigitalEvidence(fault_id, chain_position)        IMDF/evidence by fault
#   PartRecords(component_id, status)                IMDF in-service/removed
#   MEL_Deferrals(aircraft_id, status)               kill switch MEL close
#   PilotReports(status)                             ghost data + flight log
#   MaintenanceDocuments(generated_at)               document list ordering
#   MaintenanceDocuments(source_type, source_id)     document dedup lookup
#   Components(aircraft_id)                          CAMSIS/env component scans


_QUERY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_schedule_start_time ON Schedule(start_time)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_aircraft_status ON Schedule(aircraft_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_component_sensor_recorded "
    "ON SensorTelemetry(component_id, sensor_type, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_faults_component_resolved "
    "ON Faults(component_id, resolved, fault_type)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_aircraft_chain "
    "ON DigitalEvidence(aircraft_id, chain_position)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_fault_chain "
    "ON DigitalEvidence(fault_id, chain_position)",
    "CREATE INDEX IF NOT EXISTS idx_parts_component_status "
    "ON PartRecords(component_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mel_aircraft_status "
    "ON MEL_Deferrals(aircraft_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_pilotreports_status ON PilotReports(status)",
    "CREATE INDEX IF NOT EXISTS idx_maintdocs_generated ON MaintenanceDocuments(generated_at)",
    "CREATE INDEX IF NOT EXISTS idx_maintdocs_source "
    "ON MaintenanceDocuments(source_type, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_components_aircraft ON Components(aircraft_id)",
]


def _migration_007_query_indexes(conn):
    for ddl in _QUERY_INDEXES:
        conn.execute(ddl)


def _migration_007_post_commit(conn):
    """Persist WAL mode in the database file (DB-10).

    Runs after COMMIT because SQLite refuses a journal-mode change from inside
    a transaction. WAL lets background watchers (kill switch, schedule
    lifecycle) and request handlers read while a writer is active, instead of
    serializing on the file lock. synchronous stays at the default FULL so
    durability semantics are unchanged.
    """
    conn.execute("PRAGMA journal_mode = WAL")


# ---------------------------------------------------------------------------
# Migration 8: uniqueness & idempotency guards (Phase 3A, DB-08)
# ---------------------------------------------------------------------------
# Each unique index backs a read-then-write pattern that could race when two
# requests/threads hit it at the same time (double-click, background watcher
# + request, two reasoner runs). The application code catches the resulting
# IntegrityError and treats it as "already recorded":
#
#   Faults(component_id, fault_type) WHERE resolved = 0
#       the ontology reasoner checks for an existing OPEN fault before
#       inserting; without the constraint two concurrent runs both pass the
#       check and create duplicate rows. Partial: only open faults are
#       unique - a resolved fault may legitimately be detected again later,
#       and SQLite treats NULL component_id as distinct (legacy repaired
#       rows and airframe-level PIREP faults never collide).
#   IngestedDocuments(doc_id)
#       exactly one ingestion row per AircraftDocuments row (1:1) - the
#       upload flow inserts them together; guards against double-inserts on
#       retried requests.
#   MaintenanceDocuments(source_type, source_id)
#       generate_document() returns the existing PDF when one exists; the
#       unique index makes that read-then-write race-safe (replaces the
#       non-unique idx_maintdocs_source from 007, which is now redundant).
#   DigitalEvidence(aircraft_id, chain_position)
#       chain positions are computed as COUNT+1; concurrent uploads for the
#       same aircraft could otherwise claim the same position and silently
#       break the tamper-evident hash chain (replaces the non-unique
#       idx_evidence_aircraft_chain from 007).
#
# Deliberately NOT constrained: DiagnosticJobs (job_id is a uuid and two
# "Run Diagnostics" clicks are separate legitimate runs) and the append-only
# audit logs (XAILogs, PartScanLog, ScheduleLifecycleLog, ...) where
# duplicate rows are normal.
#
# Audited 2026-08-19: the live database contains zero duplicate rows in any
# of these shapes - the indexes are preventive, not repairs. If a future
# database does contain duplicates, this migration fails loudly and rolls
# back (same policy as migration 006).

_UNIQUENESS_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_faults_open_component_type "
    "ON Faults(component_id, fault_type) WHERE resolved = 0",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_documents_doc_id "
    "ON IngestedDocuments(doc_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_maintdocs_source_unique "
    "ON MaintenanceDocuments(source_type, source_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_chain_position "
    "ON DigitalEvidence(aircraft_id, chain_position)",
]

_REDUNDANT_INDEX_DROPS = [
    "DROP INDEX IF EXISTS idx_maintdocs_source",
    "DROP INDEX IF EXISTS idx_evidence_aircraft_chain",
]


def _migration_008_uniqueness(conn):
    for ddl in _UNIQUENESS_INDEXES:
        conn.execute(ddl)
    for ddl in _REDUNDANT_INDEX_DROPS:
        conn.execute(ddl)


# ---------------------------------------------------------------------------
# Migration 9: stable aircraft references (Phase 3B)
# ---------------------------------------------------------------------------
# MaintenanceHistory.aircraft_reg and CRS_Records.aircraft_reg are free-text
# registrations ('5N-TAJ', '5N_TAJ', ...) with no referential link to the
# Aircraft table. This migration adds a proper aircraft_id foreign key
# (audit-reference policy: ON DELETE SET NULL, matching Phase 2B) and
# backfills it by matching the free-text value against Aircraft.registration
# (dashes/underscores are treated as equivalent - the live data uses both).
#
# Rows whose registration matches no aircraft keep aircraft_reg and get a
# NULL aircraft_id; they are preserved, not deleted. The free-text column is
# kept as the human-readable display value.
#
# No indexes on the new columns yet: no query currently filters on them (the
# CBR engine intentionally full-scans history), and tenant-leading indexes
# belong with Phase 5 (DB-01) enforcement.

def _migration_009_stable_aircraft_refs(conn):
    _add_column(
        conn, 'MaintenanceHistory', 'aircraft_id',
        'TEXT REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL',
    )
    _add_column(
        conn, 'CRS_Records', 'aircraft_id',
        'TEXT REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL',
    )

    conn.execute('''
        UPDATE MaintenanceHistory
        SET aircraft_id = (
            SELECT a.aircraft_id FROM Aircraft a
            WHERE REPLACE(a.registration, '-', '_')
                = REPLACE(MaintenanceHistory.aircraft_reg, '-', '_')
            LIMIT 1
        )
    ''')
    conn.execute('''
        UPDATE CRS_Records
        SET aircraft_id = (
            SELECT a.aircraft_id FROM Aircraft a
            WHERE REPLACE(a.registration, '-', '_')
                = REPLACE(CRS_Records.aircraft_reg, '-', '_')
            LIMIT 1
        )
    ''')


# ---------------------------------------------------------------------------
# Migration 10: company ownership on every operational table (Phase 5, DB-01)
# ---------------------------------------------------------------------------
# DB-01: most operational tables had no company_id column, so an
# authenticated user could read/mutate another company's records by ID. This
# migration gives every operational table a company_id column (INTEGER NOT
# NULL DEFAULT 1 - the seeded single company - so legacy rows are never
# dropped), backfills ownership from the nearest owning row, and adds
# company-leading indexes.
#
# Ownership derivation:
#   aircraft_id  -> Aircraft.company_id          Components, Schedule,
#                                                  MEL_Deferrals, PilotReports,
#                                                  DigitalEvidence, PartRecords,
#                                                  EnvironmentalRiskLog,
#                                                  DiagnosticJobs,
#                                                  ScheduleReminders,
#                                                  AircraftEnvironmentContext
#   aircraft_id / registration (dash==underscore) MaintenanceHistory, CRS_Records
#   registration (dash==underscore)              MaintenanceDocuments
#   component_id -> Components.company_id        SensorTelemetry, XAILogs,
#                                                  CAMSISGroundingLog,
#                                                  IoTToolReadings, HITLPacketLog
#   crs_id -> CRS_Records.company_id             KillSwitchProcessedCRS,
#                                                  KillSwitchLog
#   part_serial -> PartRecords.company_id        PartScanLog
#   record_id -> Schedule.company_id             ScheduleLifecycleLog
#   extraction_id -> PendingExtractions.company_id ExtractionAuditLog
#   component_id / fault_id                      MaintenanceRecords,
#                                                  LegalSignOffs
#   (no link - shared reference/config data)     MaintenanceTasks, Directives,
#                                                  SWRLRules, CAMSISLimits,
#                                                  TorqueSpecs,
#                                                  HITLListenerConfig,
#                                                  GhostDataLog
#
# Rows whose owner cannot be resolved fall back to the seeded company (1);
# they are preserved, never deleted. App code (app/tenancy.py + every route)
# now filters on company_id; see DATABASE_AUDIT_GUIDELINES.md Phase 5.

_TENANCY_REFERENCE_TABLES = (
    'MaintenanceTasks', 'Directives', 'SWRLRules', 'CAMSISLimits',
    'TorqueSpecs', 'HITLListenerConfig', 'GhostDataLog',
)

_TENANCY_AIRCRAFT_LINKED_TABLES = (
    'Components', 'Schedule', 'MEL_Deferrals', 'PilotReports',
    'DigitalEvidence', 'PartRecords', 'EnvironmentalRiskLog',
    'DiagnosticJobs', 'ScheduleReminders', 'AircraftEnvironmentContext',
)

_TENANCY_COMPONENT_LINKED_TABLES = (
    'Faults', 'SensorTelemetry', 'XAILogs', 'CAMSISGroundingLog',
    'IoTToolReadings', 'HITLPacketLog',
)

_TENANCY_CRS_LINKED_TABLES = ('KillSwitchProcessedCRS', 'KillSwitchLog')

_TENANCY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_components_company_aircraft "
    "ON Components(company_id, aircraft_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_company_start "
    "ON Schedule(company_id, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_faults_company_resolved "
    "ON Faults(company_id, resolved)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_company_component "
    "ON SensorTelemetry(company_id, component_id)",
    "CREATE INDEX IF NOT EXISTS idx_history_company_aircraft "
    "ON MaintenanceHistory(company_id, aircraft_id)",
    "CREATE INDEX IF NOT EXISTS idx_crs_company ON CRS_Records(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_mel_company_status "
    "ON MEL_Deferrals(company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_pilotreports_company_status "
    "ON PilotReports(company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_partrecords_company_aircraft "
    "ON PartRecords(company_id, aircraft_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_company_aircraft "
    "ON DigitalEvidence(company_id, aircraft_id)",
    "CREATE INDEX IF NOT EXISTS idx_maintdocs_company "
    "ON MaintenanceDocuments(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_ingesteddocs_company_status "
    "ON IngestedDocuments(company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_pendingextractions_company_status "
    "ON PendingExtractions(company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_engineers_company ON Engineers(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_aircraft_company ON Aircraft(company_id)",
]


def _migration_010_company_tenancy(conn):
    for table in _TENANCY_REFERENCE_TABLES + _TENANCY_AIRCRAFT_LINKED_TABLES + \
            _TENANCY_COMPONENT_LINKED_TABLES + _TENANCY_CRS_LINKED_TABLES + \
            ('MaintenanceHistory', 'CRS_Records', 'MaintenanceDocuments',
             'PartScanLog', 'ScheduleLifecycleLog', 'ExtractionAuditLog',
             'MaintenanceRecords', 'LegalSignOffs'):
        _add_column(
            conn, table, 'company_id',
            f'INTEGER NOT NULL DEFAULT {DEFAULT_COMPANY_ID}',
        )

    # Aircraft-linked tables: inherit from Aircraft.company_id.
    for table in _TENANCY_AIRCRAFT_LINKED_TABLES:
        conn.execute(f'''
            UPDATE {table} SET company_id = COALESCE(
                (SELECT a.company_id FROM Aircraft a
                 WHERE a.aircraft_id = {table}.aircraft_id),
                {DEFAULT_COMPANY_ID})
        ''')

    # MaintenanceHistory / CRS_Records: prefer the stable aircraft_id
    # (migration 009), then the free-text registration, then the default.
    for table in ('MaintenanceHistory', 'CRS_Records'):
        conn.execute(f'''
            UPDATE {table} SET company_id = COALESCE(
                (SELECT a.company_id FROM Aircraft a
                 WHERE a.aircraft_id = {table}.aircraft_id),
                (SELECT a.company_id FROM Aircraft a
                 WHERE REPLACE(a.registration, '-', '_')
                       = REPLACE({table}.aircraft_reg, '-', '_')
                 LIMIT 1),
                {DEFAULT_COMPANY_ID})
        ''')

    # MaintenanceDocuments only carries a free-text registration.
    conn.execute(f'''
        UPDATE MaintenanceDocuments SET company_id = COALESCE(
            (SELECT a.company_id FROM Aircraft a
             WHERE REPLACE(a.registration, '-', '_')
                   = REPLACE(MaintenanceDocuments.aircraft_reg, '-', '_')
             LIMIT 1),
            {DEFAULT_COMPANY_ID})
    ''')

    # Component-linked tables: inherit from Components.company_id (which was
    # backfilled just above).
    for table in _TENANCY_COMPONENT_LINKED_TABLES:
        conn.execute(f'''
            UPDATE {table} SET company_id = COALESCE(
                (SELECT c.company_id FROM Components c
                 WHERE c.component_id = {table}.component_id),
                {DEFAULT_COMPANY_ID})
        ''')

    # CRS-linked tables: inherit from CRS_Records.company_id.
    for table in _TENANCY_CRS_LINKED_TABLES:
        conn.execute(f'''
            UPDATE {table} SET company_id = COALESCE(
                (SELECT cr.company_id FROM CRS_Records cr
                 WHERE cr.id = {table}.crs_id),
                {DEFAULT_COMPANY_ID})
        ''')

    # Part-linked, schedule-linked and extraction-linked audit tables.
    conn.execute(f'''
        UPDATE PartScanLog SET company_id = COALESCE(
            (SELECT p.company_id FROM PartRecords p
             WHERE p.part_serial = PartScanLog.part_serial),
            {DEFAULT_COMPANY_ID})
    ''')
    conn.execute(f'''
        UPDATE ScheduleLifecycleLog SET company_id = COALESCE(
            (SELECT s.company_id FROM Schedule s
             WHERE s.event_id = ScheduleLifecycleLog.record_id),
            {DEFAULT_COMPANY_ID})
    ''')
    conn.execute(f'''
        UPDATE ExtractionAuditLog SET company_id = COALESCE(
            (SELECT p.company_id FROM PendingExtractions p
             WHERE p.extraction_id = ExtractionAuditLog.extraction_id),
            {DEFAULT_COMPANY_ID})
    ''')
    conn.execute(f'''
        UPDATE MaintenanceRecords SET company_id = COALESCE(
            (SELECT c.company_id FROM Components c
             WHERE c.component_id = MaintenanceRecords.component_id),
            {DEFAULT_COMPANY_ID})
    ''')
    conn.execute(f'''
        UPDATE LegalSignOffs SET company_id = COALESCE(
            (SELECT f.company_id FROM Faults f
             WHERE f.fault_id = LegalSignOffs.fault_id),
            {DEFAULT_COMPANY_ID})
    ''')

    for ddl in _TENANCY_INDEXES:
        conn.execute(ddl)


# ---------------------------------------------------------------------------
# Ordered migration register
# ---------------------------------------------------------------------------

MIGRATIONS = [
    {'version': 1, 'name': '001_core_operational_schema', 'apply': _migration_001_core},
    {'version': 2, 'name': '002_auth_company_schema', 'apply': _migration_002_auth_company},
    {'version': 3, 'name': '003_ingestion_pipeline_schema', 'apply': _migration_003_ingestion},
    {'version': 4, 'name': '004_camp_extension_schema', 'apply': _migration_004_extensions},
    {'version': 5, 'name': '005_fk_orphan_repair', 'apply': _migration_005_fk_repair},
    {'version': 6, 'name': '006_missing_foreign_keys', 'apply': _migration_006_missing_foreign_keys},
    {
        'version': 7,
        'name': '007_query_indexes',
        'apply': _migration_007_query_indexes,
        'post_commit': _migration_007_post_commit,
    },
    {'version': 8, 'name': '008_uniqueness_constraints', 'apply': _migration_008_uniqueness},
    {'version': 9, 'name': '009_stable_aircraft_refs', 'apply': _migration_009_stable_aircraft_refs},
    {'version': 10, 'name': '010_company_tenancy', 'apply': _migration_010_company_tenancy},
]
