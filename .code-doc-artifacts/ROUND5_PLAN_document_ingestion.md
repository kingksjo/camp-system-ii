# C.O.R.E. CAMP — Document Ingestion & Auto-Population: Build Plan

Status: **Phases 0, 1, 2, 3 (architecture), 4, and 5 built and tested** - see
ROUND6_CHANGES.md for what actually shipped and how it was verified.

## Guiding principles (non-negotiable, carried from earlier discussion)

1. **Nothing auto-commits.** Every extracted row lands in a pending-review
   table first. It only becomes real `MasterMEL` / `Components` /
   `PartRecords` data after an engineer explicitly approves it, line by
   line, in the UI.
2. **Tiered parsing, matched to document structure** — not every manual
   gets the same treatment:
   - **Tier 1 (structured/tabular)** — MMEL, IPC, WDM, CMM. Table/layout
     extraction, no LLM required for well-formed PDFs.
   - **Tier 2 (regulatory feeds)** — AD, SB, ICA. Structured metadata +
     narrative body; hybrid extraction.
   - **Tier 3 (freeform/narrative)** — TSM/FIM, SSM, OEM datasheets,
     general AMM excerpts. Needs an LLM in the loop (your Anthropic API
     key — see Open Decisions).
3. **Every extracted fact keeps its receipt** — source document, page/table
   reference, and extraction method travel with the row all the way through
   review and into the final commit, so any value in `Components` or
   `MasterMEL` can be traced back to the exact manual page it came from.
4. **Build on what already exists**, don't duplicate it: `AircraftDocuments`
   (company profile uploads), `MasterMEL`, `Components`, `PartRecords`, and
   the IMDF evidence/parts engines are the landing points, not new parallel
   tables.

## Architecture overview

```
Upload (signup onboarding / company profile / work order)
        │
        ▼
Document Classifier  →  tags doc_type (MMEL / IPC / WDM / CMM / TSM / AD / SB / unknown)
        │
        ▼
Parser Router  →  Tier 1 table-extraction   OR   Tier 3 LLM-assisted extraction
        │
        ▼
PendingExtraction queue (per row: field values + confidence + source page)
        │
        ▼
Review UI  →  engineer approves / edits / rejects each row
        │
        ▼
Commit Layer  →  writes into MasterMEL / Components / PartRecords / Directives
                  (stamps source_document_id + approved_by + approved_at)
```

## Phase 0 — Schema foundations

New tables (additive, same self-healing migration pattern as every other
round):

- **`IngestedDocuments`** — one row per uploaded manual: `doc_id` (FK to
  existing `AircraftDocuments`), `doc_type`, `classification_confidence`,
  `parser_used`, `status` (`Queued` / `Parsing` / `Ready for Review` /
  `Reviewed` / `Failed`), `company_id`.
- **`PendingExtractions`** — one row per extracted fact/line item:
  `extraction_id`, `doc_id`, `target_table` (`MasterMEL` / `Components` /
  `PartRecords` / `Directives`), `field_data` (JSON of proposed values),
  `source_page`, `source_excerpt`, `confidence`, `status` (`Pending` /
  `Approved` / `Edited & Approved` / `Rejected`), `reviewed_by`,
  `reviewed_at`.
- **`ExtractionAuditLog`** — immutable log of every review decision (who,
  when, what changed if edited) — this is the "receipt" for regulatory
  audit purposes, same spirit as the CRS/evidence hash chain already built.

No changes needed to `MasterMEL`/`Components`/`PartRecords` themselves
beyond an optional `source_document_id` column on each, so a committed
record can point back to `IngestedDocuments`.

## Phase 1 — Intake

- Hook into the **existing** upload point: `AircraftDocuments` already
  gets written to from the company profile page, which is reached right
  after registration (`/register` → `/company-profile?onboarding=1`) —
  exactly the "scanner at sign-up" idea. No new upload UI needed, just a
  post-upload hook that enqueues an `IngestedDocuments` row and classifies it.
- **Classification**: filename/heading heuristics first (cheap, catches
  obvious cases like "MMEL" or "Wiring Diagram Manual" in the title), LLM
  fallback for ambiguous files. Low-confidence classifications go straight
  to a "confirm document type" step for the engineer rather than guessing.

## Phase 2 — Tier 1 structured parsers

Build in this order — MMEL first because `MasterMEL`'s columns already
mirror the standard MMEL table shape almost exactly, so it's the fastest
path to a working end-to-end pipeline others can be modeled on:

1. **MMEL** → `PendingExtractions` targeting `MasterMEL`
   (`target_model`, `ata_chapter`, `item_description`, `mmel_category`,
   `max_deferral_days`, `remarks`).
2. **IPC** → targeting `PartRecords` (part numbers, hierarchy, effectivity).
3. **WDM** → new `source_document_id` + wiring metadata fields on
   `Components` (circuit ref, connector, bus).
4. **CMM** → targeting `Components` (sensor/component test limits,
   calibration data) — this is the one that actually answers "what sensors
   exist on this component and what are their thresholds."

Each parser is a small, independent module behind a common interface
(`parse(file_path) -> list[PendingExtraction]`), so adding a 5th document
type later doesn't touch the other four.

## Phase 3 — Tier 3 LLM-assisted parsers

Same output contract as Tier 1 (rows into `PendingExtractions`), different
extraction method — a structured-output prompt against the Anthropic API
constrained to the same field schema, always with a lower default
confidence score than Tier 1 so reviewers know to look harder. Covers
TSM/FIM, SSM, OEM datasheets, and anything that fails Tier 1 table
extraction. **Blocked on your API key** — see Open Decisions.

## Phase 4 — Review UI

A new "Document Review" panel (fits naturally next to Work Order
Documentation in the sidebar) listing `IngestedDocuments` with drill-down
into their `PendingExtractions`:

- Side-by-side: proposed value + source page image/excerpt + confidence.
- Per-row Approve / Edit-then-Approve / Reject.
- Bulk actions only for genuinely low-risk fields (never for MMEL category
  or deferral limits — those always require individual sign-off, no bulk
  approve).

## Phase 5 — Commit layer

Approved rows get written into their target table in the same transaction
as their `ExtractionAuditLog` entry. Rejected rows stay in
`PendingExtractions` with `status='Rejected'` for the audit trail — nothing
gets deleted.

## Phase 6 — Revisions over time

Manuals get revised. Re-uploading a document of a type that already has
committed data doesn't overwrite silently — it **diffs** against existing
`MasterMEL`/`Components` rows sourced from the prior version of that
document and raises only the *changed* lines for review, so a routine MMEL
revision doesn't mean re-approving 300 unchanged rows.

## Testing strategy

- **Tier 1**: build/test against generated mock documents that follow real
  ATA iSpec 2200 table layout (as discussed) — validates the parser logic
  independent of sourcing real manuals.
- **Tier 3**: test against a handful of intentionally messy/inconsistent
  mock excerpts to make sure low-confidence flagging actually works, not
  just the happy path.
- Both tiers get a regression test once real sample documents are sourced.

## Open decisions needed from you before Phase 3 can start

1. **Anthropic API key** for Tier 3 parsing — your key, your billing; I
   won't build a path that uses anything else.
2. **Reviewer role** — is "any Engineer" allowed to approve extractions, or
   does this need a specific permission tier (e.g., only Admin/CAMO roles)?
3. **Scope** — per-company (`company_id`) like the rest of Round 3's
   multi-tenant groundwork, or global reference data shared across
   companies (e.g., a manufacturer's MMEL is the same for every operator
   of that type)?
4. **Storage** — where do uploaded manuals live long-term? `AircraftDocuments`
   currently just stores a `file_path`; worth confirming that's still fine
   at the volume you're expecting (a full WDM/IPC can be large).

## Suggested build order

Phase 0 → Phase 1 → **MMEL parser only** (Phase 2, step 1) → Phase 4 review
UI → Phase 5 commit layer, all working end-to-end for one document type
first. Then widen Phase 2 to IPC/WDM/CMM once that pipeline is proven, and
only take on Phase 3 (LLM tier) once the API key/role/scope questions above
are settled.
