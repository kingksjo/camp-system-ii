"""
Configuration settings for C.O.R.E. CAMP application.
"""
import os
import tempfile

class Config:
    """Base configuration."""
    
    # Flask Settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    # Upload Settings
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size
    
    # Database Settings
    DATABASE_PATH = os.environ.get('CAMP_DATABASE_PATH', 'camp_system.db')
    DB_TIMEOUT = 10.0  # SQLite timeout for concurrent access
    DB_BUSY_TIMEOUT_MS = 10000  # busy_timeout per connection (mirrors DB_TIMEOUT)
    
    # Ontology Settings
    ONTOLOGY_PATH = '.'
    BASE_ONTOLOGY = 'camp.owl'
    MOA_ONTOLOGY = 'camp_multi_ontology.owl'
    
    # CBR Settings
    CBR_SIMILARITY_THRESHOLD = 0.3
    CBR_TOP_RESULTS = 3


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    TESTING = False
    
    def __init__(self):
        """Validate production config on instantiation."""
        super().__init__()
        if not os.environ.get('SECRET_KEY'):
            raise ValueError("SECRET_KEY environment variable must be set for production")


class TestingConfig(Config):
    """Testing configuration - file-backed database.

    ':memory:' is unusable here: every sqlite3.connect(':memory:') creates a
    separate, empty database, so the migration connection would never share
    schema with request connections. A real temp file makes the app factory,
    migrations, and every connection operate on one database (Phase 2C,
    DB-11).
    """
    TESTING = True
    DEBUG = True
    DATABASE_PATH = (
        os.environ.get('CAMP_DATABASE_PATH')
        or os.path.join(tempfile.gettempdir(), 'camp_core_testing.db')
    )


# Configuration selector
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
