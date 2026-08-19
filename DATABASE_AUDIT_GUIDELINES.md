# C.O.R.E. CAMP Database Audit Guidelines

## Purpose

This document records the database problems found in the current working tree and the live `camp_system.db` file. It explains:

- how the problems were identified;
- what each problem affects;
- where the relevant code lives;
- the recommended remediation order;
- how to test each repair.

This is an audit and remediation guide. No data repair was performed during the audit.

## Audit Scope

The audit covered:

- `app/database.py` and `app/config.py`;
- the authentication and company model in `app/auth.py`;
- all core Flask routes under `app/routes/`;
- extension modules under `app/camp_extensions/`;
- document ingestion under `app/ingestion/`;
- the legacy root-level `app.py`;
- the live SQLite schema, foreign-key state, indexes, row counts, and referential-integrity checks.

The current package application is started through `run.py`. The root `app.py` is a second, legacy application implementation and must not be treated as an equivalent entry point.

## How The Findings Were Discovered

The following read-only checks were used from the project root:

```powershell
sqlite3 camp_system.db ".tables"
sqlite3 camp_system.db "select name, sql from sqlite_master where type='table' and name not like 'sqlite_%' order by name;"
sqlite3 camp_system.db "PRAGMA foreign_keys; PRAGMA journal_mode; PRAGMA foreign_key_check;"
sqlite3 camp_system.db "select name, tbl_name, sql from sqlite_master where type='index' and name not like 'sqlite_%';"
```

The application code was then traced for:

- every `CREATE TABLE` and `ALTER TABLE`;
- every `SELECT`, `INSERT`, `UPDATE`, and `DELETE` involving operational data;
- every use of `company_id`;
- background threads and database writes;
- transaction boundaries and swallowed exceptions.

## Evidence From The Database (baseline at audit time)

The live database **at audit time** (before any Phase 2 repair) reported:

- `PRAGMA foreign_keys` was `0`.
- SQLite was using `delete` journal mode, not WAL.
- `PRAGMA foreign_key_check` reported 19 violations.
- 18 `Faults` rows referenced deleted component IDs.
- 1 `Faults` row referenced a missing `SensorTelemetry` row.
- 1 `Schedule` row referenced deleted aircraft `Aircraft_5N_NAF`.
- There were no user-defined indexes.
- The database contained 4 aircraft, 28 components, 25 faults, 8 schedules, 15 maintenance-history rows, and 12 CRS records.

All of the above have since been remediated - see the Implementation Status
sections below for the current state (`foreign_keys = ON`, zero FK
violations, WAL enabled, 12 indexes, 7 schedules after the orphan row was
archived).

These checks demonstrate that the problems are not only theoretical. The current database already contains broken relationships.

## Findings

### DB-01: Company isolation is incomplete

**Severity:** Critical

Authentication stores a session company in `app/auth.py:242-267`, but most operational tables do not contain `company_id` and most queries do not join through an owned aircraft.

Tables that currently have company ownership fields include:

- `Aircraft`;
- `Engineers`;
- `Personnel`;
- `MasterMEL`;
- `ToolCrib`;
- `Companies`, `Users`, and document-ingestion tables.

Important operational tables without complete company scoping include:

- `Components`;
- `Faults`;
- `SensorTelemetry`;
- `Schedule`;
- `MEL_Deferrals`;
- `PilotReports`;
- `MaintenanceHistory`;
- `CRS_Records`;
- `PartRecords`;
- `DigitalEvidence`;
- `EnvironmentalRiskLog`;
- `CAMSISGroundingLog`;
- `IoTToolReadings`.

**Affected code:**

- Global fleet reads: `app/routes/dashboard.py`, `workspace.py`, `calendar.py`, `mel.py`, `flight_log.py`, `due_list.py`, `personnel.py`, and `tool_crib.py`.
- Global work orders: `app/camp_extensions/routes_imdf.py:26-64`.
- Global evidence: `app/camp_extensions/digital_evidence.py:102-189` and `routes_evidence.py:23-51`.
- Global parts: `app/camp_extensions/parts_traceability.py:51-77` and `routes_parts.py:21-57`.
- Global schedule APIs: `app/camp_extensions/fullcalendar_schedule.py:49-140`.
- Ingestion IDORs: `app/routes/ingestion.py:17-72` and `app/ingestion/commit.py:50-128`.

**Effect:** An authenticated user can potentially read or mutate another company's records by supplying another company's aircraft ID, fault ID, schedule row ID, extraction ID, component ID, or part serial.

**Recommended fix:** Choose one explicit product decision:

1. implement real multi-company isolation; or
2. remove the tenant-facing behavior until isolation is complete.

If multi-company isolation is retained, derive ownership through `Aircraft.company_id` for every aircraft-linked table, add direct `company_id` to global/reference tables where needed, and centralize ownership checks in reusable database helpers.

### DB-02: Foreign-key enforcement is disabled

**Severity:** Critical

Tables declare some foreign keys, but `app/database.py:10-21` never executes:

```sql
PRAGMA foreign_keys = ON;
```

**Effect:** SQLite permits rows referencing deleted or nonexistent aircraft, components, faults, and telemetry. The live `PRAGMA foreign_key_check` result already contains 19 violations.

**Recommended fix:** Enable foreign keys on every connection, then repair existing violations before enforcing stricter constraints:

```python
conn.execute('PRAGMA foreign_keys = ON')
```

Do not enable this blindly in production before the repair migration has been tested against a copy of the database.

### DB-03: Destructive component migration deletes operational history

**Severity:** Critical

`app/database.py:65-151` invokes `_revamp_components_and_sensors()` from every connection until `ComponentRevampMarker` exists. The migration deletes:

- all `SensorTelemetry` rows;
- all `Components` rows;
- the test aircraft and selected dependent records.

Fault records are intentionally retained, but their component references are not remapped. This explains the 18 orphaned faults in the live database.

**Effect:** Historical telemetry and component identity are lost, while audit records remain linked to nonexistent components.

**Recommended fix:**

- Remove the destructive operation from request-time connection setup.
- Back up the database before any repair.
- Create an explicit, versioned migration.
- Preserve old components and telemetry, or create a documented mapping from old component IDs to new IDs.
- Only archive test data when it can be identified without ambiguity.
- Validate foreign-key integrity after migration.

### DB-04: Migrations are unversioned, repeated, and silently fail

**Severity:** High

Schema changes are attempted on every connection in `app/database.py:34-69`. Similar patterns exist in `app/auth.py`, `app/ingestion/schema.py`, `app/camp_extensions/ext_db.py`, and extension modules.

Many migration blocks catch `OperationalError` or broad `Exception` and continue as though the only possible cause were an already-existing column.

**Effect:** Real errors such as `database is locked`, malformed DDL, missing base tables, and disk failures are hidden. The application may continue against a partially migrated schema.

**Recommended fix:** Add a migration table such as:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

Apply migrations once, in order, under a migration lock. Catch only the expected duplicate-column/table condition, and fail loudly for all other errors.

### DB-05: The source schema and live schema have diverged

**Severity:** High

The root `app.py` contains a second database implementation and schema setup path. It creates different table definitions from the package application.

A concrete example is `PilotReports`:

- `app/database.py:185-191` creates `id`;
- the live database has `report_id`;
- `app/routes/fault_resolution.py` expects `report_id`;
- legacy `app.py` has both `id` and `rowid` assumptions.

`XAILogs` also has materially different columns between the legacy and current schemas.

**Effect:** A fresh database can behave differently from the existing database. Running `python app.py` instead of `python run.py` can produce inconsistent routes and migrations against the same file.

**Recommended fix:**

- Retire or clearly isolate the root `app.py`.
- Make `run.py` the only supported entry point.
- Define each table once in the package migration system.
- Add a fresh-database schema test and a current-database upgrade test.

### DB-06: The relational model lacks important foreign keys

**Severity:** High

Several logical relationships are only represented by text or integer IDs without constraints:

- `Schedule.aircraft_id -> Aircraft.aircraft_id`;
- `MEL_Deferrals.aircraft_id -> Aircraft.aircraft_id`;
- `MaintenanceHistory.aircraft_reg -> Aircraft.registration`;
- `DigitalEvidence.fault_id -> Faults.fault_id`;
- `DigitalEvidence.component_id -> Components.component_id`;
- `PartRecords.component_id -> Components.component_id`;
- `PartRecords.aircraft_id -> Aircraft.aircraft_id`;
- `EnvironmentalRiskLog.aircraft_id -> Aircraft.aircraft_id`;
- `CAMSISGroundingLog.component_id -> Components.component_id`;
- `IoTToolReadings.component_id -> Components.component_id`.

**Effect:** Deletion and update operations can leave orphaned records. Text-based registration references can become invalid if an aircraft registration changes.

**Recommended fix:** Use stable primary-key references, add foreign keys with deliberate `ON DELETE` behavior, and migrate `MaintenanceHistory`/CRS from free-text registration references to `aircraft_id`.

### DB-07: No indexes support normal application queries

**Severity:** High

The live database has no user-defined indexes. Frequent filters and sorts include:

- `company_id`;
- `aircraft_id`;
- `component_id`;
- `fault_id`;
- `recorded_at`;
- `status`;
- `start_time`;
- `source_type, source_id`.

**Effect:** Dashboard, telemetry, history, schedule, ingestion, and reasoner queries will become increasingly slow as data grows.

**Recommended fix:** Add indexes after reviewing query plans. Initial candidates include:

```sql
CREATE INDEX idx_components_aircraft ON Components(aircraft_id);
CREATE INDEX idx_telemetry_component_time ON SensorTelemetry(component_id, recorded_at DESC);
CREATE INDEX idx_faults_component_status ON Faults(component_id, resolved, fault_type);
CREATE INDEX idx_schedule_aircraft_time ON Schedule(aircraft_id, start_time);
CREATE INDEX idx_history_aircraft_date ON MaintenanceHistory(aircraft_reg, completion_date DESC);
CREATE INDEX idx_documents_source ON MaintenanceDocuments(source_type, source_id);
CREATE INDEX idx_ingestion_company_status ON IngestedDocuments(company_id, status);
```

Add company-leading indexes when company isolation is implemented.

### DB-08: No uniqueness or idempotency protections exist for key operations

**Severity:** High

The application uses read-then-write logic without database constraints in several places:

- open faults: `app/ontology_reasoner.py:366-376`;
- ingestion parsing: `app/ingestion/runner.py:59-73`;
- ingestion approval: `app/ingestion/commit.py:50-109`;
- maintenance document generation: `app/camp_extensions/maintenance_documents.py:190-218`;
- evidence chain position: `app/camp_extensions/digital_evidence.py:135-153`.

**Effect:** Double-clicks, retries, concurrent workers, and repeated diagnostics can create duplicate faults, pending rows, documents, or chain positions.

**Recommended fix:** Add appropriate unique constraints and use atomic conditional updates. Examples:

- unique open fault key, where supported by the business model;
- unique `(source_type, source_id)` for maintenance documents;
- unique or versioned extraction candidates;
- atomic `UPDATE ... WHERE status = 'Pending'` before approval;
- transactionally allocate evidence chain positions.

### DB-09: Background writers race with user transactions

**Severity:** High

Background threads write to the same SQLite database as web requests:

- diagnostic workers: `app/diagnostics_jobs.py:61-81`;
- kill switch: `app/camp_extensions/kill_switch.py`;
- schedule lifecycle: `app/camp_extensions/schedule_lifecycle.py`;
- HITL UDP listener: `app/camp_extensions/hitl_listener.py:141-176`.

The watchers select a row and later update it without rechecking its status. For example, a schedule can be signed off while the lifecycle watcher later marks it expired.

**Effect:** Completed work can be overwritten, duplicate actions can be logged, and SQLite lock errors can surface under load.

**Recommended fix:** Use conditional updates, for example:

```sql
UPDATE Schedule
SET status = 'Expired-AutoRemoved'
WHERE event_id = ? AND status = 'Scheduled';
```

Check the affected-row count. For production deployment, move scheduled/background work to a single worker process or a real job queue rather than starting daemon threads in every Flask process.

### DB-10: SQLite configuration is not production-safe

**Severity:** Medium-High

`app/config.py:18-20` only sets a 10-second timeout. The connection setup does not configure:

- WAL mode;
- foreign keys;
- busy retry/backoff;
- migration locking;
- connection health checks.

**Effect:** Concurrent requests and watchers can encounter `database is locked`, especially when migrations and long-running reasoner work overlap.

**Recommended fix:** Centralize connection initialization and explicitly configure the selected SQLite operating mode. Keep migrations out of normal request connections. If expected write volume grows, migrate operational data to PostgreSQL rather than expanding SQLite concurrency hacks.

### DB-11: Testing configuration does not represent the real database

**Severity:** Medium

`app/config.py:50-55` uses `:memory:` for testing, but `get_db()` opens a new connection for every operation. Each connection receives a separate in-memory database.

**Effect:** Multi-request tests, migration tests, background-job tests, and route tests do not share data correctly.

**Recommended fix:** Use a temporary file database per test session, or share one SQLite in-memory connection using a URI and controlled connection lifecycle. Disable background watchers in tests.

### DB-12: Extension schemas are created lazily

**Severity:** Medium

Many extension tables are created only when a feature is first visited or used. Examples include evidence, parts, ingestion, maintenance documents, IoT, CAMSIS, and lifecycle tables.

**Effect:** The database schema depends on user navigation order. Deployment checks cannot reliably know whether the schema is complete, and background threads can race table creation.

**Recommended fix:** Put all schema creation in the versioned migration system and validate the complete schema at startup. Startup should fail if required tables are missing.

### DB-13: Audit identity is stored as free text

**Severity:** Medium

Fields such as `signed_off_by`, `uploaded_by`, `scanned_by`, and `checked_out_to` store names or submitted strings rather than immutable user/engineer IDs.

**Effect:** Audit history is difficult to verify, names can change, and clients can submit false operator identities.

**Recommended fix:** Store `user_id`, `engineer_id`, and `company_id` alongside display snapshots. Derive the actor from the authenticated session rather than trusting form values.

## Remediation Plan

### Phase 0: Protect and baseline the data

1. Stop the application.
2. Copy `camp_system.db` to a protected backup location.
3. Record the current schema and row counts.
4. Do not run the destructive component revamp against the only copy.
5. Decide whether the current database is development data or must be preserved as operational data.

Suggested baseline commands:

```powershell
Copy-Item camp_system.db camp_system.db.pre-db-repair.bak
sqlite3 camp_system.db ".dump" > camp_system_db_baseline.sql
sqlite3 camp_system.db "PRAGMA foreign_key_check;"
```

### Phase 1: Establish one schema authority

1. Make `run.py` plus `app/` the only supported application path.
2. Retire or isolate root `app.py`.
3. Create a migration runner and `schema_migrations` table.
4. Move extension schema creation into migrations.
5. Remove migrations from `get_db_connection()`.
6. Stop swallowing unexpected migration errors.

### Phase 2: Repair referential integrity

1. Enable `PRAGMA foreign_keys = ON` for every connection.
2. Write a repair migration for existing orphaned faults and schedules.
3. Decide whether orphaned historical rows should be archived, remapped, or marked invalid.
4. Add missing foreign keys and explicit delete/update policies.
5. Run `PRAGMA foreign_key_check` and require zero violations.

### Phase 3: Repair the data model

1. Use stable `aircraft_id` references instead of registration text.
2. Add company ownership through all aircraft-linked tables.
3. Add immutable actor IDs to audit records.
4. Add indexes for common filters and joins.
5. Add uniqueness constraints for records that must not be duplicated.

### Phase 4: Make writes safe under retry and concurrency

1. Make ingestion parsing and approval idempotent.
2. Make document generation idempotent.
3. Use conditional updates for schedule lifecycle and kill-switch actions.
4. Prevent duplicate diagnostics for the same aircraft while one is running.
5. Move background work out of Flask worker processes.

### Phase 5: Add tenancy enforcement

1. Add reusable ownership queries/helpers.
2. Scope every list query by company.
3. Validate ownership before every ID-based read or write.
4. Test cross-company access with two companies and identical resource shapes.

## Implementation Status

### Phase 1: One schema authority - IMPLEMENTED (2026-08-18)

- **`app/migrations.py`** is now the single schema authority. All DDL and
  seed data (core tables, auth/company tables, ingestion tables, every
  extension table, IMDF/fullcalendar/company additive columns) live in four
  ordered migrations (`001_core_operational_schema`,
  `002_auth_company_schema`, `003_ingestion_pipeline_schema`,
  `004_camp_extension_schema`).
- Each applied migration is recorded in the new `schema_migrations` table.
  `run_migrations()` is idempotent: once the schema is current, the common
  path is a read-only no-op (no write lock taken on every request).
- Pending migrations apply inside one `BEGIN IMMEDIATE` transaction with a
  short busy retry, then `COMMIT`; any unexpected error rolls back and is
  raised - no more swallowed `OperationalError`.
- The only tolerated failure is "duplicate column name" inside the guarded
  `_add_column()` helper used by additive `ALTER TABLE` upgrade paths.
- **The Round-3 destructive component/sensor revamp is retired.** It no
  longer runs from `get_db_connection()`. `ComponentRevampMarker` is still
  created (with a retirement note) so existing tooling finds it, but no
  telemetry/component rows are ever deleted or rewritten by migrations.
- `app/database.py` is now pure connection management (no schema work).
  `get_db_connection()` opens a plain connection and does nothing else.
- All `ensure_*_schema()` functions (`auth`, ingestion, diagnostics jobs,
  and every extension) are thin wrappers that call `run_migrations()`.
  `app/camp_extensions/ext_db.py` has been deleted.
- `app/__init__.py::create_app()` runs `run_migrations()` at startup; a
  failing migration prevents the application from starting.
- The root `app.py` has been isolated to `archives/legacy_app.py` (with a
  header warning) - `python run.py` is the only supported entry point.
- **Schema additions delivered by these migrations:** `Schedule.source` and
  `Schedule.related_reference` (FullCalendar), `MasterMEL.source_document_id`
  and `Directives.source_document_id` (ingestion provenance) - previously
  these were only created lazily and were missing from the live database.

Not yet done (tracked as later phases):

- Phase 2: `PRAGMA foreign_keys = ON` on every connection, plus a repair
  migration for the 19 existing violations (18 orphaned faults, 1 orphaned
  schedule row). The orphaned rows have been left untouched by Phase 1.
- Phase 2: missing foreign keys (DB-06), indexes (DB-07), WAL/busy settings
  (DB-10), `TestingConfig` file-based database (DB-11), conditional
  background updates (DB-09), company isolation enforcement (DB-01/DB-13,
  Phase 5).

### Phase 2A: Referential-integrity repair - IMPLEMENTED (2026-08-18)

- **`PRAGMA foreign_keys = ON` is now executed on every connection** - in
  `app/database.py:get_db_connection()` and in the migration runner's
  `_connect()`. Existing write paths were reviewed for FK safety before
  enabling; the PIREP route (`app/routes/flight_log.py`) now validates the
  aircraft before inserting a report/component/fault.
- **Migration `005_fk_orphan_repair`** repairs the known orphans against a
  copy-verified policy:
  - 18 `Faults.component_id` values pointing at deleted components were
    archived to the new `FKRepairAudit` table and set to `NULL` (history is
    preserved, not deleted).
  - 1 `Faults.telemetry_id` value (`fault_id = 1`) referencing missing
    telemetry was archived and set to `NULL`.
  - 1 `Schedule` row (`event_id = 7`, deleted `Aircraft_5N_NAF`) was
    archived in full (JSON snapshot) and removed, so later FK additions
    start from a clean slate.
- **`PRAGMA foreign_key_check` now returns zero rows** on the live database.
- Row counts are preserved: 4 aircraft, 28 components, 25 faults (18 with
  NULL component refs), 7 schedules (was 8).
- **Tests added** in `tests/test_migrations.py` (run with `pytest tests`):
  fresh-database migration completeness, migration idempotency, upgrade
  repair of orphan faults/telemetry/schedules with audit-table verification,
  and FK enforcement on application connections.
- Backup of the pre-repair database: `camp_system.db.pre-repair-backup.bak`
  (also covered by `*.bak` in `.gitignore`).

### Phase 2B: Missing foreign keys (DB-06) - IMPLEMENTED (2026-08-18)

- **Migration `006_missing_foreign_keys`** rebuilds the seven tables that
  lacked constraints (`Schedule`, `MEL_Deferrals`, `DigitalEvidence`,
  `PartRecords`, `EnvironmentalRiskLog`, `CAMSISGroundingLog`,
  `IoTToolReadings`). SQLite cannot add FKs via `ALTER TABLE`, so each table
  is recreated with identical columns plus FK clauses, rows are copied, the
  old table is dropped, and the new one renamed (AUTOINCREMENT sequences
  preserved via `sqlite_sequence`).
- **Delete policy (confirmed product decision):**
  - `Schedule.aircraft_id` and `MEL_Deferrals.aircraft_id` use
    `ON DELETE RESTRICT` - aircraft deletion is blocked while linked
    schedule/MEL records exist.
  - All audit/history references use `ON DELETE SET NULL` so records
    survive parent retirement: evidence (aircraft/fault/component),
    parts (component/aircraft/replaced_by_serial), environmental risk
    log, CAMSIS grounding (component/limit), IoT readings
    (tool/task/component/spec), and `MEL_Deferrals.mmel_id`.
- **Preflight guard:** the migration aborts loudly (rolls back) if any
  orphan rows are found in the 17 checked relationships, instead of
  silently copying bad data. The live database passed with zero orphans.
- **Write paths updated for clean errors instead of 500s:**
  - `app/routes/workspace.py::remove_aircraft` catches the FK block and
    flashes an explanation.
  - Schedule creation (`calendar.py`, `fullcalendar_schedule.py`), MEL
    deferral creation (`mel.py`), evidence upload
    (`digital_evidence.py`), part registration (`parts_traceability.py`,
    `routes_parts.py`), and IoT ingestion (`iot_tools.py`) validate
    parent IDs before inserting.
- **`PRAGMA foreign_key_check` returns zero rows** on the live database;
  row counts are preserved (4 aircraft, 28 components, 25 faults,
  7 schedules, 6 MEL deferrals, 112 CAMSIS logs).
- **Tests:** 9 tests in `tests/test_migrations.py` now cover fresh schema
  FK declarations, invalid child inserts, aircraft-deletion blocking,
  SET NULL preservation of audit rows, and the 005->006 upgrade path.
- Backup of the pre-2B database: `camp_system.db.pre-fk2b.bak`.

### Phase 2C: Indexes, WAL, and test isolation (DB-07 / DB-10 / DB-11) - IMPLEMENTED (2026-08-19)

- **Migration `007_query_indexes`** creates 12 indexes, each justified by a
  real query path verified with `EXPLAIN QUERY PLAN` (all previously full
  table scans):
  - `Schedule(start_time)` - calendar/dashboard ordering.
  - `Schedule(aircraft_id, status)` - kill-switch open-event lookup.
  - `SensorTelemetry(component_id, sensor_type, recorded_at)` - telemetry
    pages, ontology reasoner `MAX(recorded_at)`, ghost-data dedup.
  - `Faults(component_id, resolved, fault_type)` - reasoner active-fault
    lookup.
  - `DigitalEvidence(aircraft_id, chain_position)` and
    `(fault_id, chain_position)` - evidence chain reads (upload + IMDF).
  - `PartRecords(component_id, status)` - IMDF in-service/removed lists.
  - `MEL_Deferrals(aircraft_id, status)` - kill-switch MEL close.
  - `PilotReports(status)` - ghost data + flight log open-report scans.
  - `MaintenanceDocuments(generated_at)` and
    `(source_type, source_id)` - document list + dedup lookups.
  - `Components(aircraft_id)` - CAMSIS/environment component scans.
- **WAL journal mode (DB-10):** persisted in the database file by migration
  007's post-commit step (SQLite refuses `journal_mode` changes inside a
  transaction). Background watchers (kill switch, schedule lifecycle) and
  request handlers can now read while a writer is active. `synchronous`
  intentionally stays at the default FULL - durability semantics unchanged.
- **`busy_timeout` (DB-10):** applied per connection
  (`PRAGMA busy_timeout = DB_BUSY_TIMEOUT_MS`, 10s) in both
  `app/database.py::get_db_connection()` and the migration runner, so lock
  contention waits instead of failing instantly.
- **TestingConfig is file-backed (DB-11):** `:memory:` gave every
  `sqlite3.connect()` a separate empty database, so `create_app('testing')`
  ran migrations on one database and request connections saw nothing.
  `TestingConfig.DATABASE_PATH` now resolves to `CAMP_DATABASE_PATH` or a
  temp file. Tests cover shared-schema app factory startup, cross-connection
  visibility, and isolation between runs.
- **Verified on live database:** migration 7 recorded, `PRAGMA journal_mode`
  = `wal`, `PRAGMA integrity_check` = `ok`, `PRAGMA foreign_key_check` = 0
  rows, row counts unchanged (4 aircraft / 28 components / 7 schedules /
  6 MEL deferrals), and the previously full-scan hot queries now use the new
  indexes (`EXPLAIN QUERY PLAN`).
- **Tests:** 16 total - added 007 index declaration/plan/WAL tests,
  busy_timeout assertion, and the 3 app-factory tests in
  `tests/test_app_factory.py`.
- Backup of the pre-2C database: `camp_system.db.pre-2c.bak`.
- **Deliberately NOT indexed in 007:** the remaining DB-07 candidates from
  the findings section. `MaintenanceHistory(aircraft_reg, completion_date)`
  and `IngestedDocuments(company_id, status)` are deferred because no code
  path currently filters on those columns (the CBR engine intentionally
  full-scans history for TF-IDF matching, and the history page is a UNION
  with no per-aircraft filter). Company-leading indexes on every table are
  deferred to Phase 5, where they belong alongside DB-01 tenancy
  enforcement - adding `company_id` indexes now would only slow writes.

### Phase 3A: Uniqueness & idempotency guards (DB-08) - IMPLEMENTED (2026-08-19)

- **Audit:** every duplicate-prone read-then-write path was measured against
  the live database first. Zero duplicate rows existed in any shape - these
  constraints are preventive, not repairs.
  - `Faults` open `(component_id, fault_type)` - 0 duplicates (resolved
    repeats like `Engine_Overheat_Critical` are legitimate re-detections).
  - `IngestedDocuments(doc_id)` - 0 duplicates (1:1 with AircraftDocuments).
  - `MaintenanceDocuments(source_type, source_id)` - 0 duplicates.
  - `DigitalEvidence(aircraft_id, chain_position)` - 0 duplicates.
- **Migration `008_uniqueness_constraints`** adds:
  - `idx_faults_open_component_type`: partial unique index on
    `Faults(component_id, fault_type) WHERE resolved = 0` - matches the
    ontology reasoner's dedup query exactly. A resolved fault may
    legitimately be detected again, and SQLite treats NULL `component_id`
    as distinct (legacy repaired rows, PIREP airframe faults never
    collide).
  - `idx_ingested_documents_doc_id`: unique - one ingestion row per
    document.
  - `idx_maintdocs_source_unique`: unique `(source_type, source_id)` -
    makes the PDF dedup lookup race-safe; replaces the redundant non-unique
    `idx_maintdocs_source` from 007 (dropped in the same migration).
  - `idx_evidence_chain_position`: unique `(aircraft_id, chain_position)` -
    two concurrent uploads can no longer claim the same position and break
    the tamper-evident hash chain; replaces the redundant non-unique
    `idx_evidence_aircraft_chain` from 007 (dropped in the same migration).
- **Write paths hardened** (IntegrityError is treated as "already
  recorded", never a 500):
  - `app/ontology_reasoner.py` - concurrent reasoner runs collapse into
    one open fault.
  - `app/routes/flight_log.py` - identical duplicate PIREPs still record
    the pilot report; only the redundant fault row is skipped.
  - `app/camp_extensions/maintenance_documents.py` - a lost race removes
    the orphan PDF it just wrote and returns the winner's record.
  - `app/camp_extensions/digital_evidence.py` - a chain-position collision
    recomputes the position/hash and retries (3 attempts).
- **Deliberately NOT constrained:** `DiagnosticJobs` (uuid job_id; repeated
  "Run Diagnostics" clicks are separate legitimate runs) and the append-only
  audit logs (`XAILogs`, `PartScanLog`, `ScheduleLifecycleLog`, ...) where
  duplicate rows are normal.
- **Fails loudly:** if a future database does contain duplicates, migration
  008 aborts and rolls back (same policy as 006) - verified by
  `test_migration_008_fails_loudly_on_preexisting_duplicates`.
- **Verified on live database:** migration 8 recorded, `PRAGMA
  integrity_check` = `ok`, `PRAGMA foreign_key_check` = 0 rows, all four
  unique indexes present, redundant 007 indexes gone, zero duplicate rows,
  row counts unchanged (4 aircraft / 28 components / 25 faults / 7
  schedules / 6 MEL deferrals).
- **Tests:** 22 total - 6 new tests in `tests/test_migrations.py` covering
  index declaration, per-table rejection of duplicates, legitimate repeats
  (resolved faults, NULL components, other aircraft), and the loud-failure
  upgrade path.
- Backup of the pre-3A database: `camp_system.db.pre-3a.bak`.

Still open for later phases:

- Ingestion parsing idempotency (the remaining DB-08 candidate:
  `app/ingestion/runner.py` pending rows) - the approval half was closed
  alongside tenancy in Phase 5 (the commit claim is conditional and the
  target-row company_id is derived from the owning ingestion row).

### Phase 4: Conditional background-writer updates (DB-09) - IMPLEMENTED (2026-08-19)

The watchers SELECT open rows and later UPDATE them. If a user signs off
between the two statements, the unconditional UPDATE overwrote the newer
status and the audit log claimed an action that never happened. All four
writers from the finding were reviewed:

- **`app/camp_extensions/kill_switch.py` (fixed):** the Schedule and
  MEL_Deferrals auto-close UPDATEs are now conditional
  (`WHERE rowid = ? AND (status = 'Scheduled' OR status IS NULL)` /
  `WHERE deferral_id = ? AND status = 'Active'`), the audit-log insert only
  runs when `rowcount > 0`, and the processed-CRS marker uses
  `INSERT OR IGNORE` so two concurrent scans cannot crash on the primary
  key.
- **`app/camp_extensions/schedule_lifecycle.py` (fixed):** the 2-day expiry
  UPDATE is now conditional on the row still being open, the lifecycle log
  only records actual expiries (`rowcount > 0`), and reminder firing uses
  `INSERT OR IGNORE` (record_id is the primary key) so concurrent or repeat
  scans are no-ops instead of IntegrityErrors.
- **`app/diagnostics_jobs.py` (no change needed):** each background worker
  owns its `DiagnosticJobs` row by uuid job_id - no user-facing state is
  ever overwritten, so there is no race to guard.
- **`app/camp_extensions/hitl_listener.py` (no change needed):** a pure
  producer - INSERTs into `SensorTelemetry`/`HITLPacketLog` and its own
  `HITLListenerConfig` row; it never mutates rows a user can see.
- **Tests:** 5 new tests in `tests/test_background_writers.py` covering
  expiry skipping signed-off/cancelled events, single-fire reminders,
  kill-switch open-events-only cancellation, and two-thread concurrent scans
  (which log each action exactly once thanks to SQLite's single-writer
  serialization). Full suite: 27 tests.
- No migration or live-database change was required - this phase is
  code-only.

### Phase 3B: Stable aircraft references (Phase 3 of the roadmap) - IMPLEMENTED (2026-08-19)

`MaintenanceHistory.aircraft_reg` and `CRS_Records.aircraft_reg` were
free-text registrations in two formats (`5N-TAJ` with dashes, `5N_TAJ` with
underscores) with no referential link to the Aircraft table - the same
free-text values the kill switch had to guess from.

- **Migration `009_stable_aircraft_refs`** adds an `aircraft_id` column to
  both tables (`REFERENCES Aircraft(aircraft_id) ON DELETE SET NULL`,
  matching the Phase 2B audit-reference policy) and backfills it by matching
  the free-text value against `Aircraft.registration`, treating dashes and
  underscores as equivalent. Rows that match no aircraft keep their
  free-text registration with a NULL `aircraft_id` - preserved, not
  deleted. The free-text column stays as the human-readable display value.
- **Write paths record the stable id:**
  - `app/cbr_engine.py::log_maintenance_action` (used by calendar sign-off,
    fault resolution, due-list completion, and MEL clearance) resolves and
    stores `aircraft_id` via a shared `_resolve_aircraft_id()` helper.
  - `app/routes/fault_resolution.py` stores `aircraft_id` on CRS creation.
  - `app/camp_extensions/kill_switch.py` now prefers `crs['aircraft_id']`
    over its old string-guess (the guess remains as a fallback for rows
    created before this migration).
- **Verified on live database:** migration 9 recorded, all 15 history rows
  and 12 CRS rows backfilled to the correct aircraft (0 unlinked), `PRAGMA
  integrity_check` = `ok`, `PRAGMA foreign_key_check` = 0 rows.
- **Tests:** 4 new tests in `tests/test_migrations.py` covering the upgrade
  backfill (dash + underscore + unmatched), FK declarations, SET NULL on
  aircraft deletion, and `log_maintenance_action` resolution. Full suite:
  31 tests.
- No indexes on the new columns yet - no query filters on them today, and
  tenant-leading indexes belong with Phase 5 (DB-01) enforcement.
- Backup of the pre-3B database: `camp_system.db.pre-3b.bak`.

### Phase 5: Company tenancy enforcement (DB-01/DB-13) - IMPLEMENTED (2026-08-19)

DB-01: most operational tables had no `company_id` column, so an
authenticated user could read or mutate another company's records by ID
(dashboard, telemetry, MEL, faults, evidence, parts, ingestion approval).
This phase adds ownership to every operational table and enforces it on
every read/write path.

- **Migration `010_company_tenancy`** adds `company_id INTEGER NOT NULL
  DEFAULT 1` to every operational table (Components, Schedule,
  MEL_Deferrals, PilotReports, DigitalEvidence, PartRecords,
  EnvironmentalRiskLog, DiagnosticJobs, ScheduleReminders,
  AircraftEnvironmentContext, Faults, SensorTelemetry, XAILogs,
  CAMSISGroundingLog, IoTToolReadings, HITLPacketLog, KillSwitchProcessedCRS,
  KillSwitchLog, MaintenanceHistory, CRS_Records, MaintenanceDocuments,
  PartScanLog, ScheduleLifecycleLog, ExtractionAuditLog, MaintenanceRecords,
  LegalSignOffs). Ownership is backfilled through the nearest owning row
  (aircraft -> components -> telemetry/faults; CRS -> kill-switch logs; part
  -> scan log; schedule -> lifecycle log; extraction -> audit log); rows
  whose owner cannot be resolved fall back to the seeded company (1) and are
  preserved, never deleted. Company-leading indexes are added on the hot
  query paths.
- **`app/tenancy.py`** centralizes ownership: `current_company_id()`
  (session/`g`, with a safe default outside request contexts),
  `find_owned()`/`require_owned()` and named helpers
  (`owned_aircraft`, `require_fault`, `owned_part`, ...). Routes never guess
  a company id or hand-roll a filter.
- **Route scoping (IDOR fixes):** every route now filters list queries and
  validates ownership before ID-based reads/writes - dashboard, workspace,
  calendar, history, flight log, due list, personnel, tool crib, MEL
  (deferral creation/resolution, MMEL lookups), telemetry (poll/clear/
  history APIs 404 on foreign aircraft, component seeding is scoped),
  reasoner (`run_reasoner` verifies the aircraft and threads `company_id`
  through the background job), fault resolution (fault/engineer/component/
  part ownership, CRS and history stamped), and the ingestion review
  (`routes/ingestion.py`).
- **Service/background modules:** `cbr_engine`, `ontology_reasoner`,
  `diagnostics_jobs`, and every extension service (`imdf`, `digital_evidence`,
  `parts_traceability`, `maintenance_documents`, `camsis`,
  `environmental_stressor`, `ghost_data`, `schedule_lifecycle`,
  `fullcalendar_schedule`, `hitl_listener`, `iot_tools`, `kill_switch`)
  accept an explicit `company_id=` and stamp/filter every row. Request-context
  functions default to the session company; watchers and listener threads
  (kill switch, schedule lifecycle, HITL) never touch the Flask session -
  they iterate `Companies` and run one sweep per tenant.
- **Ingestion approval** (`app/ingestion/commit.py`) now requires the
  extraction row to belong to the caller's company, derives the target row's
  `company_id` from the owning ingestion row (never from submitted data),
  and keeps the conditional status claim so a racing second approval can
  never commit twice (DB-08/DB-01 together).
- **Tests:** `tests/test_tenancy.py` (10 tests) seeds two companies with
  identically-shaped data and verifies list isolation (dashboard, personnel,
  tool crib, MEL, history) plus IDOR rejection (telemetry poll/history,
  reasoner, fault resolution against foreign IDs). Full suite: 42 tests.
- Backup of the pre-tenancy database: `camp_system.db.pre-3b.bak` (the 010
  migration is applied to a copy first, then the live file once verified).

## Test Plan

*Phases 2A-5 delivered automated coverage in `tests/test_migrations.py`,
`tests/test_app_factory.py`, `tests/test_background_writers.py`, and
`tests/test_tenancy.py` (fresh
migration, idempotency, orphan-repair upgrade, FK enforcement, indexes, WAL,
busy_timeout, app-factory test isolation, uniqueness/idempotency guards,
conditional background-writer updates, stable aircraft references,
cross-company isolation). Run with
`python -m pytest tests`. The manual scenarios below remain relevant for
broader regression coverage.*

### Fresh database tests

- Create a new empty database.
- Run the complete migration set once.
- Confirm every required table exists.
- Confirm all expected columns and indexes exist.
- Confirm no migration is re-applied on the second connection.
- Confirm `PRAGMA foreign_key_check` returns no rows.

### Existing database upgrade tests

- Use a copy of the current database, never the only live copy.
- Apply migrations.
- Verify all existing valid records remain available.
- Verify orphan records are handled according to the repair policy.
- Verify row counts before and after each migration.
- Verify no telemetry, components, faults, or audit records disappear unexpectedly.

### Referential-integrity tests

Attempt to:

- delete an aircraft with components;
- insert a component for a nonexistent aircraft;
- insert telemetry for a nonexistent component;
- insert a fault for a nonexistent component;
- attach evidence to a nonexistent fault or aircraft;
- register a part for a nonexistent aircraft.

Each operation should either fail cleanly or follow a documented archive/cascade policy.

### Tenancy tests

Create Company A and Company B, then verify that a Company A user cannot:

- list Company B aircraft;
- view Company B faults or schedules;
- resolve Company B faults;
- upload evidence to Company B aircraft;
- move or cancel Company B schedules;
- approve Company B document extractions;
- read Company B parts or maintenance history.

### Concurrency tests

- Submit two diagnostic requests for the same aircraft simultaneously.
- approve one extraction from two sessions simultaneously.
- parse one document repeatedly and concurrently.
- sign off a schedule while lifecycle expiry runs.
- upload multiple evidence records for one aircraft concurrently.
- run multiple application workers against the same test database.

Expected results should include no duplicate records, no overwritten completed states, no duplicate evidence chain positions, and no unhandled lock errors.

### Regression tests

Run the existing application flows after migration:

- login and company setup;
- aircraft creation and update;
- telemetry polling and fault injection;
- diagnostics and fault resolution;
- calendar creation and sign-off;
- MEL deferral and resolution;
- PIREP creation and closure;
- evidence and parts documentation;
- document ingestion, review, approval, and rejection;
- maintenance-history and CRS PDF generation.

## Exit Criteria

Database remediation should not be considered complete until:

- there is one schema authority;
- migrations are versioned and fail loudly;
- `PRAGMA foreign_keys` is enabled;
- `PRAGMA foreign_key_check` returns zero rows;
- no destructive migration runs during ordinary requests;
- required indexes and uniqueness constraints exist;
- cross-company access tests pass, if tenancy remains enabled;
- concurrency tests pass without duplicate or lost records;
- fresh and upgraded databases produce the same schema;
- the application has a documented backup and restore procedure.
