"""
Schema for Phases 2-5 of the document ingestion pipeline
(see ROUND5_PLAN_document_ingestion.md).

Design decision carried over from that plan and worth restating here since
it drives every table below: a document's extracted data does NOT write
into the same tables that represent physical reality unless it genuinely
*is* that data. Concretely:

- MMEL data really is MasterMEL data (the schema is already shaped for it),
  so approved MMEL extractions commit straight into the existing
  `MasterMEL` table.
- IPC data is a *parts catalog* (valid part numbers/nomenclature), not a
  record of a specific physical part in the hangar - committing it into
  `PartRecords` would fabricate parts that don't actually exist. It goes
  into the new `PartsCatalog` reference table instead, which the Parts
  Traceability registration form can look up against.
- WDM/CMM/OEM datasheet data is reference/spec information about a
  component *type*, not a specific installed instance - it goes into new
  `WiringReferences` / `ComponentSpecs` reference tables.
- AD/SB/ICA slot naturally into the existing `Directives` table, which
  already exists for exactly this purpose.
- TSM/FIM and SSM data (fault-isolation logic, system schematics) goes into
  new lightweight reference tables. Wiring this into the live SWRL
  reasoner is a deliberately separate, later step - see the note in
  app/ontology_reasoner.py once that work starts.

All table definitions and additive columns live in app/migrations.py
(migration 003) - the single schema authority. This module keeps only the
routing registry.
"""

# Which reference table (and, for MMEL, the live operational table) each
# manual type's approved extractions are committed into.
TARGET_TABLE_BY_MANUAL_TYPE = {
    'MMEL': 'MasterMEL',
    'IPC': 'PartsCatalog',
    'WDM': 'WiringReferences',
    'CMM': 'ComponentSpecs',
    'AD': 'Directives',
    'SB': 'Directives',
    'ICA': 'Directives',
    'TSM_FIM': 'FaultIsolationRules',
    'SSM': 'SystemSchematics',
    'OEM_Datasheet': 'ComponentSpecs',
}


def ensure_ingestion_schema():
    """Compatibility wrapper - runs the versioned migrations (no-op once current)."""
    from app.migrations import run_migrations
    run_migrations()
