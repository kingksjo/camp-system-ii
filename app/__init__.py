"""
C.O.R.E. CAMP (Continuous Ontology Reasoning Engine)
Aircraft Maintenance Management System

Main application entry point with Flask setup and blueprint registration.
"""
from flask import Flask
import os
import json
from app.config import Config
from app.database import get_db_connection, get_db
from app.routes import register_blueprints


def create_app(config_name='development'):
    """
    Application factory function.
    
    Args:
        config_name (str): Configuration environment ('development', 'production', 'testing')
    
    Returns:
        Flask: Configured Flask application instance
    """
    # Get the root directory (parent of app/ folder)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    template_dir = os.path.join(root_dir, 'templates')
    static_dir = os.path.join(root_dir, 'static')
    
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    
    # Load configuration
    from app.config import config as config_dict
    app.config.from_object(config_dict.get(config_name, config_dict['default']))
    
    # Create upload folder
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Register blueprints (route modules)
    register_blueprints(app)
    
    # Register template filters
    app.jinja_env.filters['abs'] = lambda x: abs(x)
    
    return app


# Create application instance
app = create_app()


if __name__ == '__main__':
    print("🚀 C.O.R.E. CAMP (Continuous Ontology Reasoning Engine) is online!")
    print("📍 Running on http://127.0.0.1:5000")
    print("⚠️  DEBUG MODE: Remember to disable in production!")
    
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=app.config['DEBUG']
    )
