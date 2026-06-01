"""
__init__.py for app routes module.
"""
from flask import Blueprint

# Create blueprints for each route module
def register_blueprints(app):
    """Register all route blueprints with the Flask app."""
    from app.routes import (
        dashboard, workspace, fault_resolution, due_list,
        calendar, mel, tool_crib, reasoner, flight_log,
        personnel, history
    )
    
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(workspace.bp)
    app.register_blueprint(fault_resolution.bp)
    app.register_blueprint(due_list.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(mel.bp)
    app.register_blueprint(tool_crib.bp)
    app.register_blueprint(reasoner.bp)
    app.register_blueprint(flight_log.bp)
    app.register_blueprint(personnel.bp)
    app.register_blueprint(history.bp)
