"""
Configuration settings for C.O.R.E. CAMP application.
"""
import os

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
    DATABASE_PATH = 'camp_system.db'
    DB_TIMEOUT = 10.0  # SQLite timeout for concurrent access
    
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
    """Testing configuration."""
    TESTING = True
    DATABASE_PATH = ':memory:'
    DEBUG = True


# Configuration selector
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
