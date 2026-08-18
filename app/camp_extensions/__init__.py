"""
C.O.R.E. CAMP Extensions Package
================================

Everything under app/camp_extensions/ implements the items the gap analysis
marked Partial or Not Found:

  #4  HITL FlightGear UDP listener      -> hitl_listener.py   / routes_hitl.py
  #9  FullCalendar hangar schedule      -> fullcalendar_schedule.py
  #10 Calendar kill switch              -> kill_switch.py     / routes_kill_switch.py
  #11 Geotagged digital evidence        -> digital_evidence.py/ routes_evidence.py
  #12 CAMSIS-3 data grounding           -> camsis.py           / routes_camsis.py
  #13 Ghost data elimination            -> ghost_data.py       / routes_ghost_data.py
  #18 IoT smart tool (Bluetooth)        -> iot_tools.py        / routes_iot_tools.py
  #19 RFID/QR part traceability         -> parts_traceability.py / routes_parts.py
  #20 Environmental stressor (Layer 7)  -> environmental_stressor.py / routes_environmental.py

Not one existing file under app/ (config.py, database.py, utils.py,
cbr_engine.py, ontology_reasoner.py, or any module in app/routes/) is
imported for modification anywhere in this package - only read-only helpers
(get_db, Config) are reused. Every new table is created with
CREATE TABLE IF NOT EXISTS / additive ALTER TABLE, the same self-healing
pattern app/database.py already uses, so this package can be deleted
wholesale with zero impact on the rest of the application.

Integration is a single call: register_camp_extensions(app), added at the
bottom of run.py (see INTEGRATION_GUIDE.md for the exact two lines).
"""
from app.camp_extensions import (
    routes_hitl, fullcalendar_schedule, routes_kill_switch, routes_evidence,
    routes_camsis, routes_ghost_data, routes_iot_tools, routes_parts, routes_environmental,
    routes_schedule_reminders, routes_maintenance_documents, routes_imdf,
)
from app.camp_extensions import kill_switch, hitl_listener, schedule_lifecycle, maintenance_documents, imdf


def register_camp_extensions(app):
    """Register every extension blueprint and start the background watchers. Idempotent."""
    for module in (routes_hitl, fullcalendar_schedule, routes_kill_switch, routes_evidence,
                   routes_camsis, routes_ghost_data, routes_iot_tools, routes_parts, routes_environmental,
                   routes_schedule_reminders, routes_maintenance_documents, routes_imdf):
        if module.bp.name not in app.blueprints:
            app.register_blueprint(module.bp)

    # Pre-create every extension's tables up front so the first page load
    # of any feature (including ones the reasoner touches passively, like
    # the HITL listener writing into SensorTelemetry) never races a
    # CREATE TABLE against a concurrent request.
    hitl_listener.ensure_hitl_schema()
    kill_switch.ensure_ks_schema()
    schedule_lifecycle.ensure_lifecycle_schema()
    maintenance_documents.ensure_documents_schema()
    imdf.ensure_imdf_schema()

    # The calendar kill switch and the schedule lifecycle watcher are the
    # two extensions meant to run with zero user interaction.
    kill_switch.start_watcher()
    schedule_lifecycle.start_watcher()

    print("🧩 C.O.R.E. CAMP extensions loaded: HITL, FullCalendar, Kill Switch, "
          "Digital Evidence, CAMSIS-3, Ghost Data, IoT Tools, Parts Traceability, "
          "Environmental (L7), Schedule Reminders/Lifecycle, Maintenance Documents, IMDF.")
