"""
Database connection management for C.O.R.E. CAMP.

All schema work (CREATE / ALTER / seeds) lives in app/migrations.py and runs
once, versioned, at application startup - nothing here mutates the schema.
See DATABASE_AUDIT_GUIDELINES.md (Phase 1: single schema authority).
"""
import sqlite3
from contextlib import contextmanager
from app.config import Config


def get_db_connection():
    """
    Get a plain database connection. No migrations, no schema changes -
    connections are safe to open on every request.
    """
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=Config.DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections - auto closes."""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
