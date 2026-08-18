"""
License Compliance Gate (fixes "cross sign-off" report #3).

Two sign-off paths exist in this codebase:

  1. app/routes/fault_resolution.py `resolve_fault()` - already had an
     ontology-driven license check, BUT it silently disabled itself
     ("continue without compliance check") whenever the ontology failed to
     load or the ATA chapter class had no `requiresLicense` property set -
     which is exactly the situation for several chapters, so cross sign-off
     was still possible in practice.
  2. app/routes/calendar.py `sign_off_schedule()` - had NO license check at
     all. Any engineer_id could sign off any hangar check (A/B/C), which is
     the more direct instance of the "cross sign-off" problem reported.

This module is the single, deterministic fallback used by both: if the
ontology-driven check in fault_resolution.py can't produce a definitive
answer, and unconditionally for the calendar's hangar-check sign-off, this
table decides whether the signing engineer's license_type (the same value
already embedded in the "cryptographic tag" produced by
utils.create_digital_signature) authorizes the specific class of work.

Category mapping used below (documented so it's easy to adjust to your
actual regulatory requirements):
  - EASA Part-66 Category A : line maintenance / simple scheduled tasks
  - EASA Part-66 Category B1: mechanical (airframe/engine/systems) - line + base
  - EASA Part-66 Category B2: avionics/electrical/instruments - line + base
  - EASA Part-66 Category C : base maintenance certifying staff (most senior)
  - FAA A&P                 : treated as broadly equivalent to B1 mechanical
                               authority for this fleet's purposes.
"""

# Hangar schedule check-type -> license types authorized to sign it off.
CHECK_TYPE_REQUIRED_LICENSES = {
    'A-Check': {'EASA Part-66 A', 'EASA Part-66 B1', 'EASA Part-66 B2', 'EASA Part-66 C', 'FAA A&P'},
    'B-Check': {'EASA Part-66 B1', 'EASA Part-66 B2', 'EASA Part-66 C', 'FAA A&P'},
    'C-Check': {'EASA Part-66 C'},
    'AOG': {'EASA Part-66 B1', 'EASA Part-66 B2', 'EASA Part-66 C', 'FAA A&P'},
    'HIL-Test': {'EASA Part-66 A', 'EASA Part-66 B1', 'EASA Part-66 B2', 'EASA Part-66 C', 'FAA A&P'},
}

# ATA chapter -> license types authorized to sign off a fault in that chapter.
# Used as the deterministic fallback when the ontology's requiresLicense
# property isn't available (missing ontology, unmapped chapter, etc).
ATA_CHAPTER_REQUIRED_LICENSES = {
    'ATA_77': {'EASA Part-66 B1', 'FAA A&P'},   # Engine Indicating
    'ATA_72': {'EASA Part-66 B1', 'FAA A&P'},   # Engine
    'ATA_28': {'EASA Part-66 B1', 'FAA A&P'},   # Fuel Systems
    'ATA_32': {'EASA Part-66 B1', 'FAA A&P'},   # Landing Gear
    'ATA_24': {'EASA Part-66 B2', 'FAA A&P'},   # Electrical Power
    'ATA_34': {'EASA Part-66 B2'},              # Navigation/avionics
}


def check_schedule_signoff(engineer_license_type, check_type):
    """
    Returns (allowed: bool, required: set[str]) for a hangar-schedule sign-off.
    Unrecognized check types are allowed through (nothing to enforce against).
    """
    required = CHECK_TYPE_REQUIRED_LICENSES.get(check_type)
    if not required:
        return True, set()
    return (engineer_license_type in required), required


def check_fault_signoff(engineer_license_type, amm_reference, ontology_required_license=None):
    """
    Returns (allowed: bool, required: set[str]) for a CRS fault sign-off.

    If the ontology already produced a definitive required license
    (ontology_required_license not None/"None"), that takes precedence -
    this function is only the deterministic fallback/backstop.
    """
    if ontology_required_license and ontology_required_license != "None":
        return (engineer_license_type == ontology_required_license), {ontology_required_license}

    amm_chapter = (amm_reference or '').split(' ')[0]
    required = ATA_CHAPTER_REQUIRED_LICENSES.get(amm_chapter)
    if not required:
        return True, set()
    return (engineer_license_type in required), required
