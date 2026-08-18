# C.O.R.E. CAMP — Round 4: Integrated Maintenance Documentation Framework (IMDF)

Scope this round, per the head programmer's systems-analysis document
("sole.docx"): the Evidence Locker, Parts Traceability, and Maintenance
Log/CRS modules existed but operated independently. This round makes them
stages of one continuous, auditable process anchored on a Work Order - in
this codebase, a Work Order **is** the Fault row the AI ontology reasoner
creates (`app/ontology_reasoner.py: run_fleet_analysis()`), which the
dashboard already labeled "Work Order & Resolution." An upgraded CRS and a
sidebar cleanup were both explicitly requested alongside the merge.

## 1. New module: `app/camp_extensions/imdf.py` + `routes_imdf.py`

Composes the existing Evidence Locker and Parts Traceability engines
(`digital_evidence.py`, `parts_traceability.py`) rather than replacing
them - it adds no new upload/scan mechanism, it sequences the existing ones
around a Work Order.

- **`/work-orders`** - new landing page listing every AI-detected fault as
  a Work Order (number, aircraft, ATA chapter, status), linking into:
- **`/work-order/<fault_id>`** - the merged page, four tabs (same
  `.cal-tab-btn`-style pattern `calendar.html` introduced in Round 3):
  - **Authorization** (Stage 1) - WO number, aircraft, ATA chapter, AMM
    reference, detected time.
  - **Evidence Locker** (Stage 2/3/6) - the exact same upload form/hash-chain
    display as the old standalone page, pre-scoped to this fault/component,
    plus a new "Document Removed Component" form (reason, fault code,
    condition assessment, position, flight hours/cycles at removal).
  - **Parts Traceability** (Stage 4/5) - the same QR/RFID scan widgets and
    registration form as the old standalone page, pre-scoped to this
    component, now requiring a Certificate of Conformity / EASA Form 1
    reference to register a part at all.
  - **Sign-Off & CRS** (Stage 7) - a "was a component replaced?" toggle;
    if yes, a verified-replacement-part dropdown is required before the
    existing `/resolve_fault/<id>` sign-off can succeed.
- **`/work-order/<fault_id>/mark_removed`** - records the removed
  component (Stage 3) against its `PartRecords` entry.

## 2. Documentation-completeness gate (`imdf.documentation_readiness`)

Directly implements the framework doc's "the system should reject
installation if mandatory traceability information is missing":
`app/routes/fault_resolution.py: resolve_fault()` now calls this before
allowing sign-off. Evidence is **always** required for any sign-off;
a verified, EASA-Form-1-referenced replacement part is required only when
the engineer indicates an actual component swap occurred (a reset,
inspection, or software fix doesn't involve installing anything, so it
isn't held to installation-traceability rules). Verified end-to-end: a
sign-off attempt with no evidence returns `403 DOCUMENTATION INCOMPLETE`
with the exact missing item(s) and a link back to the Work Order page;
completing evidence + a real Form-1-referenced part allows it through.

## 3. CRS upgrade (Stage 7/8)

`CRS_Records` gained (additive `ALTER TABLE`, same self-healing pattern
used everywhere else in this codebase): `work_order_number`, `ata_chapter`,
`component_replaced`, `removed_part_serial`, `installed_part_serial`,
`evidence_chain_ref`. `resolve_fault()` populates all of these; `PartRecords`
gained matching removal-tracking columns (`removal_reason`,
`condition_assessment`, `fault_code`, `position_on_aircraft`,
`flight_hours_at_removal`, `flight_cycles_at_removal`, `removed_date`,
`replaced_by_serial`).

`app/camp_extensions/maintenance_documents.py`'s CRS PDF now renders a
**Component Replacement Record** section (removed part number/description/
reason side-by-side with installed part number/description/certificate)
whenever a CRS involved one, plus the Work Order number and ATA chapter in
its header. Verified by rendering an actual generated PDF to text - both
columns populate correctly with real removed/installed part data.
`templates/history.html`'s CRS register also gained a **Work Order**
column so the same reference is visible without opening the PDF.

## 4. Sidebar cleanup (duplication removed)

The standalone **Evidence Locker** (`/evidence`) and **Part Traceability**
(`/parts`) top-level sidebar links are gone - replaced by a single
**Work Order Documentation** link (`/work-orders`). Both old URLs still
work: they now redirect into `/work-orders`, same convention already used
for `/schedule/fullcalendar` and `/killswitch` in Round 3. Their
upload/register/scan API endpoints are completely unchanged - they're
exactly what the merged page's forms submit to.

The dashboard's "Work Order & Resolution" panel - the exact point where an
AI-inferred fault surfaces - now links into "Open Work Order
Documentation" instead of a bare inline mechanic-select-and-sign form,
since sign-off now happens on the merged page.

## Files touched

**New:** `app/camp_extensions/imdf.py`, `app/camp_extensions/routes_imdf.py`,
`templates/extensions/imdf_index.html`, `templates/extensions/imdf_work_order.html`.
**Edited:** `app/routes/fault_resolution.py` (documentation gate + upgraded
CRS insert + PDF generation call), `app/camp_extensions/maintenance_documents.py`
(CRS PDF layout upgrade), `app/camp_extensions/routes_evidence.py` and
`routes_parts.py` (page routes redirect into `/work-orders`; upload/register
redirects now return to the Work Order page in-context), `app/camp_extensions/__init__.py`
(+imdf registration), `templates/base.html` (sidebar: 2 links → 1),
`templates/dashboard.html` (Work Order panel links to the merged page),
`templates/history.html` (+Work Order column on the CRS register).
**Database:** additive columns on `CRS_Records` and `PartRecords` only -
`templates/extensions/digital_evidence.html` and `parts_traceability.html`
are no longer routed to directly but were left in place (unused, harmless)
in case anything still references them; every existing route/table used by
either extension is untouched.
