"""
Database connection and management for C.O.R.E. CAMP.
Implements self-healing schema migration pattern.
"""
import sqlite3
from contextlib import contextmanager
from app.config import Config


def get_db_connection():
    """
    Get a database connection with self-healing schema migrations.
    Automatically applies ALTER TABLE statements for new features.
    """
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=Config.DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    
    # Apply schema migrations
    _apply_schema_migrations(conn)
    
    return conn


@contextmanager
def get_db():
    """Context manager for database connections - auto closes."""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def _apply_schema_migrations(conn):
    """Apply all pending schema migrations."""
    migrations = [
        # Faults table enhancements
        "ALTER TABLE Faults ADD COLUMN resolved_by TEXT",
        "ALTER TABLE Faults ADD COLUMN resolved_date TEXT",
        
        # MaintenanceHistory enhancements
        "ALTER TABLE MaintenanceHistory ADD COLUMN completion_date DATETIME DEFAULT CURRENT_TIMESTAMP",
        
        # MaintenanceTasks enhancements
        "ALTER TABLE MaintenanceTasks ADD COLUMN target_model TEXT DEFAULT 'ALL'",
        
        # Components table (lifecycle tracking)
        "ALTER TABLE Components ADD COLUMN csn INTEGER DEFAULT 0",
        "ALTER TABLE Components ADD COLUMN max_csn INTEGER DEFAULT 5000",
        
        # Schedule table
        "ALTER TABLE Schedule ADD COLUMN status TEXT DEFAULT 'Scheduled'",
    ]
    
    for migration in migrations:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            # Column already exists or other error - continue
            pass
    
    # Create new tables that might not exist
    _create_tables_if_not_exist(conn)


def _create_tables_if_not_exist(conn):
    """Create tables that should always exist."""
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS CRS_Records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aircraft_reg TEXT,
                reference_id TEXT,
                description TEXT,
                signed_off_by TEXT,
                release_date DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    except sqlite3.OperationalError:
        pass
    
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS XAILogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                component_id TEXT, 
                ai_decision TEXT, 
                explanation_text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    except sqlite3.OperationalError:
        pass
    
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS PilotReports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aircraft_id TEXT,
                reported_by TEXT,
                discrepancy_text TEXT,
                status TEXT DEFAULT 'Open'
            )
        ''')
    except sqlite3.OperationalError:
        pass
    
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS SWRLRules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT NOT NULL,
                rule_body TEXT NOT NULL,
                status TEXT DEFAULT 'Pending Review',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
