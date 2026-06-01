"""
C.O.R.E. CAMP - Application Entry Point

This is the main script to run the Flask application.
Usage: python run.py
"""
from app import create_app
import os

# Get environment
ENV = os.getenv('FLASK_ENV', 'development')

# Create and run app
if __name__ == '__main__':
    app = create_app(config_name=ENV)
    
    print("🚀 C.O.R.E. CAMP (Continuous Ontology Reasoning Engine)")
    print("✈️  Aircraft Maintenance Management System")
    print(f"📍 Environment: {ENV.upper()}")
    print("🌐 URL: http://127.0.0.1:5000")
    print("⚠️  Press CTRL+C to stop the server")
    print("-" * 60)
    
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=app.config.get('DEBUG', False)
    )
